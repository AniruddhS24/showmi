"""Utilities for workflow file I/O, frontmatter parsing, and LLM-based compilation."""

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from browser_use.llm.anthropic.chat import ChatAnthropic as BrowserUseChatAnthropic
from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI

from db import WORKFLOWS_DIR

PARAM_RE = re.compile(r"\{\{(\w+)\}\}")

COMPILE_SYSTEM_PROMPT = """\
You are a workflow compiler for a browser automation agent. You receive raw DOM event \
recordings from a browser extension and must produce clean, semantic workflow instructions \
that a browser automation agent can follow.

Your output must be a single markdown document with YAML frontmatter in EXACTLY this format:

```
---
name: <workflow name>
description: <one-line description>
parameters:
  - name: <param_name>
    description: <what this parameter is>
    default: "<optional default value>"
---

# <Workflow Title>

## Steps

1. <step>
2. <step>
...

## Notes

- <any helpful notes>
```

Rules:
- Convert low-level DOM events into high-level semantic steps \
(e.g., "Click the Search button" not "Click element #yDmH0d > div.btn").
- Use element text content and aria-labels to describe targets, NOT CSS selectors.
- Merge related events (e.g., click on input + typing = "Type X into the Y field").
- Keep steps concise but unambiguous.
- Add helpful notes about timing, popups, or error-prone steps if relevant.
- Output ONLY the markdown document, no extra commentary.
"""

PARAMETERIZE_ADDENDUM = """
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


def list_workflows() -> list[dict]:
    """Read all workflow files and return parsed metadata + content."""
    if not WORKFLOWS_DIR.exists():
        return []
    results = []
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
                "body": body,
                "file_content": content,
            }
        )
    return results


def get_workflow(workflow_id: str) -> dict | None:
    """Read and parse a single workflow by slug. Returns None if not found."""
    path = WORKFLOWS_DIR / f"{workflow_id}.md"
    if not path.exists():
        return None
    content = path.read_text().strip()
    meta, body = parse_frontmatter(content)
    return {
        "id": workflow_id,
        "name": meta.get("name", workflow_id),
        "description": meta.get("description", ""),
        "parameters": meta.get("parameters", []),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "body": body,
        "file_content": content,
    }


def save_workflow(data: dict, workflow_id: str | None = None) -> str:
    """Write a workflow file. Returns the slug ID.

    Accepts either:
    - file_content: raw markdown string to write directly
    - name + body (+ optional description, parameters): builds the file from parts
    """
    now = datetime.now(timezone.utc).isoformat()

    if data.get("file_content"):
        # Direct write mode — parse to extract/update metadata
        meta, body = parse_frontmatter(data["file_content"])
        wf_id = workflow_id or slugify(meta.get("name", "untitled"))
        if "updated_at" not in meta:
            meta["updated_at"] = now
        if "created_at" not in meta:
            meta["created_at"] = now
        file_content = render_frontmatter(meta, body)
    else:
        # Structured mode
        name = data.get("name", "Untitled Workflow")
        wf_id = workflow_id or slugify(name)

        # If updating, preserve created_at from existing file
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

    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    (WORKFLOWS_DIR / f"{wf_id}.md").write_text(file_content)
    return wf_id


def delete_workflow(workflow_id: str) -> bool:
    """Delete a workflow file. Returns True if it existed."""
    path = WORKFLOWS_DIR / f"{workflow_id}.md"
    if not path.exists():
        return False
    path.unlink()
    return True


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


async def compile_recording(
    recording: dict,
    name: str,
    description: str,
    auto_parameterize: bool,
    llm_settings: dict,
) -> dict:
    """Call the LLM to compile raw DOM events into a semantic workflow.

    Returns a dict with: name, description, parameters, body, file_content.
    Raises ValueError if the LLM output cannot be parsed.
    """
    import json as _json

    system = COMPILE_SYSTEM_PROMPT
    if auto_parameterize:
        system += PARAMETERIZE_ADDENDUM

    user_msg = f"Here is the raw recording:\n{_json.dumps(recording, indent=2)}\n\n"
    user_msg += f'The user named this workflow: "{name}"\n'
    if description:
        user_msg += f"Description: {description}\n"

    llm = _make_llm(llm_settings)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    response = await llm.ainvoke(messages)
    raw_output = response.content if hasattr(response, "content") else str(response)

    # Strip markdown code fences if the LLM wrapped the output
    cleaned = raw_output.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (```markdown or ```)
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
