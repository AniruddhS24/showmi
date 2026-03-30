import asyncio
import json
import traceback

import uvicorn
from browser_use import Agent
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI

from agent import _make_browser
from config import config as default_config
from db import add_message, create_session, init_db
from hooks import load_skills

app = FastAPI(title="Stockholm Browser Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


def _make_step_hook(ws: WebSocket, session_id: str):
    """Create on_step_start and on_step_end hooks that stream updates via WebSocket."""

    async def on_step_start(agent) -> None:
        step = agent.state.n_steps
        model_output = agent.state.last_model_output

        goal = None
        if model_output and model_output.next_goal:
            goal = model_output.next_goal

        msg = {
            "type": "step",
            "step_number": step,
            "goal": goal,
            "phase": "start",
        }
        try:
            await ws.send_json(msg)
        except Exception:
            pass

        # Also print to console like the original hooks
        print(f"\n{'='*60}")
        print(f"Step {step}")
        print(f"{'='*60}")
        if goal:
            print(f"Goal: {goal}")

    async def on_step_end(agent) -> None:
        step = agent.state.n_steps
        results = agent.state.last_result or []
        model_output = agent.state.last_model_output

        action_names = []
        if model_output and model_output.action:
            for action in model_output.action:
                action_data = action.model_dump(exclude_none=True)
                action_names.append(action_data)

        # Print results to console (reuse original hooks logic)
        for i, r in enumerate(results):
            action_label = str(action_names[i]) if i < len(action_names) else "action"
            if r.error:
                print(f"  [error] {action_label} — {r.error}")
            elif r.is_done:
                print(f"  [done] {action_label}")
            else:
                print(f"  [ok] {action_label}")
            if r.extracted_content:
                print(f"         → {r.extracted_content[:150]}")

        # Current URL
        url = "unknown"
        try:
            url = await agent.browser_session.get_current_page_url()
        except Exception:
            pass
        print(f"URL: {url}")

        # Build actions list for the WebSocket message
        actions = []
        for i, r in enumerate(results):
            actions.append({
                "action": action_names[i] if i < len(action_names) else None,
                "error": r.error,
                "is_done": r.is_done,
                "extracted": r.extracted_content,
            })

        goal = None
        if model_output and model_output.next_goal:
            goal = model_output.next_goal

        msg = {
            "type": "step",
            "step_number": step,
            "goal": goal,
            "actions": actions,
            "url": url,
        }
        try:
            await ws.send_json(msg)
        except Exception:
            pass

        # Save step data to DB
        add_message(session_id, "assistant", json.dumps(msg), metadata=msg)

    return on_step_start, on_step_end


async def run_agent_ws(task: str, ws: WebSocket, session_id: str, settings: dict) -> None:
    """Run the browser-use agent, streaming updates over a WebSocket."""

    # Override config values from settings if provided
    cfg = default_config
    provider = settings.get("provider", "local")
    if settings.get("api_key"):
        object.__setattr__(cfg, "llm_api_key", settings["api_key"])
    if settings.get("base_url"):
        object.__setattr__(cfg, "llm_base_url", settings["base_url"])
    if settings.get("model"):
        object.__setattr__(cfg, "llm_model", settings["model"])
    if settings.get("temperature") is not None:
        object.__setattr__(cfg, "llm_temperature", float(settings["temperature"]))

    print(f"Task: {task}")
    print(f"Provider: {provider}, Model: {cfg.llm_model}")

    browser = _make_browser(cfg)

    if provider == "anthropic":
        llm = BrowserUseChatAnthropic(
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            api_key=cfg.llm_api_key,
        )
    else:
        # OpenAI-compatible (including local models)
        llm = BrowserUseChatOpenAI(
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            api_key=cfg.llm_api_key,
        )

    skills_text = load_skills()
    if skills_text:
        print(f"Loaded {skills_text.count('## Skill:')} skill(s)")

    on_step_start, on_step_end = _make_step_hook(ws, session_id)

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        extend_system_message=skills_text or None,
        max_actions_per_step=cfg.max_actions_per_step,
        max_failures=cfg.max_failures,
        use_vision=cfg.use_vision,
    )

    print("Running agent...\n")
    history = await agent.run(
        max_steps=cfg.max_steps,
        on_step_start=on_step_start,
        on_step_end=on_step_end,
    )

    # Build and send final result
    result_msg = {
        "type": "result",
        "summary": history.final_result() or "",
        "steps_taken": len(history.history),
        "errors": history.errors() or [],
    }
    await ws.send_json(result_msg)

    # Save final result to DB
    add_message(
        session_id,
        "assistant",
        result_msg["summary"],
        metadata=result_msg,
    )

    print(f"\n{'='*60}")
    print("Agent finished")
    print(f"{'='*60}")
    print(f"Steps taken: {len(history.history)}")
    if history.final_result():
        print(f"Result: {history.final_result()}")
    if history.errors():
        print(f"Errors: {history.errors()}")


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if data.get("type") != "task":
                await ws.send_json({"type": "error", "message": f"Unknown message type: {data.get('type')}"})
                continue

            content = data.get("content", "")
            settings = data.get("settings", {})
            active_tab = data.get("active_tab")

            if not content:
                await ws.send_json({"type": "error", "message": "Empty task content"})
                continue

            # Prepend active tab context if available
            task = content
            if active_tab and active_tab.get("url"):
                task = f"[User is currently on: {active_tab['url']} — \"{active_tab.get('title', '')}\"]\n\n{content}"

            # Create session and save user message
            session_id = create_session(title=content[:120])
            add_message(session_id, "user", content)

            try:
                await run_agent_ws(task, ws, session_id, settings)
            except Exception as e:
                tb = traceback.format_exc()
                print(f"Agent error: {tb}")
                error_msg = {"type": "error", "message": str(e)}
                await ws.send_json(error_msg)
                add_message(session_id, "assistant", str(e), metadata=error_msg)

    except WebSocketDisconnect:
        print("WebSocket client disconnected")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
