from browser_use import Agent, Browser
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.openai.chat import ChatOpenAI

from config import config
from db import get_identity_text, get_memory_text
from hooks import load_workflows, on_step_end, on_step_start


def _make_browser(cfg=None) -> Browser:
    """Create a Browser instance, using CDP if configured or launching with profile."""
    cfg = cfg or config
    if cfg.cdp_url:
        return Browser(cdp_url=cfg.cdp_url)
    profile = BrowserProfile(
        user_data_dir=cfg.chrome_profile_dir,
        headless=cfg.headless,
        channel="chrome",
        window_size={"width": 1280, "height": 900},
        window_position={"width": 800, "height": 0},
    )
    return Browser(browser_profile=profile)


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


async def run_agent(task: str) -> None:
    """Run the browser-use agent on the given task."""
    print(f"Task: {task}")

    browser = _make_browser()

    llm = ChatOpenAI(
        base_url=config.llm_base_url,
        model=config.llm_model,
        temperature=config.llm_temperature,
        api_key=config.llm_api_key,
    )

    system_message = _build_system_message()
    if system_message:
        workflow_count = system_message.count("## Workflow:")
        if workflow_count:
            print(f"Loaded {workflow_count} workflow(s)")

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        extend_system_message=system_message,
        max_actions_per_step=config.max_actions_per_step,
        max_failures=config.max_failures,
        use_vision=config.use_vision,
    )

    print("Running agent...\n")
    history = await agent.run(
        max_steps=config.max_steps,
        on_step_start=on_step_start,
        on_step_end=on_step_end,
    )

    # Final summary
    print(f"\n{'='*60}")
    print("Agent finished")
    print(f"{'='*60}")
    print(f"Steps taken: {len(history.history)}")
    if history.final_result():
        print(f"Result: {history.final_result()}")
    if history.errors():
        print(f"Errors: {history.errors()}")
