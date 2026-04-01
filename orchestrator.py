"""Orchestrator Agent — routes user messages to browser agent, recording, or planning.

Sits between the user and sub-agents. Uses raw anthropic/openai SDK clients
with tool definitions (same pattern as planning.py).
"""

import asyncio
import json
import traceback

from db import add_message, get_session_messages
from planning import (
    _make_anthropic_client,
    _make_openai_client,
    run_planning_agent,
)

# ── Constants ──

from config import DEFAULT_ANTHROPIC_MODEL, DEFAULT_OPENAI_MODEL

DEFAULT_RECORDING_INSTRUCTION = "Please demonstrate the workflow. Click Stop when done."
RESULT_DISPLAY_LIMIT = 2000

# ── System prompt ──

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are an orchestrator for a browser automation assistant called Showmi. The user \
talks to you naturally and you decide what to do.

## Tools

1. **run_browser_agent** — Execute a browser automation task. Pass a clear, detailed task \
description. Always pass retrieved memories as 'context'.

2. **run_workflow** — Run a saved workflow by name or ID. Ask the user for any required \
parameter values before running.

3. **list_workflows** — List all saved workflows with their IDs, names, descriptions, and \
parameters. Call this before run_browser_agent. If any listed workflow covers what the user \
asked for, use run_workflow with that ID — never run a task from scratch when a saved \
workflow already handles it.

4. **query_memories** — Search stored memories. Call this BEFORE every browser task, no \
exceptions. Pass the results as 'context' to run_browser_agent or run_workflow. Also call \
this when the user asks what you remember about something.

5. **store_memory** — Persist information across sessions. Three uses:
   - type='episodic': Store ONLY when something unexpected, novel, or noteworthy happened \
during a run — a surprising outcome, a failure worth remembering, a new pattern discovered. \
Do NOT store routine successful completions ("I did X on Y") — those are noise.
   - type='procedural': Store when you discover a non-obvious site trick, workaround, or \
navigation pattern that would help future runs. Use priority=1 if the user explicitly corrected you.
   - type='semantic': When the user states a preference or fact about themselves.

6. **start_recording** — Ask the user to demonstrate a workflow by recording their browser \
actions. Use when the user wants to teach you something new.

7. **start_planning** — Process a recorded demonstration into a reusable workflow. \
Call this IMMEDIATELY after start_recording returns.

8. **save_as_workflow** — Save the current session's browser actions as a reusable workflow.

9. **evict_memory** — Delete a memory by ID. Use when the user asks to remove or forget \
something, or when a memory is outdated and needs replacing (evict, then store_memory). \
Call query_memories first to find the right ID.

10. **update_workflow** — Modify a workflow's markdown file. Use when the user asks to \
change steps, parameters, name, or description of an existing workflow. Call list_workflows \
first to get the ID, then pass the full updated markdown including frontmatter.

## Memory Rules (follow these strictly)

BEFORE any browser task:
  1. query_memories with keywords from the task (site name, action type, topic)
  2. Pass results as 'context' to run_browser_agent or run_workflow

AFTER any completed browser run — reflect before storing:
  1. First check query_memories results from before the run — is there anything NEW worth remembering?
  2. Only store_memory type='episodic' if something unexpected, novel, or noteworthy happened \
(a failure, a surprising result, a new discovery). Skip routine "task completed successfully" entries.
  3. If a non-obvious site trick or workaround was discovered: store type='procedural'

IMMEDIATELY when the user corrects you or states a preference:
  - store_memory before replying. Do not ask for confirmation.

## Other Guidelines

- Always call list_workflows before run_browser_agent. Read the results and use run_workflow if any workflow matches — do not run from scratch.
- If the user's intent is ambiguous, ask before acting
- Keep your messages brief — the user can see what the agent is doing\
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
                "context": {
                    "type": "string",
                    "description": "Optional context to inject into the agent's system prompt, e.g. relevant memories retrieved via query_memories.",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "run_workflow",
        "description": "Run a saved workflow by name or ID. Loads the workflow file and passes it to the browser agent as instructions. Query memories first and pass results as context.",
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
                "context": {
                    "type": "string",
                    "description": "Relevant memories retrieved via query_memories to inject as context.",
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
    {
        "name": "list_workflows",
        "description": "List all saved workflows with their IDs, names, descriptions, and parameters. Call this before run_browser_agent to check if a saved workflow already covers the task.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "save_as_workflow",
        "description": "Save the current session's browser actions as a reusable workflow. Gathers what the agent did in this session and creates a workflow from it. Use when the user says 'save this as a workflow' or 'remember how to do this'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A short name for the workflow",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the workflow does",
                    "default": "",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "query_memories",
        "description": (
            "Search stored memories for context relevant to a task. "
            "Call this before run_browser_agent when the task involves a site or workflow "
            "the user has used before. Returns matching memories you can pass as 'context' "
            "to run_browser_agent. Also call this when the user asks what you remember about something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing what to search for, e.g. 'linkedin search' or 'gmail draft'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "store_memory",
        "description": (
            "Persist a correction, user preference, or site-specific trick as a memory. "
            "Call this when the user corrects you, says 'next time do X', 'remember that', "
            "'always use X for Y', 'don't forget', or provides any instruction that should "
            "persist across sessions. Use type='procedural' + priority=1 for corrections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["episodic", "procedural", "semantic"],
                    "description": "episodic = what happened in a specific run; procedural = site trick or how-to; semantic = general fact about the user or their preferences",
                },
                "content": {
                    "type": "string",
                    "description": "The memory content. Be specific and concise — one sentence, under 150 chars.",
                },
                "priority": {
                    "type": "integer",
                    "enum": [0, 1],
                    "description": "1 = high priority (use for human corrections), 0 = normal",
                },
            },
            "required": ["type", "content"],
        },
    },
    {
        "name": "evict_memory",
        "description": (
            "Delete a memory by ID. Use when the user asks to remove a memory, or when "
            "a memory is outdated/wrong and needs to be replaced (evict then store_memory). "
            "Call query_memories first to find the ID of the memory to evict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "The ID of the memory to delete.",
                },
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "update_workflow",
        "description": (
            "Modify an existing workflow's markdown file. Use when the user asks to change, "
            "update, or tweak a workflow's steps, parameters, name, or description. "
            "Call list_workflows first to find the workflow ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "The ID (slug) of the workflow to update.",
                },
                "file_content": {
                    "type": "string",
                    "description": (
                        "The full updated markdown content including YAML frontmatter. "
                        "Preserve the existing frontmatter fields (name, description, parameters, "
                        "created_at) and update only what the user asked to change. "
                        "IMPORTANT: parameters must be a list of objects with 'name', 'description', "
                        "and 'default' keys, e.g.:\n"
                        "parameters:\n"
                        "  - name: company_name\n"
                        "    description: \"The target company\"\n"
                        "    default: \"\""
                    ),
                },
            },
            "required": ["workflow_id", "file_content"],
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


async def _emit(event_bus: asyncio.Queue, data: dict) -> None:
    """Push an event onto the SSE bus."""
    await event_bus.put(data)


# ── Tool execution ──


def _execute_query_memories(query: str) -> str:
    """Search stored memories and return formatted results."""
    from db import retrieve_memories, use_memory
    memories = retrieve_memories(query, limit=5)
    if not memories:
        return "No relevant memories found."
    # Track usage so ranking improves over time
    for m in memories:
        use_memory(m["id"])
    _TYPE_LABELS = {"episodic": "Past run", "procedural": "How-to", "semantic": "Fact"}
    lines = [f"Found {len(memories)} relevant memories:\n"]
    for m in memories:
        label = _TYPE_LABELS.get(m["type"], m["type"])
        lines.append(f"- [{label} | id={m['id']}] {m['content']}")
    return "\n".join(lines)


async def _execute_run_browser_agent(
    task: str, event_bus: asyncio.Queue, session_id: str, settings: dict,
    context: str | None = None,
    agent_overrides: dict | None = None,
) -> str:
    """Execute the browser agent and return a result summary."""
    from server import run_agent

    if context:
        task = f"Context from memory:\n{context}\n\n---\n\n{task}"

    try:
        result = await run_agent(
            task, event_bus, session_id, settings,
            agent_overrides=agent_overrides,
        )
        summary = result or "Browser agent completed (no result text)."
        return (
            f"{summary}\n\n"
            "---\n"
            "Reflect on this run. Only call store_memory if something genuinely worth "
            "remembering happened — a surprising outcome, a failure, a new site-specific trick, "
            "or a pattern not already in your memories. Do NOT store routine completions like "
            "'Successfully did X on Y'. If you discovered a non-obvious workaround, store it as "
            "type='procedural'. If something unexpected happened, store as type='episodic'."
        )
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
        if isinstance(p, str):
            pname, default = p, ""
        else:
            pname, default = p.get("name", ""), p.get("default", "")
        if pname and pname not in param_values and default:
            param_values[pname] = default

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


async def _execute_run_workflow(
    workflow_id: str,
    task_context: str,
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    context: str | None = None,
    flash_mode: bool = True,
) -> str:
    """Load a workflow and run it via browser-use agent."""
    from workflow_utils import get_workflow

    workflow = get_workflow(workflow_id)
    if not workflow:
        return f"Workflow '{workflow_id}' not found."

    task = _format_workflow_task(workflow, task_context)
    return await _execute_run_browser_agent(
        task, event_bus, session_id, settings,
        context=context or None,
        agent_overrides={"flash_mode": flash_mode, "use_thinking": False, "use_vision": "auto"},
    )


async def _execute_start_recording(
    instruction: str, event_bus: asyncio.Queue, session_id: str, queue: asyncio.Queue
) -> dict:
    """Send recording command to frontend and wait for recording data."""
    await _emit(event_bus, {
        "type": "orchestrator_command",
        "session_id": session_id,
        "command": "start_recording",
        "instruction": instruction,
    })

    # Block until the frontend sends recording_complete (routed to our queue by server.py)
    recording_data = await queue.get()

    # Expect a dict with type="recording"
    if isinstance(recording_data, dict) and recording_data.get("type") == "recording":
        return recording_data.get("data", {})

    # If we got a plain string (user typed something instead), return empty
    return {}


async def _execute_start_planning(
    recording: dict,
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    runtime,
) -> str:
    """Run the planning agent with the recorded data. Returns result summary."""
    if not recording or not recording.get("events"):
        return "No recording data available. Ask the user to record first."

    planning_queue = asyncio.Queue()
    planning_queue._recording = recording
    runtime.planning_queue = planning_queue

    try:
        await run_planning_agent(recording, event_bus, session_id, settings, planning_queue)
        outcome = getattr(planning_queue, "_outcome", None)
        if outcome == "approved":
            return "Workflow approved and saved by the user."
        elif outcome == "rejected":
            return "Workflow rejected by the user."
        else:
            return "Workflow planning completed. The user has been asked to approve or reject."
    except asyncio.CancelledError:
        return "Workflow planning was cancelled."
    except Exception as e:
        return f"Workflow planning failed: {e}"
    finally:
        runtime.planning_queue = None


def _execute_store_memory(type: str, content: str, priority: int = 0) -> str:
    """Write a memory from the orchestrator context."""
    from db import add_memory
    try:
        memory_id = add_memory(type=type, content=content, priority=priority)
        label = "high-priority" if priority == 1 else "normal"
        return f"Memory stored (id={memory_id}, type={type}, priority={label}): {content[:100]}"
    except Exception as e:
        return f"Failed to store memory: {e}"


def _execute_evict_memory(memory_id: int) -> str:
    """Delete a memory by ID."""
    from db import delete_memory
    try:
        delete_memory(memory_id)
        return f"Memory {memory_id} deleted."
    except Exception as e:
        return f"Failed to delete memory: {e}"


def _execute_update_workflow(workflow_id: str, file_content: str) -> str:
    """Update a workflow's markdown file. Validates frontmatter before saving."""
    from workflow_utils import get_workflow, save_workflow, parse_frontmatter
    existing = get_workflow(workflow_id)
    if not existing:
        return f"Workflow '{workflow_id}' not found."

    # Validate frontmatter before saving
    meta, body = parse_frontmatter(file_content)
    if not meta:
        return (
            "Invalid workflow: missing YAML frontmatter. "
            "File must start with --- and contain name, description, parameters fields."
        )
    if not meta.get("name"):
        return "Invalid workflow: frontmatter must include 'name'."
    for p in meta.get("parameters", []):
        if not isinstance(p, dict) or "name" not in p:
            return (
                f"Invalid parameter format: {p!r}. Each parameter must be an object with "
                "'name', 'description', and 'default' keys. Example:\n"
                "parameters:\n"
                "  - name: company_name\n"
                "    description: \"The target company\"\n"
                "    default: \"\""
            )

    try:
        save_workflow({"file_content": file_content}, workflow_id=workflow_id)
        return f"Workflow '{workflow_id}' updated."
    except Exception as e:
        return f"Failed to update workflow: {e}"


def _execute_list_workflows() -> str:
    """List all saved workflows."""
    from workflow_utils import list_workflows

    wf_list = list_workflows()
    if not wf_list:
        return "No workflows saved yet."

    lines = []
    for wf in wf_list:
        params = ", ".join(
            p["name"] if isinstance(p, dict) else str(p)
            for p in wf.get("parameters", [])
        )
        lines.append(
            f'- id="{wf["id"]}", name="{wf.get("name", wf["id"])}", '
            f'description="{wf.get("description", "")}", params=[{params}]'
        )
    return "Saved workflows:\n" + "\n".join(lines)


async def _execute_save_as_workflow(
    name: str,
    description: str,
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    runtime,
) -> str:
    """Gather conversation context and run the planning agent to create a workflow."""
    from planning import run_planning_agent_from_context

    messages = get_session_messages(session_id)
    if not messages:
        return "No conversation history to create a workflow from."

    # Build context from session messages
    context_lines = [f"Workflow name: {name}"]
    if description:
        context_lines.append(f"Description: {description}")
    context_lines.append("\nSession history:")

    for msg in messages:
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = None

        if msg["role"] == "user":
            context_lines.append(f"\nUser: {msg['content']}")
        elif meta and meta.get("type") == "step":
            actions = meta.get("actions", [])
            goal = meta.get("goal", "")
            url = meta.get("url", "")
            step_num = meta.get("step_number", "?")
            action_descs = []
            for a in actions:
                action_data = a.get("action", {})
                if action_data:
                    action_descs.append(str(action_data))
            step_line = f"  Step {step_num}: {goal}" if goal else f"  Step {step_num}"
            if action_descs:
                step_line += f" -> {', '.join(action_descs)}"
            if url:
                step_line += f" (URL: {url})"
            context_lines.append(step_line)
        elif meta and meta.get("type") == "result":
            context_lines.append(f"\nResult: {meta.get('summary', msg['content'])}")

    context_text = "\n".join(context_lines)

    planning_queue = asyncio.Queue()
    runtime.planning_queue = planning_queue

    try:
        await run_planning_agent_from_context(
            context_text, event_bus, session_id, settings, planning_queue
        )
        outcome = getattr(planning_queue, "_outcome", None)
        if outcome == "approved":
            return "Workflow approved and saved by the user."
        elif outcome == "rejected":
            return "Workflow rejected by the user."
        proposed = getattr(planning_queue, "_last_proposed_markdown", None)
        if proposed:
            return "Workflow created from conversation. The user has been asked to approve or reject."
        else:
            return "Workflow planning session ended."
    except asyncio.CancelledError:
        return "Workflow planning was cancelled."
    except Exception as e:
        return f"Workflow planning failed: {e}"
    finally:
        runtime.planning_queue = None


# ── Shared tool dispatch ──


async def _dispatch_tool(
    tool_name: str,
    tool_input: dict,
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
    runtime,
    ctx: dict,
) -> str:
    """Dispatch a tool call and return the result text.

    ctx is a mutable dict carrying state between calls (e.g. last_recording).
    """
    if tool_name == "run_browser_agent":
        task = tool_input.get("task", "")
        context = tool_input.get("context") or None
        flash = settings.get("flash_mode", True)
        return await _execute_run_browser_agent(
            task, event_bus, session_id, settings, context=context,
            agent_overrides={"flash_mode": flash},
        )

    if tool_name == "query_memories":
        return _execute_query_memories(tool_input.get("query", ""))

    if tool_name == "run_workflow":
        wf_id = tool_input.get("workflow_id", "")
        task_ctx = tool_input.get("task_context", "")
        mem_ctx = tool_input.get("context", "") or None
        flash = settings.get("flash_mode", True)
        return await _execute_run_workflow(
            wf_id, task_ctx, event_bus, session_id, settings, context=mem_ctx, flash_mode=flash
        )

    if tool_name == "start_recording":
        instruction = tool_input.get("instruction", DEFAULT_RECORDING_INSTRUCTION)
        recording = await _execute_start_recording(instruction, event_bus, session_id, queue)
        ctx["last_recording"] = recording
        event_count = len(recording.get("events", []))
        return f"Recording received with {event_count} events. Call start_planning to process it into a workflow."

    if tool_name == "start_planning":
        rec = ctx.get("last_recording")
        if rec:
            result = await _execute_start_planning(rec, event_bus, session_id, settings, runtime)
            ctx["last_recording"] = None
            return result
        return "No recording data available. Call start_recording first."

    if tool_name == "list_workflows":
        return _execute_list_workflows()

    if tool_name == "save_as_workflow":
        return await _execute_save_as_workflow(
            tool_input.get("name", "workflow"),
            tool_input.get("description", ""),
            event_bus, session_id, settings, runtime,
        )

    if tool_name == "store_memory":
        mem_type = tool_input.get("type", "procedural")
        content = tool_input.get("content", "")
        priority = int(tool_input.get("priority", 0))
        result = _execute_store_memory(mem_type, content, priority)
        print(f"[memory] store_memory called: type={mem_type} priority={priority} content={content[:100]!r}")
        return result

    if tool_name == "evict_memory":
        mid = int(tool_input.get("memory_id", 0))
        print(f"[memory] evict_memory called: id={mid}")
        return _execute_evict_memory(mid)

    if tool_name == "update_workflow":
        return _execute_update_workflow(
            tool_input.get("workflow_id", ""),
            tool_input.get("file_content", ""),
        )

    print(f"[orchestrator] WARNING: unhandled tool call: {tool_name}")
    return f"Unknown tool: {tool_name}"


async def _handle_tool_call(
    tool_name: str,
    tool_input: dict,
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
    runtime,
    ctx: dict,
) -> str:
    """Emit events, dispatch tool, save to DB, and return result_text."""
    await _emit(event_bus, {
        "type": "tool_call_start",
        "session_id": session_id,
        "tool": tool_name,
        "args": tool_input,
    })

    result_text = await _dispatch_tool(
        tool_name, tool_input, event_bus, session_id, settings, queue, runtime, ctx
    )

    display_result = result_text[:RESULT_DISPLAY_LIMIT] + ("\u2026" if len(result_text) > RESULT_DISPLAY_LIMIT else "")
    add_message(session_id, "assistant", f"[{tool_name}]",
                metadata={"type": "tool_call", "tool": tool_name, "args": tool_input, "result": display_result})
    await _emit(event_bus, {
        "type": "tool_call_result",
        "session_id": session_id,
        "tool": tool_name,
        "result": display_result,
    })

    return result_text


async def _handle_user_message(queue: asyncio.Queue, ctx: dict) -> str:
    """Wait for next user message, handle recording edge case."""
    user_msg = await queue.get()
    if isinstance(user_msg, dict) and user_msg.get("type") == "recording":
        ctx["last_recording"] = user_msg.get("data", {})
        event_count = len(ctx["last_recording"].get("events", []))
        return f"[Recording received with {event_count} events. Process it into a workflow.]"
    return str(user_msg)


# ── Anthropic orchestrator loop ──


async def _run_orchestrator_anthropic(
    messages: list,
    system_prompt: str,
    settings: dict,
    event_bus: asyncio.Queue,
    session_id: str,
    queue: asyncio.Queue,
    runtime,
) -> None:
    """Run the orchestrator conversation loop using Anthropic's API."""
    client = _make_anthropic_client(settings)
    model = settings.get("model", DEFAULT_ANTHROPIC_MODEL)
    ctx = {"last_recording": None}

    while True:
        response = await client.messages.create(
            model=model, max_tokens=2048, system=system_prompt,
            tools=ANTHROPIC_TOOLS, messages=messages,
        )

        assistant_content = response.content
        messages.append({"role": "assistant", "content": [b.model_dump() for b in assistant_content]})

        tool_results = []
        for block in assistant_content:
            if block.type == "text" and block.text.strip():
                add_message(session_id, "assistant", block.text)
                await _emit(event_bus, {
                    "type": "orchestrator_message",
                    "session_id": session_id,
                    "content": block.text,
                })
            elif block.type == "tool_use":
                result_text = await _handle_tool_call(
                    block.name, block.input, event_bus, session_id,
                    settings, queue, runtime, ctx,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn":
            await _emit(event_bus, {"type": "orchestrator_ready", "session_id": session_id})
            messages.append({"role": "user", "content": await _handle_user_message(queue, ctx)})


# ── OpenAI orchestrator loop ──


async def _run_orchestrator_openai(
    messages: list,
    system_prompt: str,
    settings: dict,
    event_bus: asyncio.Queue,
    session_id: str,
    queue: asyncio.Queue,
    runtime,
) -> None:
    """Run the orchestrator conversation loop using OpenAI's API."""
    client = _make_openai_client(settings)
    model = settings.get("model", DEFAULT_OPENAI_MODEL)
    ctx = {"last_recording": None}

    openai_messages = [{"role": "system", "content": system_prompt}] + messages

    while True:
        response = await client.chat.completions.create(
            model=model, max_completion_tokens=2048,
            tools=OPENAI_TOOLS, messages=openai_messages,
        )

        choice = response.choices[0]
        message = choice.message
        openai_messages.append(message.model_dump(exclude_none=True))

        if message.content:
            add_message(session_id, "assistant", message.content)
            await _emit(event_bus, {
                "type": "orchestrator_message",
                "session_id": session_id,
                "content": message.content,
            })

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                result_text = await _handle_tool_call(
                    tc.function.name, tool_input, event_bus, session_id,
                    settings, queue, runtime, ctx,
                )
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })
        elif choice.finish_reason == "stop":
            await _emit(event_bus, {"type": "orchestrator_ready", "session_id": session_id})
            openai_messages.append({"role": "user", "content": await _handle_user_message(queue, ctx)})


# ── Main entry point ──


async def run_orchestrator(
    event_bus: asyncio.Queue,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
    runtime,
) -> None:
    """Run the orchestrator agent conversation loop."""
    try:
        # Wait for first user message
        first_msg = await queue.get()
        if isinstance(first_msg, dict):
            first_msg = json.dumps(first_msg)

        provider = settings.get("provider", "local")

        # Load prior conversation history from DB for multi-turn context
        prior_messages = get_session_messages(session_id)
        messages = []
        for msg in prior_messages:
            role = msg["role"]
            content = msg["content"] or ""
            # Skip the current message (already in first_msg) and step/result metadata
            meta = msg.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = None
            if meta and meta.get("type") in ("step", "result", "tool_call", "workflow_proposal"):
                continue
            if role in ("user", "assistant") and content:
                # Collapse consecutive same-role messages
                if messages and messages[-1]["role"] == role:
                    messages[-1]["content"] += "\n" + content
                else:
                    messages.append({"role": role, "content": content})

        # Ensure conversation ends with user role (add current message)
        if messages and messages[-1]["role"] == "user":
            # The last user message in DB is the current one (already added by server.py)
            pass
        else:
            messages.append({"role": "user", "content": str(first_msg)})

        if provider == "anthropic":
            await _run_orchestrator_anthropic(
                messages, ORCHESTRATOR_SYSTEM_PROMPT, settings, event_bus, session_id, queue, runtime
            )
        else:
            await _run_orchestrator_openai(
                messages, ORCHESTRATOR_SYSTEM_PROMPT, settings, event_bus, session_id, queue, runtime
            )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(f"Orchestrator error: {tb}")
        try:
            await event_bus.put({
                "type": "error",
                "session_id": session_id,
                "message": f"Orchestrator error: {e}",
            })
        except Exception:
            pass
