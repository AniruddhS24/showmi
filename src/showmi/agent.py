from browser_use import Agent, Browser
from browser_use.llm.openai.chat import ChatOpenAI

from .config import _parse_use_vision, config
from .hooks import on_step_end, on_step_start


PROXY_CDP_URL = "ws://localhost:8765/devtools/browser/showmi"


def _make_browser(cfg=None) -> Browser:
    """Create a Browser that talks to the user's active tab via the showmi
    CDP proxy, which forwards commands to the Chrome extension's
    chrome.debugger session on that tab.
    """
    cfg = cfg or config
    if getattr(cfg, "attach_tab_id", None) is None:
        raise RuntimeError(
            "No tab attached. Open the Showmi sidepanel and click Attach to pick a tab."
        )
    return Browser(cdp_url=PROXY_CDP_URL)


async def run_agent(task: str) -> None:
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

    print(f"\n{'='*60}")
    print("Agent finished")
    print(f"{'='*60}")
    print(f"Steps taken: {len(history.history)}")
    if history.final_result():
        print(f"Result: {history.final_result()}")
    if history.errors():
        print(f"Errors: {history.errors()}")
