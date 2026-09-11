import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

def _resolve_data_dir() -> Path:
    # 1. Custom environment override
    if os.environ.get("JOBAGENT_HOME"):
        p = Path(os.environ["JOBAGENT_HOME"])
        p.mkdir(parents=True, exist_ok=True)
        (p / "config").mkdir(parents=True, exist_ok=True)
        return p

    # 2. Local development source checkout (must have git or tests and not be in site-packages)
    is_installed = "site-packages" in str(BASE_DIR).lower() or "dist-packages" in str(BASE_DIR).lower()
    if not is_installed and ((BASE_DIR / ".git").exists() or (BASE_DIR / "tests").exists()):
        return BASE_DIR

    # 3. User home directory ~/.jobagent for global pip installs
    user_dir = Path.home() / ".jobagent"
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "config").mkdir(parents=True, exist_ok=True)
    return user_dir

DATA_DIR = _resolve_data_dir()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(DATA_DIR / ".env") if (DATA_DIR / ".env").exists() else str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Data and Base directories
    DATA_DIR: Path = DATA_DIR
    BASE_DIR: Path = BASE_DIR

    # Runtime & Stealth settings
    HEADLESS: bool = False
    USER_DATA_DIR: str = str(DATA_DIR / ".browser_context")
    SLOW_MO_MS: int = 150
    TIMEOUT_MS: int = 30000
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 800

    # Persistence
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DATA_DIR / 'applications.db'}"

    # Profile & Resume Paths
    PROFILE_PATH: str = str(DATA_DIR / "config" / "candidate_profile.json")
    RESUME_PATH: str = str(DATA_DIR / "config" / "resume.pdf")

    # LLM Settings
    LLM_PROVIDER: str = "auto"  # 'auto', 'openai', 'gemini', 'anthropic', or 'heuristic'
    LLM_MODEL: str = "gemini-3.1-flash-lite"
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.1

    # Search & Fit Defaults
    DEFAULT_KEYWORD: str = "QA Automation Engineer"
    DEFAULT_LOCATION: str = "Hyderabad"
    DEFAULT_MAX_JOBS: int = 10
    DEFAULT_MIN_SCORE: int = 70
    REQUIRE_HUMAN_APPROVAL: bool = True

settings = Settings()

def get_browser_user_data_dir() -> Path:
    """
    Resolves the persistent browser profile directory.
    Checks:
    1. Local current working directory .browser_context (if profile exists)
    2. D:/Gravity/.browser_context (if profile exists on dev machine)
    3. Centralized settings.USER_DATA_DIR (e.g. ~/.jobagent/.browser_context)
    """
    local_dir = Path(".browser_context").resolve()
    if (local_dir / "Default").exists():
        return local_dir

    gravity_dir = Path("D:/Gravity/.browser_context")
    if gravity_dir.exists() and (gravity_dir / "Default").exists():
        return gravity_dir

    central_dir = Path(settings.USER_DATA_DIR).resolve()
    central_dir.mkdir(parents=True, exist_ok=True)
    return central_dir

