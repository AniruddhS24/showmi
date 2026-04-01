import asyncio
import json
import traceback

import uvicorn
import yaml
from browser_use import Agent
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import FileResponse, StreamingResponse
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
from pydantic import BaseModel


class _RobustChatOpenAI(BrowserUseChatOpenAI):
    """OpenAI-compatible client that handles local models appending trailing whitespace to JSON.

    browser-use uses Pydantic's model_validate_json (strict jiter parser) which rejects
    even a trailing newline. Python's json.loads handles this fine, so we fall back to it.
    """

    async def ainvoke(self, messages, output_format=None, **kwargs):
        if output_format is None:
            return await super().ainvoke(messages, output_format, **kwargs)

        orig = output_format.model_validate_json

        def _tolerant_validate(json_data, *a, **kw):
            try:
                return orig(json_data, *a, **kw)
            except Exception:
                return output_format.model_validate(json.loads(json_data))

        output_format.model_validate_json = _tolerant_validate
        try:
            return await super().ainvoke(messages, output_format, **kwargs)
        finally:
            output_format.model_validate_json = orig

from agent import _make_browser
from config import DEFAULT_EXTRACTION_MODEL, config as default_config
from planning import run_planning_agent
from db import (
    add_message,
    create_session,
    CHATS_DIR,
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


def _mask_api_key(model_dict: dict) -> dict:
    """Replace api_key with a masked preview in a model dict."""
    key = model_dict.pop("api_key", "")
    model_dict["api_key_preview"] = ("..." + key[-4:]) if len(key) > 4 else ("*" * len(key))
    return model_dict


@app.get("/api/models")
async def api_list_models():
    return [_mask_api_key(m) for m in list_models()]


@app.get("/api/models/active")
async def api_active_model():
    m = get_active_model()
    if not m:
        return JSONResponse(status_code=404, content={"error": "No active model"})
    return _mask_api_key(m)


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


@app.delete("/memory/{memory_id}")
async def remove_memory(memory_id: int):
    from db import delete_memory
    delete_memory(memory_id)
    return {"ok": True}


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


# ── Step hooks (push to SSE event bus) ──


def _make_step_hook(event_bus: asyncio.Queue, session_id: str):
    """Create on_step_start and on_step_end hooks that push events to the SSE bus."""

    async def on_step_start(agent) -> None:
        step = agent.state.n_steps
        model_output = agent.state.last_model_output
        goal = model_output.next_goal if model_output and model_output.next_goal else None

        await event_bus.put({
            "type": "step", "session_id": session_id,
            "step_number": step, "goal": goal, "phase": "start",
        })
        print(f"\n{'='*60}\nStep {step}\n{'='*60}")
        if goal:
            print(f"Goal: {goal}")

    async def on_step_end(agent) -> None:
        step = agent.state.n_steps
        results = agent.state.last_result or []
        model_output = agent.state.last_model_output

        action_names = []
        if model_output and model_output.action:
            for action in model_output.action:
                action_names.append(action.model_dump(exclude_none=True))

        for i, r in enumerate(results):
            label = str(action_names[i]) if i < len(action_names) else "action"
            if r.error:
                print(f"  [error] {label} — {r.error}")
            elif r.is_done:
                print(f"  [done] {label}")
            else:
                print(f"  [ok] {label}")
            if r.extracted_content:
                print(f"         → {r.extracted_content[:150]}")

        url = "unknown"
        try:
            url = await agent.browser_session.get_current_page_url()
        except Exception:
            pass
        print(f"URL: {url}")

        actions = [{
            "action": action_names[i] if i < len(action_names) else None,
            "error": r.error, "is_done": r.is_done, "extracted": r.extracted_content,
        } for i, r in enumerate(results)]

        goal = model_output.next_goal if model_output and model_output.next_goal else None
        msg = {
            "type": "step", "session_id": session_id, "step_number": step,
            "goal": goal, "actions": actions, "url": url,
        }
        await event_bus.put(msg)
        add_message(session_id, "assistant", json.dumps(msg), metadata=msg)

        if step > 0 and step % COMPRESSION_INTERVAL == 0:
            compress_chat_context(session_id, step)

    return on_step_start, on_step_end




SPEED_PROMPT = """\
Speed optimization instructions:
- Be direct — get to the goal as quickly as possible
- Use multi-action sequences to reduce steps
- Don't over-verify — trust that actions succeeded unless you see an error
- Skip unnecessary scrolling or waiting"""


async def run_agent(task: str, event_bus: asyncio.Queue, session_id: str, settings: dict, agent_overrides: dict | None = None) -> str:
    """Run the browser-use agent, streaming step events to the SSE bus."""

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

    print(f"Task: {task}\nProvider: {provider}, Model: {cfg.llm_model}")

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

    on_step_start, on_step_end = _make_step_hook(event_bus, session_id)

    from config import _parse_use_vision
    from pathlib import Path

    gif_dir = CHATS_DIR / session_id
    gif_dir.mkdir(parents=True, exist_ok=True)
    gif_path = str(gif_dir / "replay.gif")

    agent_kwargs = {
        "task": task, "llm": llm, "browser": browser,
        "max_actions_per_step": cfg.max_actions_per_step,
        "max_failures": cfg.max_failures,
        "use_vision": _parse_use_vision(cfg.use_vision),
        "flash_mode": cfg.flash_mode, "use_thinking": cfg.use_thinking,
        "vision_detail_level": cfg.vision_detail_level,
        "max_history_items": cfg.max_history_items or None,
        "extend_system_message": SPEED_PROMPT,
        "generate_gif": gif_path,
    }
    if provider != "anthropic":
        agent_kwargs["page_extraction_llm"] = BrowserUseChatOpenAI(
            base_url=cfg.llm_base_url, model=DEFAULT_EXTRACTION_MODEL,
            temperature=0.0, api_key=cfg.llm_api_key,
        )
    if agent_overrides:
        agent_kwargs.update(agent_overrides)

    agent = Agent(**agent_kwargs)
    print("Running agent...\n")
    update_session_status(session_id, "running")

    start_url = settings.get("start_url")
    if start_url:
        try:
            browser_context = await browser.get_browser_context()
            pages = browser_context.pages
            page = pages[0] if pages else await browser_context.new_page()
            await page.goto(start_url, wait_until="domcontentloaded", timeout=10000)
        except Exception as e:
            print(f"Failed to navigate to start URL: {e}")

    history = await agent.run(
        max_steps=cfg.max_steps,
        on_step_start=on_step_start,
        on_step_end=on_step_end,
    )

    compress_chat_context(session_id, len(history.history))

    raw_errors = history.errors() or []
    error_strings = [str(e) for e in raw_errors if e is not None and str(e).strip().lower() not in ("", "none")]

    gif_available = Path(gif_path).exists()
    result_msg = {
        "type": "result", "session_id": session_id,
        "summary": history.final_result() or "",
        "steps_taken": len(history.history), "errors": error_strings,
    }
    if gif_available:
        result_msg["gif_url"] = f"/api/sessions/{session_id}/replay.gif"
    await event_bus.put(result_msg)
    add_message(session_id, "assistant", result_msg["summary"], metadata=result_msg)
    update_session_status(session_id, "completed")

    result_summary = result_msg["summary"] or f"Completed in {len(history.history)} steps."
    print(f"\n{'='*60}\nAgent finished — {len(history.history)} steps\n{'='*60}")
    return result_summary


# ── Session runtime (replaces WebSocket state) ──


class SessionRuntime:
    """Runtime state for a session with a running orchestrator."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.events: asyncio.Queue = asyncio.Queue()  # SSE reads from here
        self.orch_queue: asyncio.Queue = asyncio.Queue()  # user messages → orchestrator
        self.planning_queue: asyncio.Queue | None = None  # planning responses
        self.task: asyncio.Task | None = None


_runtimes: dict[str, SessionRuntime] = {}


def _get_runtime(session_id: str) -> SessionRuntime:
    if session_id not in _runtimes:
        _runtimes[session_id] = SessionRuntime(session_id)
    return _runtimes[session_id]


def _generate_title(content: str) -> str:
    text = content.strip().split("\n")[0]
    for prefix in ["can you ", "could you ", "please ", "i want to ", "i need to "]:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    if text:
        text = text[0].upper() + text[1:]
    if len(text) > 60:
        text = text[:57] + "..."
    return text or "New chat"


def _resolve_settings() -> dict:
    active = get_active_model()
    if active:
        return {
            "provider": active["provider"],
            "model": active["model"],
            "base_url": active["base_url"],
            "api_key": active["api_key"],
            "temperature": active["temperature"],
        }
    return {}


# ── SSE: event stream per session ──


@app.get("/api/sessions/{session_id}/events")
async def session_events(session_id: str):
    """Server-Sent Events stream for a session. Replaces the WebSocket."""
    rt = _get_runtime(session_id)

    async def generate():
        try:
            while True:
                event = await rt.events.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── REST: Chat (send message / start orchestrator) ──


class ChatRequest(BaseModel):
    content: str
    session_id: str | None = None
    active_tab: dict | None = None
    flash_mode: bool = True


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    from orchestrator import run_orchestrator

    settings = _resolve_settings()
    settings["flash_mode"] = req.flash_mode
    if not settings.get("api_key"):
        return JSONResponse(status_code=400, content={"error": "No active model configured."})
    if not req.content:
        return JSONResponse(status_code=400, content={"error": "Empty message"})

    if req.session_id:
        session_id = req.session_id
    else:
        session_id = create_session(title=_generate_title(req.content))

    add_message(session_id, "user", req.content)

    user_content = req.content
    if req.active_tab and req.active_tab.get("url"):
        user_content = f"[User is currently on: {req.active_tab['url']} — \"{req.active_tab.get('title', '')}\"]\n\n{req.content}"
        settings["start_url"] = req.active_tab["url"]

    rt = _get_runtime(session_id)

    if rt.task and not rt.task.done():
        # Orchestrator already running — feed message into its queue
        await rt.orch_queue.put(user_content)
    else:
        # Start new orchestrator
        rt.orch_queue = asyncio.Queue()
        await rt.orch_queue.put(user_content)

        async def _run(sid, s, queue, runtime):
            try:
                await run_orchestrator(runtime.events, sid, s, queue, runtime)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"Orchestrator error: {traceback.format_exc()}")
                try:
                    await runtime.events.put({"type": "error", "session_id": sid, "message": str(e)})
                except Exception:
                    pass

        rt.task = asyncio.create_task(_run(session_id, settings, rt.orch_queue, rt))

    return {
        "session_id": session_id,
        "title": _generate_title(req.content) if not req.session_id else None,
    }


# ── REST: Cancel ──


@app.post("/api/sessions/{session_id}/cancel")
async def api_cancel(session_id: str):
    rt = _runtimes.get(session_id)
    if rt and rt.task:
        rt.task.cancel()
        rt.planning_queue = None
    await _get_runtime(session_id).events.put({"type": "cancelled", "session_id": session_id})
    return {"ok": True}


# ── REST: Planning actions ──


class PlanningRespondRequest(BaseModel):
    content: str


@app.post("/api/sessions/{session_id}/planning/respond")
async def api_planning_respond(session_id: str, req: PlanningRespondRequest):
    add_message(session_id, "user", req.content)
    rt = _runtimes.get(session_id)
    if rt and rt.planning_queue:
        await rt.planning_queue.put(req.content)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/planning/approve")
async def api_planning_approve(session_id: str, payload: WorkflowCreate):
    """Save workflow AND signal the planning agent in one atomic request."""
    data = payload.model_dump()

    # Save the workflow
    if data.get("file_content"):
        meta, _ = parse_frontmatter(data["file_content"])
        slug = slugify(meta.get("name", "untitled"))
    elif data.get("name"):
        slug = slugify(data["name"])
    else:
        return JSONResponse(status_code=400, content={"error": "name or file_content required"})

    if get_workflow(slug):
        # Update existing
        save_workflow(data, workflow_id=slug)
        wf_id = slug
    else:
        wf_id = save_workflow(data)

    # Signal planning agent
    rt = _runtimes.get(session_id)
    if rt and rt.planning_queue:
        await rt.planning_queue.put({"type": "approve"})

    return {"ok": True, "id": wf_id}


@app.post("/api/sessions/{session_id}/planning/reject")
async def api_planning_reject(session_id: str):
    rt = _runtimes.get(session_id)
    if rt and rt.planning_queue:
        await rt.planning_queue.put({"type": "reject"})
    return {"ok": True}


class PlanningTestRequest(BaseModel):
    params: dict = {}


@app.post("/api/sessions/{session_id}/planning/test")
async def api_planning_test(session_id: str, req: PlanningTestRequest | None = None):
    rt = _runtimes.get(session_id)
    if not rt or not rt.planning_queue:
        return JSONResponse(status_code=400, content={"error": "No active planning session"})

    q = rt.planning_queue
    workflow_markdown = getattr(q, "_last_proposed_markdown", "")
    manifest_yaml = getattr(q, "_last_proposed_manifest", "")
    if not workflow_markdown:
        return JSONResponse(status_code=400, content={"error": "No workflow to test"})

    user_params = req.params if req else {}
    manifest = yaml.safe_load(manifest_yaml) or {}
    task = workflow_markdown
    for p in manifest.get("parameters", []):
        if isinstance(p, str):
            pname, default = p, ""
        else:
            pname, default = p.get("name", ""), p.get("default", "")
        value = user_params.get(pname) or default
        if pname and value:
            task = task.replace(f"{{{{{pname}}}}}", value)

    await rt.events.put({"type": "test_start", "session_id": session_id})

    settings = _resolve_settings()
    try:
        result = await run_agent(
            task, rt.events, session_id, settings,
            agent_overrides={"flash_mode": True, "use_thinking": False, "use_vision": "auto"},
        )
        await rt.events.put({
            "type": "test_result", "session_id": session_id,
            "success": True, "return_value": result or "Workflow completed.",
            "error": "", "traceback": "",
        })
        await q.put({"type": "test_result", "success": True, "result_text": result or ""})
    except Exception as e:
        tb = traceback.format_exc()
        await rt.events.put({
            "type": "test_result", "session_id": session_id,
            "success": False, "error": str(e), "traceback": tb,
        })
        await q.put({"type": "test_result", "success": False, "error": str(e), "traceback": tb})

    return {"ok": True}


# ── REST: Recording complete ──


class RecordingCompleteRequest(BaseModel):
    start_url: str = ""
    events: list = []
    audio_b64: str = ""


@app.post("/api/sessions/{session_id}/recording")
async def api_recording_complete(session_id: str, req: RecordingCompleteRequest):
    rt = _runtimes.get(session_id)
    if rt:
        await rt.orch_queue.put({"type": "recording", "data": req.model_dump()})
    return {"ok": True}


# ── REST: GIF replay ──


@app.get("/api/sessions/{session_id}/replay.gif")
async def session_replay_gif(session_id: str):
    gif_path = CHATS_DIR / session_id / "replay.gif"
    if not gif_path.exists():
        return JSONResponse(status_code=404, content={"error": "No replay available"})
    return FileResponse(str(gif_path), media_type="image/gif")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
