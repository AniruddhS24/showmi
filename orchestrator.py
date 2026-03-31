"""Orchestrator Agent — routes user messages to browser agent, recording, or planning.

Sits between the user and sub-agents. Uses raw anthropic/openai SDK clients
with tool definitions (same pattern as planning.py).
"""

import asyncio
import json
import traceback

from starlette.websockets import WebSocket, WebSocketState

from db import add_message
from planning import (
    _make_anthropic_client,
    _make_openai_client,
    run_planning_agent,
)

# ── System prompt ──

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are an orchestrator for a browser automation assistant called Showmi. The user \
talks to you naturally and you decide what to do.

You have four tools:

1. **run_browser_agent** — Execute a browser automation task. Use this when the user \
wants something DONE in their browser (navigate, click, fill forms, extract data, etc.). \
Pass a clear, detailed task description.

2. **run_workflow** — Run a saved workflow by name. Use this when the user asks to run \
a specific workflow. The workflow file will be loaded and passed to the browser agent \
as detailed instructions. If the workflow has parameters (like {{destination}}), ask the \
user for values first, then include them in the task.

3. **start_recording** — Ask the user to demonstrate a workflow by recording their browser \
actions. Use this when the user wants to TEACH you something new, e.g.:
   - "let me show you how to..."
   - "I want to create a workflow for..."
   - "record how I do X"
   - "teach you to do X"
   After recording completes, you will automatically receive the recording data.

4. **start_planning** — Process a recorded demonstration into a reusable workflow. \
Call this IMMEDIATELY after you receive recording data from start_recording. \
Pass the recording data as-is.

Guidelines:
- For direct browser tasks → run_browser_agent
- For running a saved workflow → run_workflow
- For teaching/demonstrating → start_recording, then start_planning
- For questions or conversation → respond directly (no tool call)
- Always briefly acknowledge what you're about to do before calling a tool
- After a sub-agent finishes, summarize the result for the user
- If the user's intent is ambiguous, ask a clarifying question before acting
- When running a workflow with parameters, collect parameter values from the user first
{context}\
"""

# ── Tool definitions ──

ANTHROPIC_TOOLS = [
    {
        "name": "run_browser_agent",
        "description": "Run the browser automation agent to complete a task in the user's browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Detailed task description for the browser agent",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "run_workflow",
        "description": "Run a saved workflow by name or ID. Loads the workflow file and passes it to the browser agent as instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The workflow ID (slug) to run",
                },
                "task_context": {
                    "type": "string",
                    "description": "Additional context or parameter values for this run (e.g., 'destination=NYC, date=2024-04-15')",
                    "default": "",
                },
            },
            "required": ["workflow_id"],
        },
    },
    {
        "name": "start_recording",
        "description": "Tell the user to demonstrate a workflow by recording their browser actions. Use when the user wants to teach you something new.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Message to show the user before recording starts (explain what to do)",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "start_planning",
        "description": "Process a recorded browser demonstration into a reusable workflow. Call this after receiving recording data from start_recording.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        },
    }
    for t in ANTHROPIC_TOOLS
]


async def _safe_send(ws: WebSocket, data: dict) -> bool:
    """Send JSON over WebSocket, returning False if connection is closed."""
    try:
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_json(data)
            return True
    except Exception:
        pass
    return False


# ── Tool execution ──


async def _execute_run_browser_agent(
    task: str, ws: WebSocket, session_id: str, settings: dict,
    agent_overrides: dict | None = None,
) -> str:
    """Execute the browser agent and return a result summary."""
    from server import run_agent_ws

    try:
        result = await run_agent_ws(task, ws, session_id, settings, agent_overrides=agent_overrides)
        return result or "Browser agent completed (no result text)."
    except Exception as e:
        return f"Browser agent failed: {e}"


def _format_workflow_task(workflow: dict, task_context: str) -> str:
    """Convert a workflow dict into an optimized browser-use task prompt."""
    from workflow_utils import parse_frontmatter
    import re

    meta, body = parse_frontmatter(workflow.get("file_content", ""))
    name = meta.get("name", "workflow")
    description = meta.get("description", "")
    params = meta.get("parameters", [])

    # Parse task_context into parameter values (e.g. "university=MIT, field=NLP")
    param_values = {}
    if task_context:
        # Try key=value pairs first
        for match in re.finditer(r'(\w+)\s*=\s*"?([^",]+)"?', task_context):
            param_values[match.group(1)] = match.group(2).strip()

    # Fill defaults for missing params
    for p in params:
        pname = p.get("name", "")
        if pname and pname not in param_values and p.get("default"):
            param_values[pname] = p["default"]

    # Substitute {{param}} placeholders
    resolved_body = body
    for k, v in param_values.items():
        resolved_body = resolved_body.replace(f"{{{{{k}}}}}", v)

    lines = [
        f"Execute this workflow: {name}",
        f"Goal: {description}",
    ]
    if param_values:
        lines.append("\nParameters:")
        for k, v in param_values.items():
            lines.append(f"  {k} = {v}")

    lines.append(f"\n{resolved_body}")
    lines.append(
        "\nIMPORTANT: Follow these steps exactly in order. "
        "If an element is not found, try scrolling or use send_keys with Tab/Enter as fallback. "
        "If navigation fails, use go_to_url to retry."
    )
    return "\n".join(lines)


# Workflow execution uses speed-optimized agent settings (DOM-only, no screenshots)
_WORKFLOW_AGENT_OVERRIDES = {
    "flash_mode": True,
    "use_thinking": False,
    "use_vision": False,
}

# Playwright runner is disabled — all workflows run via browser-use agent with markdown
_ENABLE_PLAYWRIGHT = False


async def _execute_run_workflow(
    workflow_id: str,
    task_context: str,
    ws: WebSocket,
    session_id: str,
    settings: dict,
) -> str:
    """Load a workflow and run it. Uses Playwright runner for new format, browser-use for legacy."""
    from workflow_utils import get_workflow

    workflow = get_workflow(workflow_id)
    if not workflow:
        return f"Workflow '{workflow_id}' not found."

    # For playwright-format workflows, load the markdown file for browser-use
    if workflow.get("format") == "playwright":
        from db import WORKFLOWS_DIR

        md_path = WORKFLOWS_DIR / workflow_id / "workflow.md"
        if md_path.exists():
            workflow = {
                "file_content": md_path.read_text(),
                "parameters": workflow.get("parameters", []),
            }
        else:
            return f"Workflow '{workflow_id}' has no markdown file."

    # All workflows run via browser-use agent with DOM (no screenshots)
    task = _format_workflow_task(workflow, task_context)
    return await _execute_run_browser_agent(
        task, ws, session_id, settings, agent_overrides=_WORKFLOW_AGENT_OVERRIDES
    )


async def _execute_start_recording(
    instruction: str, ws: WebSocket, session_id: str, queue: asyncio.Queue
) -> dict:
    """Send recording command to frontend and wait for recording data."""
    await ws.send_json(
        {
            "type": "orchestrator_command",
            "session_id": session_id,
            "command": "start_recording",
            "instruction": instruction,
        }
    )

    # Block until the frontend sends recording_complete (routed to our queue by server.py)
    recording_data = await queue.get()

    # Expect a dict with type="recording"
    if isinstance(recording_data, dict) and recording_data.get("type") == "recording":
        return recording_data.get("data", {})

    # If we got a plain string (user typed something instead), return empty
    return {}


async def _execute_start_planning(
    recording: dict,
    ws: WebSocket,
    session_id: str,
    settings: dict,
    planning_queues: dict,
) -> str:
    """Run the planning agent with the recorded data. Returns result summary."""
    if not recording or not recording.get("events"):
        return "No recording data available. Ask the user to record first."

    planning_queue = asyncio.Queue()
    planning_queue._recording = recording
    planning_queues[session_id] = planning_queue

    try:
        await run_planning_agent(recording, ws, session_id, settings, planning_queue)
        finalized = getattr(planning_queue, "_finalized_workflow", None)
        if finalized:
            return "Workflow planning completed. The user has been asked to approve or reject."
        else:
            return "Workflow planning session ended."
    except asyncio.CancelledError:
        return "Workflow planning was cancelled."
    except Exception as e:
        return f"Workflow planning failed: {e}"
    finally:
        planning_queues.pop(session_id, None)


# ── Anthropic orchestrator loop ──


async def _run_orchestrator_anthropic(
    messages: list,
    system_prompt: str,
    settings: dict,
    ws: WebSocket,
    session_id: str,
    queue: asyncio.Queue,
    planning_queues: dict,
) -> None:
    """Run the orchestrator conversation loop using Anthropic's API."""
    client = _make_anthropic_client(settings)
    model = settings.get("model", "claude-sonnet-4-20250514")

    # Track the last recording received (for start_planning)
    last_recording = None

    while True:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            tools=ANTHROPIC_TOOLS,
            messages=messages,
        )

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []

        for block in assistant_content:
            if block.type == "text" and block.text.strip():
                add_message(session_id, "assistant", block.text)
                if not await _safe_send(ws, {
                    "type": "orchestrator_message",
                    "session_id": session_id,
                    "content": block.text,
                }):
                    return  # WebSocket closed
            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input
                result_text = ""

                if tool_name == "run_browser_agent":
                    task = tool_input.get("task", "")
                    result_text = await _execute_run_browser_agent(
                        task, ws, session_id, settings
                    )

                elif tool_name == "run_workflow":
                    wf_id = tool_input.get("workflow_id", "")
                    ctx = tool_input.get("task_context", "")
                    result_text = await _execute_run_workflow(
                        wf_id, ctx, ws, session_id, settings
                    )

                elif tool_name == "start_recording":
                    instruction = tool_input.get("instruction", "Please demonstrate the workflow. Click Stop when done.")
                    recording = await _execute_start_recording(
                        instruction, ws, session_id, queue
                    )
                    last_recording = recording
                    event_count = len(recording.get("events", []))
                    result_text = f"Recording received with {event_count} events. Call start_planning to process it into a workflow."

                elif tool_name == "start_planning":
                    if last_recording:
                        result_text = await _execute_start_planning(
                            last_recording, ws, session_id, settings, planning_queues
                        )
                        last_recording = None
                    else:
                        result_text = "No recording data available. Call start_recording first."

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    }
                )

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn":
            # No tool calls, wait for next user message
            user_msg = await queue.get()
            if isinstance(user_msg, dict) and user_msg.get("type") == "recording":
                # Recording arrived without start_recording tool call (edge case)
                last_recording = user_msg.get("data", {})
                event_count = len(last_recording.get("events", []))
                messages.append(
                    {
                        "role": "user",
                        "content": f"[Recording received with {event_count} events. Process it into a workflow.]",
                    }
                )
            else:
                messages.append({"role": "user", "content": str(user_msg)})


# ── OpenAI orchestrator loop ──


async def _run_orchestrator_openai(
    messages: list,
    system_prompt: str,
    settings: dict,
    ws: WebSocket,
    session_id: str,
    queue: asyncio.Queue,
    planning_queues: dict,
) -> None:
    """Run the orchestrator conversation loop using OpenAI's API."""
    client = _make_openai_client(settings)
    model = settings.get("model", "gpt-4o")

    openai_messages = [{"role": "system", "content": system_prompt}] + messages

    last_recording = None

    while True:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            tools=OPENAI_TOOLS,
            messages=openai_messages,
        )

        choice = response.choices[0]
        message = choice.message
        openai_messages.append(message.model_dump(exclude_none=True))

        if message.content:
            add_message(session_id, "assistant", message.content)
            if not await _safe_send(ws, {
                "type": "orchestrator_message",
                "session_id": session_id,
                "content": message.content,
            }):
                return  # WebSocket closed

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                result_text = ""

                if tool_name == "run_browser_agent":
                    task = tool_input.get("task", "")
                    result_text = await _execute_run_browser_agent(
                        task, ws, session_id, settings
                    )

                elif tool_name == "run_workflow":
                    wf_id = tool_input.get("workflow_id", "")
                    ctx = tool_input.get("task_context", "")
                    result_text = await _execute_run_workflow(
                        wf_id, ctx, ws, session_id, settings
                    )

                elif tool_name == "start_recording":
                    instruction = tool_input.get("instruction", "Please demonstrate the workflow. Click Stop when done.")
                    recording = await _execute_start_recording(
                        instruction, ws, session_id, queue
                    )
                    last_recording = recording
                    event_count = len(recording.get("events", []))
                    result_text = f"Recording received with {event_count} events. Call start_planning to process it into a workflow."

                elif tool_name == "start_planning":
                    if last_recording:
                        result_text = await _execute_start_planning(
                            last_recording, ws, session_id, settings, planning_queues
                        )
                        last_recording = None
                    else:
                        result_text = "No recording data available. Call start_recording first."

                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )
        elif choice.finish_reason == "stop":
            user_msg = await queue.get()
            if isinstance(user_msg, dict) and user_msg.get("type") == "recording":
                last_recording = user_msg.get("data", {})
                event_count = len(last_recording.get("events", []))
                openai_messages.append(
                    {
                        "role": "user",
                        "content": f"[Recording received with {event_count} events. Process it into a workflow.]",
                    }
                )
            else:
                openai_messages.append({"role": "user", "content": str(user_msg)})


# ── Main entry point ──


def _build_orchestrator_context() -> str:
    """Build context string for the orchestrator system prompt."""
    from db import get_identity_text, get_memory_text
    from hooks import load_workflows_text
    from workflow_utils import list_workflows

    parts = []
    identity = get_identity_text()
    if identity:
        parts.append(f"\n\nIdentity:\n{identity}")
    memory = get_memory_text()
    if memory:
        parts.append(f"\n\nMemory:\n{memory}")

    # Include workflow index so the orchestrator knows exact IDs
    wf_list = list_workflows()
    if wf_list:
        lines = ["Available workflows (use the id for run_workflow):"]
        for wf in wf_list:
            params = ", ".join(p["name"] for p in wf.get("parameters", []))
            lines.append(f'  - id="{wf["id"]}", name="{wf.get("name", wf["id"])}", params=[{params}]')
        parts.append("\n\n" + "\n".join(lines))

    workflows = load_workflows_text()
    if workflows:
        parts.append(f"\n\n{workflows}")
    return "".join(parts)


async def run_orchestrator(
    ws: WebSocket,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
    planning_queues: dict,
) -> None:
    """Run the orchestrator agent conversation loop.

    Args:
        ws: WebSocket connection
        session_id: Current session ID
        settings: LLM settings dict
        queue: Queue for receiving user messages and recording data
        planning_queues: Shared dict for planning agent queue routing
    """
    try:
        context = _build_orchestrator_context()
        system_prompt = ORCHESTRATOR_SYSTEM_PROMPT.format(context=context)

        # Wait for first user message
        first_msg = await queue.get()
        if isinstance(first_msg, dict):
            first_msg = json.dumps(first_msg)

        provider = settings.get("provider", "local")
        messages = [{"role": "user", "content": str(first_msg)}]

        if provider == "anthropic":
            await _run_orchestrator_anthropic(
                messages, system_prompt, settings, ws, session_id, queue, planning_queues
            )
        else:
            await _run_orchestrator_openai(
                messages, system_prompt, settings, ws, session_id, queue, planning_queues
            )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(f"Orchestrator error: {tb}")
        try:
            await ws.send_json(
                {
                    "type": "error",
                    "session_id": session_id,
                    "message": f"Orchestrator error: {e}",
                }
            )
        except Exception:
            pass
