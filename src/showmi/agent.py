from browser_use import Agent, Browser
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.openai.chat import ChatOpenAI

from .config import _parse_use_vision, config
from .hooks import on_step_end, on_step_start

def _make_browser(cfg=None) -> Browser:
    """Create a Browser instance, using CDP if configured or launching with profile."""
    cfg = cfg or config
    if cfg.cdp_url:
        return Browser(cdp_url=cfg.cdp_url)
    profile = BrowserProfile(
        user_data_dir=cfg.chrome_profile_dir,
        headless=cfg.headless,
        channel="chrome",
        minimum_wait_page_load_time=0.1,
        wait_between_actions=0.1,
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

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        extend_system_message=None,
        max_actions_per_step=config.max_actions_per_step,
        max_failures=config.max_failures,
        use_vision=_parse_use_vision(config.use_vision),
        flash_mode=config.flash_mode,
        use_thinking=config.use_thinking,
        vision_detail_level=config.vision_detail_level,
        max_history_items=config.max_history_items or None,
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
