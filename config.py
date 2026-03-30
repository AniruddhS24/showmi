import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
    llm_model: str = os.getenv("LLM_MODEL", "default")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.6"))
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    cdp_url: str = os.getenv("CDP_URL", "")
    chrome_profile_dir: str = os.getenv(
        "CHROME_PROFILE_DIR",
        os.path.expanduser("~/Library/Application Support/Google/Chrome"),
    )
    headless: bool = os.getenv("HEADLESS", "false").lower() == "true"
    max_steps: int = int(os.getenv("MAX_STEPS", "100"))
    max_actions_per_step: int = int(os.getenv("MAX_ACTIONS_PER_STEP", "4"))
    max_failures: int = int(os.getenv("MAX_FAILURES", "3"))
    use_vision: bool = os.getenv("USE_VISION", "true").lower() == "true"
    require_confirmation: bool = os.getenv("REQUIRE_CONFIRMATION", "false").lower() == "true"


config = Config()
