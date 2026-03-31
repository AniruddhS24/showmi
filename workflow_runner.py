"""Hybrid workflow executor — runs Python scripts that mix Playwright + browser-use."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

from db import WORKFLOWS_DIR


@dataclass
class WorkflowResult:
    success: bool = False
    return_value: str = ""
    error: str = ""
    traceback_str: str = ""
    screenshot_on_error: str = ""  # base64 JPEG
    duration_ms: int = 0


class AgentHelper:
    """LLM-powered browser actions available inside workflow scripts."""

    def __init__(self, pw_page, llm, browser_session=None, ws=None, session_id=None):
        self._page = pw_page
        self._llm = llm
        self._browser_session = browser_session
        self._ws = ws
        self._session_id = session_id

    async def extract(self, query: str, schema: dict | None = None) -> dict | list:
        """Single LLM call to extract structured data from the current page.

        Returns parsed JSON (dict or list).
        """
        from browser_use import Agent, BrowserSession

        # Build a focused extraction task
        schema_hint = ""
        if schema:
            schema_hint = f"\nReturn JSON matching this schema: {json.dumps(schema)}"

        task = (
            f"Extract the following from the current page and return ONLY valid JSON "
            f"(no markdown, no explanation):\n{query}{schema_hint}"
        )

        agent = Agent(
            task=task,
            llm=self._llm,
            browser=self._browser_session,
            flash_mode=True,
            use_vision=False,
            max_actions_per_step=1,
        )
        history = await agent.run(max_steps=3)

        # Parse the result
        result_text = history.final_result() or ""
        # Strip markdown code fences if present
        result_text = result_text.strip()
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            result_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            return {"raw": result_text}

    async def do(self, instruction: str, max_steps: int = 5) -> str:
        """Run a short browser-use Agent for a focused interactive task.

        Uses flash_mode for speed. Returns the agent's final result text.
        """
        from browser_use import Agent

        agent = Agent(
            task=instruction,
            llm=self._llm,
            browser=self._browser_session,
            flash_mode=True,
            use_vision=False,
            max_actions_per_step=4,
        )
        history = await agent.run(max_steps=max_steps)
        return history.final_result() or ""


def _make_llm(settings: dict):
    """Create the LLM instance from active model settings."""
    provider = settings.get("provider", "local")
    if provider == "anthropic":
        from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
        return BrowserUseChatAnthropic(
            model=settings.get("model", "claude-sonnet-4-20250514"),
            temperature=settings.get("temperature", 0.6),
            api_key=settings.get("api_key", ""),
        )
    else:
        from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
        return BrowserUseChatOpenAI(
            base_url=settings.get("base_url", "http://localhost:8000/v1"),
            model=settings.get("model", "default"),
            temperature=settings.get("temperature", 0.6),
            api_key=settings.get("api_key", "not-needed"),
        )


async def run_workflow(
    slug_or_code: str,
    params: dict,
    browser=None,
    settings: dict | None = None,
    ws=None,
    session_id: str = "",
    is_test: bool = False,
    timeout_ms: int = 300_000,
) -> WorkflowResult:
    """Run a workflow script.

    Args:
        slug_or_code: If is_test=True, raw Python source code. Otherwise, workflow slug.
        params: Parameter values dict.
        browser: browser-use Browser instance (reused for CDP connection).
        settings: Active model settings dict (provider, model, api_key, etc.).
        ws: WebSocket for progress updates (optional).
        session_id: Session ID for progress updates.
        is_test: If True, slug_or_code is raw Python source to test.
        timeout_ms: Timeout in milliseconds.
    """
    settings = settings or {}
    start = time.monotonic()
    result = WorkflowResult()
    pw = None
    pw_browser = None
    page = None

    try:
        # 1. Get or start the browser-use Browser to obtain a CDP URL
        if browser is None:
            from agent import _make_browser
            browser = _make_browser()

        # Start browser if needed — populates cdp_url
        if not browser.cdp_url:
            await browser.start()
        cdp_url = browser.cdp_url
        if not cdp_url:
            raise RuntimeError("Could not obtain CDP URL from browser-use Browser")

        # 2. Connect Playwright to the same Chrome via CDP
        pw = await async_playwright().start()
        pw_browser = await pw.chromium.connect_over_cdp(cdp_url)

        # Get the default context (user's logged-in session)
        contexts = pw_browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = await pw_browser.new_context()

        # Open a new page for the workflow
        page = await context.new_page()

        # 3. Create AgentHelper with the browser-use Browser
        llm = _make_llm(settings)
        agent_helper = AgentHelper(
            pw_page=page,
            llm=llm,
            browser_session=browser,
            ws=ws,
            session_id=session_id,
        )

        # 4. Load the workflow script
        if is_test:
            # Write source to a temp file and import it
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(slug_or_code)
                tmp_path = f.name

            spec = importlib.util.spec_from_file_location("_workflow_test", tmp_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["_workflow_test"] = module
            spec.loader.exec_module(module)
        else:
            # Load from ~/.showmi/workflows/{slug}/workflow.py
            script_path = WORKFLOWS_DIR / slug_or_code / "workflow.py"
            if not script_path.exists():
                raise FileNotFoundError(f"Workflow script not found: {script_path}")

            module_name = f"_workflow_{slug_or_code.replace('-', '_')}"
            # Clear cached module to pick up changes
            sys.modules.pop(module_name, None)
            spec = importlib.util.spec_from_file_location(module_name, str(script_path))
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

        if not hasattr(module, "run"):
            raise AttributeError("Workflow script must define an `async def run(page, params, agent)` function")

        # 5. Execute the workflow with timeout
        if ws and session_id:
            await ws.send_json({"type": "workflow_progress", "session_id": session_id, "message": "Workflow started"})

        ret = await asyncio.wait_for(
            module.run(page, params, agent_helper),
            timeout=timeout_ms / 1000,
        )

        result.success = True
        result.return_value = str(ret) if ret else "Workflow completed successfully."

    except asyncio.TimeoutError:
        result.error = f"Workflow timed out after {timeout_ms / 1000:.0f}s"
        result.traceback_str = ""
    except Exception as e:
        result.error = str(e)
        result.traceback_str = traceback.format_exc()
        # Try to capture screenshot on error
        try:
            if page:
                screenshot_bytes = await page.screenshot(type="jpeg", quality=70)
                result.screenshot_on_error = base64.b64encode(screenshot_bytes).decode()
        except Exception:
            pass

    finally:
        result.duration_ms = int((time.monotonic() - start) * 1000)
        # Close only the Playwright page/connection, not Chrome itself
        try:
            if page and not page.is_closed():
                await page.close()
        except Exception:
            pass
        try:
            if pw_browser:
                await pw_browser.close()
        except Exception:
            pass
        try:
            if pw:
                await pw.stop()
        except Exception:
            pass

    return result
