from browser_use import Agent, Browser
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.openai.chat import ChatOpenAI

from config import config
from hooks import load_skills, on_step_end, on_step_start


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

    # Load any available skills
    skills_text = load_skills()
    if skills_text:
        print(f"Loaded {skills_text.count('## Skill:')} skill(s)")

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        extend_system_message=skills_text or None,
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
