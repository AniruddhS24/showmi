"""Utilities for workflow file I/O and frontmatter parsing."""

import base64
import io
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

from .db import WORKFLOWS_DIR



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



