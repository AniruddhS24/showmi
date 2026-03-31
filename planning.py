"""Workflow Planning Agent — generates browser-use workflow markdown from recordings.

Uses raw anthropic/openai SDK clients for direct LLM conversations with tool definitions.
"""

import asyncio
import base64
import json
import tempfile
import traceback
from pathlib import Path

from starlette.websockets import WebSocket, WebSocketState

from db import add_message
from workflow_utils import (
    _format_recording_for_llm,
    _prefilter_events,
)

# ── System prompt ──

PLANNING_SYSTEM_PROMPT = """\
You are a Workflow Planning Agent. Given a recorded browser demonstration (and optionally a \
voice narration), produce a clean, reusable workflow spec that a browser-use agent can follow \
for any run of this task — not just a replay of the exact recording.

## Your Process

1. Analyze the recording to understand the INTENT of the task, not just the specific actions.
2. Ask 1-2 clarifying questions if anything is ambiguous (use ask_question tool). Skip if clear.
3. Write the workflow markdown and manifest YAML, then propose via propose_workflow.
4. Iterate based on test results or user feedback until approved.

## Workflow Markdown

The workflow_markdown field must follow this structure exactly:

```
## Task: {{workflow_name}}

One or two sentences describing what this workflow does and when to use it.

1. First step.
2. Second step.
3. For each item found:
   a. Sub-step.
   b. Sub-step.
4. Final step.
```

The `## Task:` heading and description paragraph are required — don't just produce a raw \
list of steps. The description should state the goal in plain English, not just restate \
the step list.

Writing guidelines:
- **Name elements precisely** using their visible text, label, or role (e.g. "click the \
'Submit' button", "type into the Search field"). Avoid vague references like "click the button".
- **Use `{{param_name}}` placeholders** for values that vary between runs.
- **Generalize loops**: if the user demonstrated 1-2 iterations of a repeating pattern, \
write it as "For each item found: ..." with lettered sub-steps.
- **Don't over-specify**: skip fallback instructions and error recovery boilerplate unless \
there's a specific fragile step that warrants it.

Available actions (use these names when precision helps): go_to_url, click, input_text, \
send_keys, extract, scroll_down, scroll_up, go_back, wait, done, search.

## Manifest YAML

```yaml
name: snake_case_name
description: One sentence describing what this workflow does.
parameters:
  - name: param_name
    description: What this parameter controls
    default: optional_default  # omit if no sensible default exists
```

Only include parameters for things a user would genuinely want to change between runs. \
Don't parameterize values that are always the same. Default values are optional — omit them \
for required inputs rather than using empty strings.

## Reading the Recording

The recording is an EXAMPLE of the task — generalize from it, don't replay it literally:
- Identify the start URL and core navigation path
- Determine which typed values are fixed vs. should be parameters
- Spot repeated patterns and express them as loops
- Ignore fumbles, retries, and back-navigation — focus on the intended flow

The demonstration may be incomplete or imprecise. Write instructions that will work reliably \
across runs, not just reproduce what you saw.\
"""


# ── Tool definitions ──

ANTHROPIC_TOOLS = [
    {
        "name": "propose_workflow",
        "description": "Show the proposed workflow to the user for review. "
        "The user can then Test, Approve, Reject, or give feedback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "manifest_yaml": {
                    "type": "string",
                    "description": "YAML manifest with name, description, and parameters",
                },
                "workflow_markdown": {
                    "type": "string",
                    "description": "Workflow markdown body. Must start with '## Task: name', followed by a 1-2 sentence description paragraph, then numbered steps.",
                },
            },
            "required": ["manifest_yaml", "workflow_markdown"],
        },
    },
    {
        "name": "ask_question",
        "description": "Ask the user a question. Use for clarifications about the workflow.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask"},
                "choices": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of choices. Omit for free text.",
                },
            },
            "required": ["question"],
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


# ── Audio transcription ──


async def transcribe_audio(audio_b64: str, settings: dict) -> str:
    """Transcribe audio using OpenAI Whisper API.

    Only works with OpenAI provider. Returns empty string for other providers.
    """
    if not audio_b64 or settings.get("provider") != "openai":
        return ""

    try:
        import openai

        if audio_b64.startswith("data:"):
            audio_b64 = audio_b64.split(",", 1)[1] if "," in audio_b64 else ""
        if not audio_b64:
            return ""

        audio_bytes = base64.b64decode(audio_b64)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        client = openai.AsyncOpenAI(
            api_key=settings.get("api_key", ""),
            base_url=settings.get("base_url") or None,
        )
        with open(tmp_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
            )

        Path(tmp_path).unlink(missing_ok=True)
        return transcript.text
    except Exception as e:
        print(f"Audio transcription failed: {e}")
        return ""


# ── Event curation ──

CURATE_SYSTEM_PROMPT = """\
You are an event curation agent. You receive a list of raw browser interaction events \
from a recorded demonstration and must select only the ESSENTIAL events that represent \
the core workflow.

Your job:
- Identify the meaningful high-level actions (navigate, click a link, fill a form field, \
submit, select an option).
- DISCARD events that are: redundant clicks (re-clicking the same thing), fumbling/mistakes \
(clicking wrong things then going back), scroll events, intermediate page loads, \
duplicate navigations, or events that don't contribute to the task.
- Merge sequences that represent one logical action (e.g., click on input + type = one action).
- Keep events that show DIFFERENT pages or DIFFERENT UI states — if two events produce \
the same visual result, keep only one.
- If the workflow has a repeating pattern (e.g., "visit profile, copy info, paste in sheet, \
go back" repeated N times), keep ONE full iteration of the loop plus the setup steps.

Output ONLY a JSON object with:
- "selected": list of event indices (1-based) to keep, in order
- "reasoning": one sentence explaining what the core workflow is

Keep the selected list to 5-12 events maximum. Prefer fewer over more.\
"""


def _format_events_compact(events: list[dict]) -> str:
    """Format events as a compact numbered list for the curation agent."""
    lines = []
    for i, event in enumerate(events, 1):
        target = event.get("target", {})
        desc = target.get("aria_label") or target.get("text") or target.get("selector", "")
        url = event.get("url", "")
        value = event.get("value", "")
        has_screenshot = bool(event.get("screenshot"))
        line = f"{i}. [{event.get('type', '?')}] target={desc!r} url={url!r}"
        if value:
            line += f" value={value!r}"
        if has_screenshot:
            line += " [screenshot]"
        lines.append(line)
    return "\n".join(lines)


async def _curate_events_anthropic(
    events: list[dict], audio_transcript: str, settings: dict
) -> list[int]:
    """Use Anthropic to select important event indices."""
    client = _make_anthropic_client(settings)
    model = settings.get("model", "claude-sonnet-4-20250514")

    user_msg = _format_events_compact(events)
    if audio_transcript:
        user_msg = f"User narration: {audio_transcript}\n\n{user_msg}"

    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=CURATE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    result = json.loads(text)
    return result.get("selected", [])


async def _curate_events_openai(
    events: list[dict], audio_transcript: str, settings: dict
) -> list[int]:
    """Use OpenAI to select important event indices."""
    client = _make_openai_client(settings)
    model = settings.get("model", "gpt-4o")

    user_msg = _format_events_compact(events)
    if audio_transcript:
        user_msg = f"User narration: {audio_transcript}\n\n{user_msg}"

    response = await client.chat.completions.create(
        model=model,
        max_completion_tokens=1024,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CURATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )

    text = response.choices[0].message.content.strip()
    result = json.loads(text)
    return result.get("selected", [])


async def curate_events(
    events: list[dict], audio_transcript: str, settings: dict
) -> list[dict]:
    """Select important events from a recording using an LLM curation step.

    Returns the filtered list of events (preserving original event dicts).
    Falls back to all events if curation fails.
    """
    if len(events) <= 12:
        return events

    try:
        provider = settings.get("provider", "local")
        if provider == "anthropic":
            selected_indices = await _curate_events_anthropic(events, audio_transcript, settings)
        else:
            selected_indices = await _curate_events_openai(events, audio_transcript, settings)

        curated = []
        for idx in selected_indices:
            if isinstance(idx, int) and 1 <= idx <= len(events):
                curated.append(events[idx - 1])

        if curated:
            print(f"Event curation: {len(events)} events → {len(curated)} selected")
            return curated

    except Exception as e:
        print(f"Event curation failed, using all events: {e}")

    return events


# ── LLM client helpers ──


def _make_anthropic_client(settings: dict):
    import anthropic
    return anthropic.AsyncAnthropic(api_key=settings.get("api_key", ""))


def _make_openai_client(settings: dict):
    import openai
    return openai.AsyncOpenAI(
        api_key=settings.get("api_key", "not-needed"),
        base_url=settings.get("base_url") or None,
    )


# ── Anthropic planning loop ──


async def _run_anthropic_planning(
    messages: list,
    settings: dict,
    ws: WebSocket,
    session_id: str,
    queue: asyncio.Queue,
) -> None:
    """Run the planning conversation loop using Anthropic's API."""
    client = _make_anthropic_client(settings)
    model = settings.get("model", "claude-sonnet-4-20250514")

    while True:
        response = await client.messages.create(
            model=model,
            max_tokens=8192,
            system=PLANNING_SYSTEM_PROMPT,
            tools=ANTHROPIC_TOOLS,
            messages=messages,
        )

        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []

        for block in assistant_content:
            if block.type == "text" and block.text.strip():
                add_message(session_id, "assistant", block.text)
                await _safe_send(ws,{
                    "type": "planning_message",
                    "session_id": session_id,
                    "content": block.text,
                })
            elif block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                if tool_name == "propose_workflow":
                    # Store proposed outputs on queue for test/approve handlers
                    queue._last_proposed_manifest = tool_input.get("manifest_yaml", "")
                    queue._last_proposed_markdown = tool_input.get("workflow_markdown", "")

                    # Save the proposed workflow to DB
                    add_message(session_id, "assistant",
                                "Proposed workflow:\n\n" + tool_input.get("workflow_markdown", ""),
                                metadata={"type": "workflow_proposal",
                                          "manifest_yaml": tool_input.get("manifest_yaml", ""),
                                          "workflow_markdown": tool_input.get("workflow_markdown", "")})

                    await _safe_send(ws,{
                        "type": "planning_tool_call",
                        "session_id": session_id,
                        "tool": "propose_workflow",
                        "args": tool_input,
                    })

                    # Block until user Tests, Approves, Rejects, or gives feedback
                    user_response = await queue.get()

                    # Handle structured responses
                    if isinstance(user_response, dict):
                        if user_response.get("type") == "approve":
                            queue._outcome = "approved"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "User approved the workflow. It has been saved.",
                            })
                            messages.append({"role": "user", "content": tool_results})
                            return
                        elif user_response.get("type") == "reject":
                            queue._outcome = "rejected"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": "User rejected the workflow.",
                            })
                            messages.append({"role": "user", "content": tool_results})
                            return
                        elif user_response.get("type") == "test_result":
                            if user_response.get("success"):
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Test PASSED. Output: {user_response.get('result_text', '')}",
                                })
                            else:
                                err_msg = f"Test FAILED.\nError: {user_response.get('error', 'Unknown')}"
                                tb = user_response.get("traceback", "")
                                if tb:
                                    err_msg += f"\nTraceback:\n{tb}"
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": err_msg,
                                })
                        else:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(user_response),
                            })
                    else:
                        # Plain text feedback
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"User feedback: {user_response}",
                        })

                elif tool_name == "ask_question":
                    question_text = tool_input.get("question", "")
                    add_message(session_id, "assistant", question_text,
                                metadata={"type": "ask_question", **tool_input})
                    await _safe_send(ws,{
                        "type": "planning_tool_call",
                        "session_id": session_id,
                        "tool": tool_name,
                        "args": tool_input,
                    })
                    user_response = await queue.get()
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(user_response),
                    })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        elif response.stop_reason == "end_turn":
            user_response = await queue.get()
            if isinstance(user_response, dict) and user_response.get("type") in ("approve", "reject"):
                return
            messages.append({"role": "user", "content": str(user_response)})


# ── OpenAI planning loop ──


async def _run_openai_planning(
    messages: list,
    settings: dict,
    ws: WebSocket,
    session_id: str,
    queue: asyncio.Queue,
) -> None:
    """Run the planning conversation loop using OpenAI's API."""
    client = _make_openai_client(settings)
    model = settings.get("model", "gpt-4o")

    openai_messages = [{"role": "system", "content": PLANNING_SYSTEM_PROMPT}] + messages

    while True:
        response = await client.chat.completions.create(
            model=model,
            max_completion_tokens=8192,
            tools=OPENAI_TOOLS,
            messages=openai_messages,
        )

        choice = response.choices[0]
        message = choice.message
        openai_messages.append(message.model_dump(exclude_none=True))

        if message.content:
            add_message(session_id, "assistant", message.content)
            await _safe_send(ws,{
                "type": "planning_message",
                "session_id": session_id,
                "content": message.content,
            })

        if message.tool_calls:
            for tc in message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                if tool_name == "propose_workflow":
                    queue._last_proposed_manifest = tool_input.get("manifest_yaml", "")
                    queue._last_proposed_markdown = tool_input.get("workflow_markdown", "")

                    # Save the proposed workflow to DB
                    add_message(session_id, "assistant",
                                "Proposed workflow:\n\n" + tool_input.get("workflow_markdown", ""),
                                metadata={"type": "workflow_proposal",
                                          "manifest_yaml": tool_input.get("manifest_yaml", ""),
                                          "workflow_markdown": tool_input.get("workflow_markdown", "")})

                    await _safe_send(ws,{
                        "type": "planning_tool_call",
                        "session_id": session_id,
                        "tool": "propose_workflow",
                        "args": tool_input,
                    })

                    user_response = await queue.get()

                    if isinstance(user_response, dict):
                        if user_response.get("type") in ("approve", "reject"):
                            queue._outcome = user_response["type"]
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"User {'approved' if user_response['type'] == 'approve' else 'rejected'} the workflow.",
                            })
                            return
                        elif user_response.get("type") == "test_result":
                            if user_response.get("success"):
                                content = f"Test PASSED. Output: {user_response.get('result_text', '')}"
                            else:
                                content = f"Test FAILED.\nError: {user_response.get('error', 'Unknown')}"
                                tb = user_response.get("traceback", "")
                                if tb:
                                    content += f"\nTraceback:\n{tb}"
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": content,
                            })
                        else:
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps(user_response),
                            })
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"User feedback: {user_response}",
                        })

                elif tool_name == "ask_question":
                    question_text = tool_input.get("question", "")
                    add_message(session_id, "assistant", question_text,
                                metadata={"type": "ask_question", **tool_input})
                    await _safe_send(ws,{
                        "type": "planning_tool_call",
                        "session_id": session_id,
                        "tool": tool_name,
                        "args": tool_input,
                    })
                    user_response = await queue.get()
                    # Store the user's answer
                    if isinstance(user_response, str):
                        add_message(session_id, "user", user_response)
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(user_response),
                    })
        elif choice.finish_reason == "stop":
            user_response = await queue.get()
            if isinstance(user_response, dict) and user_response.get("type") in ("approve", "reject"):
                return
            openai_messages.append({"role": "user", "content": str(user_response)})


# ── Main entry point ──


async def run_planning_agent(
    recording: dict,
    ws: WebSocket,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
) -> None:
    """Run the workflow planning agent conversation.

    Transcribes audio, formats the recording, then enters a conversation loop
    where the agent writes a hybrid Playwright script for the workflow.
    """
    try:
        audio_b64 = recording.get("audio_b64", "")
        audio_transcript = ""
        if audio_b64:
            await _safe_send(ws, {
                "type": "planning_message",
                "session_id": session_id,
                "content": "Transcribing audio narration...",
            })
            audio_transcript = await transcribe_audio(audio_b64, settings)
            if audio_transcript:
                await _safe_send(ws, {
                    "type": "planning_message",
                    "session_id": session_id,
                    "content": f"**Narration transcript:** {audio_transcript}",
                })
            else:
                await _safe_send(ws, {
                    "type": "planning_message",
                    "session_id": session_id,
                    "content": "Could not transcribe audio.",
                })

        raw_events = recording.get("events", [])
        filtered_events = _prefilter_events(raw_events)

        await _safe_send(ws, {
            "type": "planning_message",
            "session_id": session_id,
            "content": f"Analyzing {len(raw_events)} recorded events...",
        })

        curated_events = await curate_events(filtered_events, audio_transcript, settings)

        curated_recording = {**recording, "events": curated_events}
        formatted = _format_recording_for_llm(curated_recording, audio_transcript)

        # Store recording on queue for screenshot extraction on approve
        queue._recording = curated_recording

        user_msg = (
            f"I just recorded a browser demonstration. "
            f"Please analyze it and write a reusable workflow.\n\n"
            f"{formatted}"
        )

        provider = settings.get("provider", "local")

        if provider == "anthropic":
            messages = [{"role": "user", "content": user_msg}]
            await _run_anthropic_planning(messages, settings, ws, session_id, queue)
        else:
            messages = [{"role": "user", "content": user_msg}]
            await _run_openai_planning(messages, settings, ws, session_id, queue)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(f"Planning agent error: {tb}")
        try:
            await _safe_send(ws,{
                "type": "planning_error",
                "session_id": session_id,
                "message": str(e),
            })
        except Exception:
            pass


async def run_planning_agent_from_context(
    context_text: str,
    ws: WebSocket,
    session_id: str,
    settings: dict,
    queue: asyncio.Queue,
) -> None:
    """Run the planning agent from a conversation context (no recording needed).

    Used when the user wants to save a completed browser session as a workflow.
    """
    try:
        await _safe_send(ws, {
            "type": "planning_message",
            "session_id": session_id,
            "content": "Creating workflow from conversation history...",
        })

        user_msg = (
            "I want to turn the following browser automation session into a reusable workflow. "
            "The session describes what actions the agent took, including URLs, clicks, inputs, "
            "and results. Please analyze it and write a workflow markdown with manifest YAML.\n\n"
            f"{context_text}"
        )

        provider = settings.get("provider", "local")

        if provider == "anthropic":
            messages = [{"role": "user", "content": user_msg}]
            await _run_anthropic_planning(messages, settings, ws, session_id, queue)
        else:
            messages = [{"role": "user", "content": user_msg}]
            await _run_openai_planning(messages, settings, ws, session_id, queue)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        print(f"Planning agent (from context) error: {tb}")
        try:
            await _safe_send(ws, {
                "type": "planning_error",
                "session_id": session_id,
                "message": str(e),
            })
        except Exception:
            pass
