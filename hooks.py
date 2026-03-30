import json
from datetime import datetime, timezone
from pathlib import Path

from config import config

LOGS_DIR = Path(__file__).parent / "logs"
SKILLS_DIR = Path(__file__).parent / "skills"


def load_skills() -> str:
    """Read all .md files from skills/ and return concatenated content."""
    if not SKILLS_DIR.exists():
        return ""
    skill_files = sorted(SKILLS_DIR.glob("*.md"))
    if not skill_files:
        return ""
    parts = []
    for f in skill_files:
        if f.name == "README.md":
            continue
        content = f.read_text().strip()
        if content:
            parts.append(f"## Skill: {f.stem}\n\n{content}")
    if not parts:
        return ""
    return "# Available Skills\n\n" + "\n\n---\n\n".join(parts)


async def on_step_start(agent) -> None:
    """Log step start. Optionally gate on user confirmation."""
    step = agent.state.n_steps
    print(f"\n{'='*60}")
    print(f"Step {step}")
    print(f"{'='*60}")

    # Show what the agent is planning
    model_output = agent.state.last_model_output
    if model_output:
        if model_output.next_goal:
            print(f"Goal: {model_output.next_goal}")

    if config.require_confirmation:
        try:
            answer = input("Proceed? [Y/n] ").strip().lower()
            if answer in ("n", "no"):
                print("Stopping agent (user declined).")
                agent.state.stopped = True
        except (EOFError, KeyboardInterrupt):
            print("\nStopping agent.")
            agent.state.stopped = True


async def on_step_end(agent) -> None:
    """Log step results and append to JSONL event log."""
    step = agent.state.n_steps
    results = agent.state.last_result or []
    model_output = agent.state.last_model_output

    # Get action names from model output
    action_names = []
    if model_output and model_output.action:
        for action in model_output.action:
            # ActionModel is a pydantic model with dynamic action fields
            action_data = action.model_dump(exclude_none=True)
            action_names.append(action_data)

    # Print results
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

    # Append JSONL event log
    LOGS_DIR.mkdir(exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "url": url,
        "actions": [
            {
                "action": action_names[i] if i < len(action_names) else None,
                "error": r.error,
                "is_done": r.is_done,
                "extracted": r.extracted_content,
            }
            for i, r in enumerate(results)
        ],
    }
    with open(LOGS_DIR / "events.jsonl", "a") as f:
        f.write(json.dumps(event, default=str) + "\n")
