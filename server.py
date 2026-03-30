import asyncio
import json
import traceback

import uvicorn
from browser_use import Agent
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
from pydantic import BaseModel

from agent import _make_browser
from config import config as default_config
from db import (
    add_message,
    create_session,
    get_context_summary,
    get_identity_text,
    get_memory_text,
    get_session_messages,
    init_db,
    list_memories,
    list_sessions,
    list_models,
    get_active_model,
    save_model,
    set_active_model,
    delete_model,
    save_context_summary,
)
from hooks import load_workflows
from workflow_utils import (
    compile_recording,
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


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── REST: Sessions ──

@app.get("/api/sessions")
async def api_list_sessions():
    return list_sessions()


@app.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str):
    return get_session_messages(session_id)


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
    return {"content": get_memory_text(), "entries": list_memories()}


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


class Recording(BaseModel):
    start_url: str = ""
    events: list[RecordingEvent] = []


class CompileRequest(BaseModel):
    name: str
    description: str = ""
    auto_parameterize: bool = True
    recording: Recording


@app.get("/api/workflows")
async def api_list_workflows():
    return {"workflows": list_workflows()}


@app.post("/api/workflows/compile")
async def api_compile_workflow(payload: CompileRequest):
    # Resolve LLM settings from active model
    settings = {}
    active = get_active_model()
    if active:
        settings = {
            "provider": active["provider"],
            "model": active["model"],
            "base_url": active["base_url"],
            "api_key": active["api_key"],
            "temperature": active["temperature"],
        }

    if not settings.get("api_key"):
        return JSONResponse(
            status_code=400,
            content={"error": "No active model configured. Add a model first."},
        )

    try:
        result = await compile_recording(
            recording=payload.recording.model_dump(),
            name=payload.name,
            description=payload.description,
            auto_parameterize=payload.auto_parameterize,
            llm_settings=settings,
        )
        return {"workflow": result}
    except ValueError as e:
        # LLM output couldn't be parsed — return raw output for manual editing
        return JSONResponse(
            status_code=422,
            content={
                "error": "Could not parse LLM output into workflow format",
                "raw_output": str(e),
            },
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Compilation failed: {str(e)}"},
        )


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


def _build_system_message() -> str | None:
    """Combine identity, memory, and workflows into a single system message extension."""
    parts = []
    identity = get_identity_text()
    if identity:
        parts.append(identity)
    memory = get_memory_text()
    if memory:
        parts.append(memory)
    workflows = load_workflows()
    if workflows:
        parts.append(workflows)
    return "\n\n---\n\n".join(parts) if parts else None


async def run_agent_ws(task: str, ws: WebSocket, session_id: str, settings: dict) -> None:
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
        llm = BrowserUseChatOpenAI(
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            api_key=cfg.llm_api_key,
        )

    system_message = _build_system_message()
    if system_message:
        workflow_count = system_message.count("## Workflow:")
        if workflow_count:
            print(f"Loaded {workflow_count} workflow(s)")

    on_step_start, on_step_end = _make_step_hook(ws, session_id)

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        extend_system_message=system_message,
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

    # Final context compression at session end
    compress_chat_context(session_id, len(history.history))

    # Build and send final result
    result_msg = {
        "type": "result",
        "summary": history.final_result() or "",
        "steps_taken": len(history.history),
        "errors": history.errors() or [],
    }
    await ws.send_json(result_msg)

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

            # If no settings provided, use the active model from DB
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

            task = content
            if active_tab and active_tab.get("url"):
                task = f"[User is currently on: {active_tab['url']} — \"{active_tab.get('title', '')}\"]\n\n{content}"

            session_id = create_session(title=content[:120])
            add_message(session_id, "user", content)

            # Send session_id back so frontend can track it
            await ws.send_json({"type": "session", "session_id": session_id})

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
