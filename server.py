import asyncio
import json
import traceback

import uvicorn
import yaml
from browser_use import Agent
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
from browser_use.llm.views import ChatInvokeCompletion
from pydantic import BaseModel, ValidationError as PydanticValidationError


class _RobustChatOpenAI(BrowserUseChatOpenAI):
    """BrowserUseChatOpenAI that tolerates trailing whitespace/newlines in JSON responses.

    Local and proxy models often append a trailing newline after the JSON object,
    which causes Pydantic's model_validate_json to raise "trailing characters".
    This subclass retries by stripping the raw content and re-validating.
    """

    async def ainvoke(self, messages, output_format=None, **kwargs):
        try:
            return await super().ainvoke(messages, output_format, **kwargs)
        except PydanticValidationError as exc:
            if output_format is None or "trailing" not in str(exc).lower():
                raise
            # Get raw string response (no structured output enforcement) and strip it
            raw = await super().ainvoke(messages, None, **kwargs)
            try:
                parsed = output_format.model_validate_json(raw.completion.strip())
                return ChatInvokeCompletion(
                    completion=parsed, usage=raw.usage, stop_reason=raw.stop_reason
                )
            except Exception:
                raise exc

from agent import _make_browser
from config import config as default_config
from planning import run_planning_agent
from db import (
    add_message,
    create_session,
    get_context_summary,
    get_identity_text,
    get_session_messages,
    init_db,
    list_memories,
    list_sessions,
    list_models,
    get_active_model,
    save_model,
    set_active_model,
    delete_model,
    delete_session,
    save_context_summary,
    update_session_status,
    update_session_title,
)
from workflow_utils import (
    delete_workflow,
    get_workflow,
    list_workflows,
    parse_frontmatter,
    save_workflow,
    slugify,
)

app = FastAPI(title="Showmi Browser Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Log streaming ──
# Tail the persistent log file at ~/.showmi/logs/server.log for the /ws/logs endpoint.

import re as _re

_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket):
    """Tail ~/.showmi/logs/server.log and stream to the client."""
    from db import LOGS_DIR

    await ws.accept()
    log_path = LOGS_DIR / "server.log"

    try:
        if not log_path.exists():
            await ws.send_text("(no log file yet — start server with `showmi start`)")
            # Wait for file to appear
            while not log_path.exists():
                await asyncio.sleep(1)

        with open(log_path, "r") as f:
            # Send last 200 lines as backlog
            lines = f.readlines()
            for line in lines[-200:]:
                clean = _ANSI_RE.sub("", line.rstrip())
                if clean:
                    await ws.send_text(clean)

            # Tail new lines
            while True:
                line = f.readline()
                if line:
                    clean = _ANSI_RE.sub("", line.rstrip())
                    if clean:
                        await ws.send_text(clean)
                else:
                    await asyncio.sleep(0.3)
    except Exception:
        pass


# ── REST: Sessions ──

@app.get("/api/sessions")
async def api_list_sessions():
    return list_sessions()


@app.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str):
    return get_session_messages(session_id)


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    delete_session(session_id)
    return {"ok": True}


# ── REST: Models ──

class ModelPayload(BaseModel):
    id: str | None = None
    name: str = ""
    provider: str = "anthropic"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.5


@app.get("/api/models")
async def api_list_models():
    models = list_models()
    # Mask API keys in response (show last 4 chars only)
    for m in models:
        key = m.get("api_key", "")
        m["api_key_preview"] = ("..." + key[-4:]) if len(key) > 4 else ("*" * len(key))
        del m["api_key"]
    return models


@app.get("/api/models/active")
async def api_active_model():
    m = get_active_model()
    if not m:
        return JSONResponse(status_code=404, content={"error": "No active model"})
    key = m.get("api_key", "")
    m["api_key_preview"] = ("..." + key[-4:]) if len(key) > 4 else ("*" * len(key))
    del m["api_key"]
    return m


@app.post("/api/models")
async def api_save_model(payload: ModelPayload):
    data = payload.model_dump()
    result = save_model(data)
    # If this is the only model, make it active
    all_models = list_models()
    if len(all_models) == 1:
        set_active_model(result["id"])
    return {"ok": True, "id": result["id"]}


@app.put("/api/models/{model_id}")
async def api_update_model(model_id: str, payload: ModelPayload):
    data = payload.model_dump()
    data["id"] = model_id
    # If api_key is empty, preserve the existing one
    if not data["api_key"]:
        existing = list_models()
        for m in existing:
            if m["id"] == model_id:
                data["api_key"] = m["api_key"]
                break
    save_model(data)
    return {"ok": True}


@app.put("/api/models/{model_id}/activate")
async def api_activate_model(model_id: str):
    set_active_model(model_id)
    return {"ok": True}


@app.delete("/api/models/{model_id}")
async def api_delete_model(model_id: str):
    delete_model(model_id)
    return {"ok": True}


# ── REST: Identity, Memory, Workflows ──


@app.get("/identity")
async def get_identity():
    return {"content": get_identity_text()}


@app.get("/memory")
async def get_memory():
    return {"entries": list_memories()}


# ── REST: Workflow CRUD & Compilation ──


class WorkflowParameter(BaseModel):
    name: str
    description: str = ""
    default: str = ""


class WorkflowCreate(BaseModel):
    name: str = ""
    description: str = ""
    parameters: list[WorkflowParameter] = []
    body: str = ""
    file_content: str = ""


class RecordingEvent(BaseModel):
    type: str  # click, input, navigation, select, keypress, scroll
    timestamp: str = ""
    url: str = ""
    page_title: str = ""
    target: dict = {}
    value: str = ""
    dom_context: str = ""  # filtered DOM elements around the interaction
    screenshot: str = ""  # base64 JPEG data URL


class Recording(BaseModel):
    start_url: str = ""
    events: list[RecordingEvent] = []
    audio_b64: str = ""  # base64 webm audio from microphone


@app.get("/api/workflows")
async def api_list_workflows():
    return {"workflows": list_workflows()}


@app.get("/api/workflows/{workflow_id}")
async def api_get_workflow(workflow_id: str):
    wf = get_workflow(workflow_id)
    if not wf:
        return JSONResponse(status_code=404, content={"error": "Workflow not found"})
    return wf


@app.post("/api/workflows")
async def api_create_workflow(payload: WorkflowCreate):
    data = payload.model_dump()
    # Determine slug to check for conflicts
    if data.get("file_content"):
        meta, _ = parse_frontmatter(data["file_content"])
        slug = slugify(meta.get("name", "untitled"))
    elif data.get("name"):
        slug = slugify(data["name"])
    else:
        return JSONResponse(
            status_code=400, content={"error": "name or file_content required"}
        )

    if get_workflow(slug):
        return JSONResponse(
            status_code=409,
            content={"error": f"Workflow '{slug}' already exists"},
        )

    wf_id = save_workflow(data)
    return {"ok": True, "id": wf_id}


@app.put("/api/workflows/{workflow_id}")
async def api_update_workflow(workflow_id: str, payload: WorkflowCreate):
    if not get_workflow(workflow_id):
        return JSONResponse(status_code=404, content={"error": "Workflow not found"})
    data = payload.model_dump()
    save_workflow(data, workflow_id=workflow_id)
    return {"ok": True, "id": workflow_id}


@app.delete("/api/workflows/{workflow_id}")
async def api_delete_workflow(workflow_id: str):
    if not delete_workflow(workflow_id):
        return JSONResponse(status_code=404, content={"error": "Workflow not found"})
    return {"ok": True}


@app.get("/chats/{session_id}/context")
async def get_chat_context(session_id: str):
    summary = get_context_summary(session_id)
    if summary is None:
        return {"content": None, "message": "No context summary available"}
    return {"content": summary}


# ── Context compression ──

COMPRESSION_INTERVAL = 10


def compress_chat_context(session_id: str, step_number: int) -> None:
    """Build a heuristic context summary from recent messages and save to context.md."""
    messages = get_session_messages(session_id)
    if not messages:
        return

    summary_lines = [
        f"# Chat Context Summary\n",
        f"Session: {session_id}",
        f"Steps completed: {step_number}\n",
        "## Recent Activity\n",
    ]

    # Summarize last 10 messages
    for msg in messages[-10:]:
        role = msg["role"]
        content = (msg["content"] or "")[:200]
        summary_lines.append(f"- **{role}**: {content}")

    save_context_summary(session_id, "\n".join(summary_lines) + "\n")


# ── WebSocket step hooks ──


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
            "session_id": session_id,
            "step_number": step,
            "goal": goal,
            "phase": "start",
        }
        try:
            await ws.send_json(msg)
        except Exception:
            pass

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

        url = "unknown"
        try:
            url = await agent.browser_session.get_current_page_url()
        except Exception:
            pass
        print(f"URL: {url}")

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
            "session_id": session_id,
            "step_number": step,
            "goal": goal,
            "actions": actions,
            "url": url,
        }
        try:
            await ws.send_json(msg)
        except Exception:
            pass

        add_message(session_id, "assistant", json.dumps(msg), metadata=msg)

        # Context compression every N steps
        if step > 0 and step % COMPRESSION_INTERVAL == 0:
            compress_chat_context(session_id, step)

    return on_step_start, on_step_end




async def run_agent_ws(task: str, ws: WebSocket, session_id: str, settings: dict, agent_overrides: dict | None = None) -> None:
    """Run the browser-use agent, streaming updates over a WebSocket."""

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
        llm = _RobustChatOpenAI(
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            api_key=cfg.llm_api_key,
        )

    on_step_start, on_step_end = _make_step_hook(ws, session_id)

    from config import _parse_use_vision

    agent_kwargs = {
        "task": task,
        "llm": llm,
        "browser": browser,
        "max_actions_per_step": cfg.max_actions_per_step,
        "max_failures": cfg.max_failures,
        "use_vision": _parse_use_vision(cfg.use_vision),
        "flash_mode": cfg.flash_mode,
        "use_thinking": cfg.use_thinking,
        "vision_detail_level": cfg.vision_detail_level,
        "max_history_items": cfg.max_history_items or None,
    }
    if provider != "anthropic":
        agent_kwargs["page_extraction_llm"] = BrowserUseChatOpenAI(
            base_url=cfg.llm_base_url,
            model="gpt-4o-mini",
            temperature=0.0,
            api_key=cfg.llm_api_key,
        )
    if agent_overrides:
        agent_kwargs.update(agent_overrides)
    agent = Agent(**agent_kwargs)

    print("Running agent...\n")
    update_session_status(session_id, "running")

    history = await agent.run(
        max_steps=cfg.max_steps,
        on_step_start=on_step_start,
        on_step_end=on_step_end,
    )

    # Final context compression at session end
    compress_chat_context(session_id, len(history.history))

    # Collect error strings, filtering out None/empty
    raw_errors = history.errors() or []
    error_strings = [str(e) for e in raw_errors if e is not None and str(e).strip().lower() not in ("", "none")]

    # Build and send final result
    result_msg = {
        "type": "result",
        "session_id": session_id,
        "summary": history.final_result() or "",
        "steps_taken": len(history.history),
        "errors": error_strings,
    }
    await ws.send_json(result_msg)

    add_message(
        session_id,
        "assistant",
        result_msg["summary"],
        metadata=result_msg,
    )

    # Mark completed — the agent finished successfully even if some steps had errors
    update_session_status(session_id, "completed")

    result_summary = result_msg["summary"] or f"Completed in {len(history.history)} steps."

    print(f"\n{'='*60}")
    print("Agent finished")
    print(f"{'='*60}")
    print(f"Steps taken: {len(history.history)}")
    if history.final_result():
        print(f"Result: {history.final_result()}")
    if error_strings:
        print(f"Errors: {error_strings}")


    return result_summary


# ── Track running tasks per WebSocket ──
_running_tasks: dict[str, asyncio.Task] = {}
_planning_queues: dict[str, asyncio.Queue] = {}
_orchestrator_queues: dict[str, asyncio.Queue] = {}


def _generate_title(content: str) -> str:
    """Generate a concise title from the user's message."""
    # Simple heuristic: take first sentence, truncate to 60 chars
    text = content.strip().split("\n")[0]
    # Remove common prefixes
    for prefix in ["can you ", "could you ", "please ", "i want to ", "i need to "]:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    # Capitalize first letter
    if text:
        text = text[0].upper() + text[1:]
    # Truncate
    if len(text) > 60:
        text = text[:57] + "..."
    return text or "New chat"


def _resolve_settings(data_settings: dict | None = None) -> dict:
    """Resolve LLM settings from provided data or active model in DB."""
    settings = data_settings or {}
    if not settings.get("api_key"):
        active = get_active_model()
        if active:
            settings = {
                "provider": active["provider"],
                "model": active["model"],
                "base_url": active["base_url"],
                "api_key": active["api_key"],
                "temperature": active["temperature"],
            }
    return settings


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

            msg_type = data.get("type")

            # ── Planning: user response to agent question ──
            if msg_type == "planning_response":
                sid = data.get("session_id", "")
                content = data.get("content", "")
                if sid and content:
                    add_message(sid, "user", content)
                q = _planning_queues.get(sid)
                if q:
                    await q.put(content)
                continue

            # ── Planning: approve / reject / test ──
            if msg_type == "planning_action":
                sid = data.get("session_id", "")
                action = data.get("action", "")
                q = _planning_queues.get(sid)

                if action == "approve":
                    if q:
                        await q.put({"type": "approve"})
                    # Frontend handles its own UI (exitPlanningMode + status message)
                    # so we don't send planning_complete here to avoid a spurious
                    # "Workflow discarded." message from the generic handler.

                elif action == "reject":
                    if q:
                        await q.put({"type": "reject"})
                    # Don't cancel the orchestrator — let planning agent return
                    # and the orchestrator will continue its conversation loop
                    await ws.send_json({
                        "type": "planning_complete",
                        "session_id": sid,
                        "workflow_id": None,
                    })

                elif action == "test" and q:
                    workflow_markdown = getattr(q, "_last_proposed_markdown", "")
                    manifest_yaml = getattr(q, "_last_proposed_manifest", "")
                    if not workflow_markdown:
                        await ws.send_json({
                            "type": "planning_error",
                            "session_id": sid,
                            "message": "No workflow to test. Generate a workflow first.",
                        })
                        continue

                    manifest = yaml.safe_load(manifest_yaml) or {}

                    # Substitute default param values into markdown
                    task = workflow_markdown
                    for p in manifest.get("parameters", []):
                        pname = p.get("name", "")
                        default = p.get("default", "")
                        if pname and default:
                            task = task.replace(f"{{{{{pname}}}}}", default)

                    await ws.send_json({"type": "test_start", "session_id": sid})

                    try:
                        settings = _resolve_settings(data.get("settings"))
                        # Run via browser-use agent with DOM (no screenshots)
                        result = await run_agent_ws(
                            task, ws, sid, settings,
                            agent_overrides={
                                "flash_mode": True,
                                "use_thinking": False,
                                "use_vision": "auto",
                            },
                        )

                        await ws.send_json({
                            "type": "test_result",
                            "session_id": sid,
                            "success": True,
                            "return_value": result or "Workflow completed.",
                            "error": "",
                            "traceback": "",
                        })

                        # Feed test result back to planning agent
                        await q.put({"type": "test_result", "success": True, "result_text": result or ""})

                    except Exception as e:
                        tb = traceback.format_exc()
                        await ws.send_json({
                            "type": "test_result",
                            "session_id": sid,
                            "success": False,
                            "error": str(e),
                            "traceback": tb,
                        })
                        await q.put({"type": "test_result", "success": False, "error": str(e), "traceback": tb})
                continue

            # ── Recording complete: feed into orchestrator queue ──
            if msg_type == "recording_complete":
                sid = data.get("session_id", "")
                q = _orchestrator_queues.get(sid)
                if q:
                    await q.put({"type": "recording", "data": data.get("recording", {})})
                continue

            # ── User message: route to orchestrator ──
            if msg_type == "message":
                from orchestrator import run_orchestrator

                content = data.get("content", "")
                settings = _resolve_settings(data.get("settings"))
                active_tab = data.get("active_tab")
                client_session_id = data.get("session_id")

                if not content:
                    await ws.send_json({"type": "error", "message": "Empty message"})
                    continue

                if not settings.get("api_key"):
                    await ws.send_json({"type": "error", "message": "No active model configured."})
                    continue

                # Reuse existing session or create new one
                if client_session_id:
                    session_id = client_session_id
                else:
                    title = _generate_title(content)
                    session_id = create_session(title=title)

                add_message(session_id, "user", content)

                # Prepend active tab context if present
                user_content = content
                if active_tab and active_tab.get("url"):
                    user_content = f"[User is currently on: {active_tab['url']} — \"{active_tab.get('title', '')}\"]\n\n{content}"

                await ws.send_json({
                    "type": "session",
                    "session_id": session_id,
                    "title": _generate_title(content) if not client_session_id else None,
                })

                # Get or create orchestrator for this session
                q = _orchestrator_queues.get(session_id)
                existing_task = _running_tasks.get(session_id)

                if q and existing_task and not existing_task.done():
                    # Orchestrator already running — feed message into its queue
                    await q.put(user_content)
                else:
                    # Start new orchestrator
                    q = asyncio.Queue()
                    _orchestrator_queues[session_id] = q
                    await q.put(user_content)

                    async def _run_orch(sid, s, queue):
                        try:
                            await run_orchestrator(ws, sid, s, queue, _planning_queues)
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            tb_str = traceback.format_exc()
                            print(f"Orchestrator error: {tb_str}")
                            try:
                                await ws.send_json(
                                    {"type": "error", "session_id": sid, "message": str(e)}
                                )
                            except Exception:
                                pass
                        finally:
                            _running_tasks.pop(sid, None)
                            _orchestrator_queues.pop(sid, None)

                    task_handle = asyncio.create_task(_run_orch(session_id, settings, q))
                    _running_tasks[session_id] = task_handle
                continue

            # ── Cancel running task ──
            if msg_type == "cancel":
                sid = data.get("session_id", "")
                task_handle = _running_tasks.pop(sid, None)
                if task_handle:
                    task_handle.cancel()
                _planning_queues.pop(sid, None)
                _orchestrator_queues.pop(sid, None)
                await ws.send_json({
                    "type": "cancelled",
                    "session_id": sid,
                })
                continue

            # Unknown message type
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        print("WebSocket client disconnected")
        # Cancel running tasks for this connection
        for task_handle in _running_tasks.values():
            task_handle.cancel()
        _running_tasks.clear()
        _orchestrator_queues.clear()
        _planning_queues.clear()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
