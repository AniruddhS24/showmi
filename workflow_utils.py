"""Utilities for workflow file I/O, frontmatter parsing, and LLM-based compilation."""

import base64
import io
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
from browser_use.llm.messages import SystemMessage, UserMessage
from PIL import Image

from db import WORKFLOWS_DIR

PARAM_RE = re.compile(r"\{\{(\w+)\}\}")

COMPILE_SYSTEM_PROMPT = """\
You are a workflow compiler for a browser automation agent powered by browser-use. You receive \
a raw recorded browser demonstration (DOM events with surrounding HTML context, screenshots, \
and optional audio narration) and must produce a clean, reusable workflow document.

CRITICAL: The raw recording may have 20-50+ low-level events. You must COMPRESS these into \
5-12 meaningful high-level steps. Merge related events aggressively:
- click on input + typing → single "input_text" step
- click on button → single "click_element" step
- navigation + page load → single "go_to_url" step
- scrolling → omit unless critical to the workflow
- redundant clicks (e.g., click then re-click) → single step
Select only the steps that are ESSENTIAL to reproduce the workflow. A good workflow \
has 5-12 steps, not 20+.

## browser-use actions

Use ONLY these action names (these are the actual actions the agent can perform):
- go_to_url: Navigate to a URL
- click_element: Click an element (describe by visible text, aria-label, or role)
- input_text: Type text into a form field
- send_keys: Press keyboard keys (e.g., "Enter", "Tab Tab Enter", "Escape", "ArrowDown")
- select_dropdown_option: Select from a dropdown menu
- scroll_down / scroll_up: Scroll the page
- scroll_to_text: Scroll until specific text is visible
- extract_page_content: Extract text or data from the page
- go_back: Navigate back
- wait: Wait for a condition

Your output must be a single markdown document with YAML frontmatter in EXACTLY this format:

```
---
name: <workflow_name>
description: <1-2 sentence description of what this workflow accomplishes>
parameters:
  - name: <param_name>
    description: <what this parameter is>
    default: "<optional default value>"
---

## Description

<2-3 sentences explaining the full workflow: what site it operates on, what it accomplishes, \
and when/why you'd use it.>

## Guidelines

- <Rule or tip for executing this workflow successfully>
- <Edge case to watch out for>
- <Timing/waiting notes if relevant>

## Steps

Follow these steps to complete "{{name}}":

### Step 1
go_to_url: <starting URL with {{param}} placeholders>

### Step 2
![screenshot](step-2.jpg)
click_element: Click "<element described by visible text, aria-label, or role>"
Fallback: send_keys "Tab Tab Enter" to reach and activate the element

### Step 3
![screenshot](step-3.jpg)
input_text: Type "{{param}}" into "<element described by placeholder or label>"
Fallback: Click the field first, then type

...

## Error Recovery

- If an element is not visible, use scroll_down to find it
- If a click fails, use send_keys with Tab navigation as fallback
- If a page doesn't load, use go_to_url to retry from the last known URL
- <Any workflow-specific recovery steps>

This marks the end of the workflow.
```

Rules:
- COMPRESS raw events into 5-12 high-level steps. Each step = one meaningful user action.
- Step 1 must ALWAYS be go_to_url with the starting URL.
- Name actions using the exact browser-use action names listed above.
- Describe elements using visible text, aria-label, role, or placeholder \
— NEVER use CSS selectors or element IDs.
- Use the DOM context provided with each event to write accurate element descriptions.
- Include a Fallback line for steps that involve clicking or interacting with specific elements.
- The screenshot reference ![screenshot](step-N.jpg) will be resolved to actual images later.
- If audio narration is provided, use it to understand user intent, add context to the \
Description section, and inform the Guidelines.
- The Guidelines section should capture edge cases, timing, error-prone steps, or \
prerequisites the user mentioned.
- Output ONLY the markdown document, no extra commentary.
- Detect values that look like user-specific data (names, dates, cities, URLs, amounts, \
search queries, etc.) and replace them with {{parameter_name}} placeholders.
- List every parameter you introduce in the YAML frontmatter `parameters` array.
- Use descriptive snake_case names for parameters.
"""


# ── Frontmatter parsing ──


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split a markdown file into YAML metadata dict and body string.

    Returns ({}, full_content) if no frontmatter is present.
    """
    content = content.strip()
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    yaml_block = content[3:end].strip()
    body = content[end + 3 :].strip()

    try:
        meta = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return {}, content

    return meta, body


def render_frontmatter(meta: dict, body: str) -> str:
    """Produce full file content from metadata dict and body string."""
    yaml_str = yaml.dump(meta, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{yaml_str}\n---\n\n{body.strip()}\n"


# ── Slugification ──


def slugify(name: str) -> str:
    """Convert a workflow name to a filename-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


# ── Workflow file I/O ──


def _workflow_md_path(workflow_id: str) -> Path:
    """Path to workflow.md inside a workflow directory."""
    return WORKFLOWS_DIR / workflow_id / "workflow.md"


def _find_workflow_path(workflow_id: str) -> Path | None:
    """Find workflow file — supports both directory and legacy single-file format."""
    dir_path = _workflow_md_path(workflow_id)
    if dir_path.exists():
        return dir_path
    # Legacy: single .md file
    legacy = WORKFLOWS_DIR / f"{workflow_id}.md"
    if legacy.exists():
        return legacy
    return None


def _load_manifest_workflow(wf_dir: Path) -> dict | None:
    """Load a workflow from a directory with manifest.yaml + workflow.md.

    Also supports legacy manifest.yaml + workflow.py (playwright format, currently disabled).
    """
    manifest_path = wf_dir / "manifest.yaml"
    md_path = wf_dir / "workflow.md"
    if not manifest_path.exists():
        return None
    # Need at least a workflow.md to be useful
    if not md_path.exists():
        return None
    try:
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError:
        return None
    md_content = md_path.read_text()
    meta, body = parse_frontmatter(md_content)
    screenshots = sorted(wf_dir.glob("step-*.jpg"))
    return {
        "id": wf_dir.name,
        "name": manifest.get("name", wf_dir.name),
        "description": manifest.get("description", ""),
        "parameters": manifest.get("parameters", []),
        "created_at": manifest.get("created_at", ""),
        "updated_at": manifest.get("updated_at", ""),
        "format": "markdown",
        "body": body,
        "file_content": md_content,
        "screenshot_paths": [str(s) for s in screenshots],
    }


def list_workflows() -> list[dict]:
    """Read all workflows and return parsed metadata + content."""
    if not WORKFLOWS_DIR.exists():
        return []
    results = []

    # Directory-based workflows
    for d in sorted(WORKFLOWS_DIR.iterdir()):
        if not d.is_dir():
            continue

        # Check for manifest.yaml + workflow.md format first
        pw = _load_manifest_workflow(d)
        if pw:
            results.append(pw)
            continue

        # Legacy markdown format (workflow.md)
        md_path = d / "workflow.md"
        if not md_path.exists():
            continue
        content = md_path.read_text().strip()
        if not content:
            continue
        meta, body = parse_frontmatter(content)
        screenshots = sorted(d.glob("step-*.jpg"))
        results.append(
            {
                "id": d.name,
                "name": meta.get("name", d.name),
                "description": meta.get("description", ""),
                "parameters": meta.get("parameters", []),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "format": "markdown",
                "body": body,
                "file_content": content,
                "screenshot_paths": [str(s) for s in screenshots],
            }
        )

    # Legacy single-file workflows
    for f in sorted(WORKFLOWS_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        content = f.read_text().strip()
        if not content:
            continue
        meta, body = parse_frontmatter(content)
        results.append(
            {
                "id": f.stem,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "parameters": meta.get("parameters", []),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "format": "markdown",
                "body": body,
                "file_content": content,
                "screenshot_paths": [],
            }
        )
    return results


def get_workflow(workflow_id: str) -> dict | None:
    """Read and parse a single workflow by slug or name. Returns None if not found."""
    # Check manifest format first (manifest.yaml + workflow.md)
    wf_dir = WORKFLOWS_DIR / workflow_id
    if wf_dir.is_dir():
        pw = _load_manifest_workflow(wf_dir)
        if pw:
            return pw

    # Fall back to markdown format
    path = _find_workflow_path(workflow_id)
    if not path:
        slug = slugify(workflow_id)
        path = _find_workflow_path(slug)
        if path:
            workflow_id = slug
    if not path:
        needle = workflow_id.lower().strip()
        for wf in list_workflows():
            if wf.get("name", "").lower().strip() == needle:
                return get_workflow(wf["id"])
        return None
    content = path.read_text().strip()
    meta, body = parse_frontmatter(content)
    screenshots = []
    if path.parent.name == workflow_id:
        screenshots = sorted(path.parent.glob("step-*.jpg"))
    return {
        "id": workflow_id,
        "name": meta.get("name", workflow_id),
        "description": meta.get("description", ""),
        "parameters": meta.get("parameters", []),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "format": "markdown",
        "body": body,
        "file_content": content,
        "screenshot_paths": [str(s) for s in screenshots],
    }


def save_workflow(
    data: dict,
    workflow_id: str | None = None,
    screenshots: dict[int, bytes] | None = None,
) -> str:
    """Write a workflow directory. Returns the slug ID.

    Args:
        data: Workflow data (file_content or name+body+description+parameters)
        workflow_id: Optional slug override
        screenshots: Optional dict of step_number -> JPEG bytes
    """
    now = datetime.now(timezone.utc).isoformat()

    if data.get("file_content"):
        meta, body = parse_frontmatter(data["file_content"])
        wf_id = workflow_id or slugify(meta.get("name", "untitled"))
        if "updated_at" not in meta:
            meta["updated_at"] = now
        if "created_at" not in meta:
            meta["created_at"] = now
        file_content = render_frontmatter(meta, body)
    else:
        name = data.get("name", "Untitled Workflow")
        wf_id = workflow_id or slugify(name)

        existing = get_workflow(wf_id) if workflow_id else None
        created_at = now
        if existing:
            meta_existing, _ = parse_frontmatter(existing["file_content"])
            created_at = meta_existing.get("created_at", now)

        meta = {
            "name": name,
            "description": data.get("description", ""),
            "parameters": data.get("parameters", []),
            "created_at": created_at,
            "updated_at": now,
        }
        body = data.get("body", f"# {name}\n")
        file_content = render_frontmatter(meta, body)

    # Create workflow directory
    wf_dir = WORKFLOWS_DIR / wf_id
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "workflow.md").write_text(file_content)

    # Save screenshots
    if screenshots:
        for step_num, jpeg_bytes in screenshots.items():
            (wf_dir / f"step-{step_num}.jpg").write_bytes(jpeg_bytes)

    # Clean up legacy single-file if it exists
    legacy = WORKFLOWS_DIR / f"{wf_id}.md"
    if legacy.exists():
        legacy.unlink()

    return wf_id


def save_playwright_workflow(
    slug: str,
    manifest: dict,
    script_code: str,
    screenshots: dict[int, bytes] | None = None,
) -> str:
    """Write a playwright-format workflow directory. Returns the slug ID.

    Args:
        slug: Workflow slug ID
        manifest: Dict with name, description, parameters, etc.
        script_code: Python source code for workflow.py
        screenshots: Optional dict of step_number -> JPEG bytes
    """
    now = datetime.now(timezone.utc).isoformat()
    manifest.setdefault("created_at", now)
    manifest.setdefault("updated_at", now)

    wf_dir = WORKFLOWS_DIR / slug
    wf_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest.yaml
    yaml_str = yaml.dump(manifest, default_flow_style=False, sort_keys=False)
    (wf_dir / "manifest.yaml").write_text(yaml_str)

    # Write workflow.py
    (wf_dir / "workflow.py").write_text(script_code)

    # Remove legacy workflow.md if present
    legacy_md = wf_dir / "workflow.md"
    if legacy_md.exists():
        legacy_md.unlink()

    # Save screenshots
    if screenshots:
        for step_num, jpeg_bytes in screenshots.items():
            (wf_dir / f"step-{step_num}.jpg").write_bytes(jpeg_bytes)

    return slug


def delete_workflow(workflow_id: str) -> bool:
    """Delete a workflow directory (or legacy file). Returns True if it existed."""
    # Directory format
    wf_dir = WORKFLOWS_DIR / workflow_id
    if wf_dir.is_dir():
        shutil.rmtree(wf_dir)
        return True
    # Legacy single file
    legacy = WORKFLOWS_DIR / f"{workflow_id}.md"
    if legacy.exists():
        legacy.unlink()
        return True
    return False


# ── LLM compilation ──


def _make_llm(settings: dict):
    """Build an LLM client from settings dict (same pattern as run_agent_ws)."""
    provider = settings.get("provider", "local")
    if provider == "anthropic":
        return BrowserUseChatAnthropic(
            model=settings.get("model", ""),
            temperature=float(settings.get("temperature", 0.5)),
            api_key=settings.get("api_key", ""),
        )
    else:
        return BrowserUseChatOpenAI(
            base_url=settings.get("base_url", ""),
            model=settings.get("model", ""),
            temperature=float(settings.get("temperature", 0.5)),
            api_key=settings.get("api_key", "not-needed"),
        )


def _screenshot_thumbnail(data_url: str, size: tuple[int, int] = (64, 64)) -> list[int] | None:
    """Decode a data-URL screenshot into a flat list of grayscale pixel values."""
    try:
        b64 = data_url.split(",", 1)[1] if "," in data_url else ""
        if not b64:
            return None
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        img = img.convert("L").resize(size, Image.NEAREST)
        return list(img.getdata())
    except Exception:
        return None


def _pixel_similarity(a: list[int], b: list[int]) -> float:
    """Mean absolute pixel difference normalized to 0-1 (0 = identical, 1 = opposite)."""
    total = sum(abs(x - y) for x, y in zip(a, b))
    return 1.0 - (total / (len(a) * 255))


def _dedup_screenshots(events: list[dict], similarity_threshold: float = 0.95) -> list[dict]:
    """Collapse runs of events with near-identical screenshots, keeping the last in each group.

    Events without screenshots pass through unchanged.
    """
    if not events:
        return events

    # Group consecutive events by screenshot similarity
    groups: list[list[int]] = []  # each group is a list of event indices
    current_group: list[int] = []
    prev_thumb: list[int] | None = None

    for i, event in enumerate(events):
        screenshot = event.get("screenshot", "")
        if not screenshot:
            # No screenshot — flush current group, add this event standalone
            if current_group:
                groups.append(current_group)
                current_group = []
            groups.append([i])
            prev_thumb = None
            continue

        thumb = _screenshot_thumbnail(screenshot)
        if thumb is None:
            if current_group:
                groups.append(current_group)
                current_group = []
            groups.append([i])
            prev_thumb = None
            continue

        if prev_thumb is not None and _pixel_similarity(prev_thumb, thumb) >= similarity_threshold:
            # Similar to previous — extend the group
            current_group.append(i)
        else:
            # New visual state — flush and start new group
            if current_group:
                groups.append(current_group)
            current_group = [i]

        prev_thumb = thumb

    if current_group:
        groups.append(current_group)

    # From each group, keep the last event (final state after interactions)
    result = []
    for group in groups:
        result.append(events[group[-1]])

    if len(result) < len(events):
        print(f"Screenshot dedup: {len(events)} events → {len(result)} (dropped {len(events) - len(result)} near-duplicates)")

    return result


def _prefilter_events(events: list[dict], max_events: int = 30) -> list[dict]:
    """Deduplicate and filter raw recording events before sending to LLM.

    Merges click+input pairs on the same target, drops scroll/redundant events,
    and caps the total to max_events by sampling evenly.
    """
    filtered = []
    skip_next_input = False

    for i, event in enumerate(events):
        etype = event.get("type", "")

        # Skip scroll events entirely
        if etype == "scroll":
            continue

        if skip_next_input:
            skip_next_input = False
            if etype in ("input", "change"):
                continue

        # Merge click followed by input/change on same target into one event
        if etype == "click" and i + 1 < len(events):
            next_ev = events[i + 1]
            next_type = next_ev.get("type", "")
            if next_type in ("input", "change"):
                same_target = (
                    event.get("target", {}).get("selector")
                    == next_ev.get("target", {}).get("selector")
                )
                if same_target and next_ev.get("value"):
                    # Merge: use the input event but label as click+type
                    merged = {**next_ev, "type": "click+type"}
                    if event.get("screenshot") and not next_ev.get("screenshot"):
                        merged["screenshot"] = event["screenshot"]
                    if event.get("dom_context") and not next_ev.get("dom_context"):
                        merged["dom_context"] = event["dom_context"]
                    filtered.append(merged)
                    skip_next_input = True
                    continue

        # Drop duplicate consecutive clicks on the same target
        if etype == "click" and filtered:
            prev = filtered[-1]
            if (
                prev.get("type") in ("click", "click+type")
                and prev.get("target", {}).get("selector")
                == event.get("target", {}).get("selector")
                and not event.get("value")
            ):
                continue

        filtered.append(event)

    # Collapse runs of visually identical screenshots (keep last in each group)
    filtered = _dedup_screenshots(filtered)

    # If still over max_events, sample evenly (keep first, last, and spread)
    if len(filtered) > max_events:
        step = len(filtered) / max_events
        indices = sorted(set([0, len(filtered) - 1] + [int(i * step) for i in range(max_events)]))
        filtered = [filtered[i] for i in indices[:max_events]]

    return filtered


def _format_recording_for_llm(recording: dict, audio_transcript: str = "") -> str:
    """Format a recording into a readable summary for the LLM, including DOM context.

    Expects events to already be filtered/curated — does not apply prefiltering.
    """
    parts = []
    parts.append(f"Start URL: {recording.get('start_url', 'unknown')}")

    if audio_transcript:
        parts.append(f"\nUser narration transcript:\n{audio_transcript}")

    events = recording.get("events", [])
    parts.append(f"\nRecorded events ({len(events)}):")
    for i, event in enumerate(events, 1):
        target = event.get("target", {})
        desc = target.get("aria_label") or target.get("text") or target.get("selector", "")
        line = f"\n--- Event {i}: {event.get('type', '?')} ---"
        line += f"\n  URL: {event.get('url', '')}"
        line += f"\n  Target: {desc}"
        if event.get("value"):
            line += f"\n  Value: {event['value']}"
        if event.get("dom_context"):
            line += f"\n  Surrounding elements:\n{event['dom_context']}"
        if event.get("screenshot"):
            line += "\n  [Screenshot captured]"
        parts.append(line)

    return "\n".join(parts)


async def compile_recording(
    recording: dict,
    name: str,
    description: str,
    auto_parameterize: bool,
    llm_settings: dict,
    audio_transcript: str = "",
) -> dict:
    """Call the LLM to compile raw DOM events into a semantic workflow.

    Returns a dict with: name, description, parameters, body, file_content.
    Raises ValueError if the LLM output cannot be parsed.
    """
    system = COMPILE_SYSTEM_PROMPT

    # Pre-filter events for the one-shot compile path
    filtered_recording = {**recording, "events": _prefilter_events(recording.get("events", []))}
    formatted = _format_recording_for_llm(filtered_recording, audio_transcript)
    user_msg = f"{formatted}\n\nThe user named this workflow: \"{name}\"\n"
    if description:
        user_msg += f"Description: {description}\n"

    llm = _make_llm(llm_settings)
    messages = [
        SystemMessage(content=system),
        UserMessage(content=user_msg),
    ]
    response = await llm.ainvoke(messages)
    raw_output = response.completion if hasattr(response, "completion") else str(response)

    # Strip markdown code fences if the LLM wrapped the output
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[: -len("```")]
    cleaned = cleaned.strip()

    meta, body = parse_frontmatter(cleaned)
    if not meta:
        raise ValueError(raw_output)

    # Ensure required fields
    meta.setdefault("name", name)
    meta.setdefault("description", description)
    meta.setdefault("parameters", [])
    now = datetime.now(timezone.utc).isoformat()
    meta.setdefault("created_at", now)
    meta.setdefault("updated_at", now)

    file_content = render_frontmatter(meta, body)

    return {
        "name": meta["name"],
        "description": meta["description"],
        "parameters": meta["parameters"],
        "body": body,
        "file_content": file_content,
    }


def extract_screenshots_from_recording(recording: dict) -> dict[int, bytes]:
    """Extract ALL screenshots from recording events, keyed by raw event index (1-based).

    Returns dict of event_index -> JPEG bytes.
    """
    screenshots = {}
    for i, event in enumerate(recording.get("events", []), 1):
        screenshot_data = event.get("screenshot", "")
        if screenshot_data and screenshot_data.startswith("data:"):
            b64 = screenshot_data.split(",", 1)[1] if "," in screenshot_data else ""
            if b64:
                try:
                    screenshots[i] = base64.b64decode(b64)
                except Exception:
                    pass
    return screenshots


STEP_IMG_RE = re.compile(r"!\[screenshot\]\(step-(\d+)\.jpg\)")


def select_workflow_screenshots(
    workflow_content: str, all_screenshots: dict[int, bytes], max_screenshots: int = 10
) -> tuple[str, dict[int, bytes]]:
    """Match screenshot references in workflow markdown to available screenshots.

    The planning agent writes ![screenshot](step-N.jpg) where N references raw event
    indices. This function:
    1. Finds all step-N.jpg references in the markdown
    2. Keeps only screenshots that are referenced (capped at max_screenshots)
    3. Renumbers them contiguously (step-1, step-2, ...)
    4. Updates the markdown references to match

    Returns (updated_workflow_content, {new_step_number: jpeg_bytes}).
    """
    # Find all referenced step numbers in order of appearance
    referenced = []
    for m in STEP_IMG_RE.finditer(workflow_content):
        n = int(m.group(1))
        if n not in referenced:
            referenced.append(n)

    # Filter to only those we have screenshots for
    available = [n for n in referenced if n in all_screenshots]

    # Cap at max_screenshots — keep evenly spaced if over limit
    if len(available) > max_screenshots:
        step = len(available) / max_screenshots
        indices = sorted(set([0, len(available) - 1] + [int(i * step) for i in range(max_screenshots)]))
        available = [available[i] for i in indices[:max_screenshots]]

    # Build old→new mapping and collect selected screenshots
    old_to_new = {}
    selected = {}
    for new_num, old_num in enumerate(available, 1):
        old_to_new[old_num] = new_num
        selected[new_num] = all_screenshots[old_num]

    # Update markdown: renumber referenced screenshots, remove unreferenced step images
    def replace_ref(m):
        old_n = int(m.group(1))
        if old_n in old_to_new:
            return f"![screenshot](step-{old_to_new[old_n]}.jpg)"
        # Screenshot not selected — remove the image reference line
        return ""

    updated = STEP_IMG_RE.sub(replace_ref, workflow_content)
    # Clean up any blank lines left by removed references
    updated = re.sub(r"\n{3,}", "\n\n", updated)

    return updated, selected
