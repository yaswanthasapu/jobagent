import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

def _resolve_data_dir() -> Path:
    # 1. Custom environment override
    if os.environ.get("JOBAGENT_HOME"):
        p = Path(os.environ["JOBAGENT_HOME"]).resolve()
        p.mkdir(parents=True, exist_ok=True)
        (p / "config").mkdir(parents=True, exist_ok=True)
        return p

    # 2. Local development checkout: actively running inside the repository directory
    is_installed = "site-packages" in str(BASE_DIR).lower() or "dist-packages" in str(BASE_DIR).lower()
    if not is_installed and Path.cwd().resolve() == BASE_DIR.resolve():
        return BASE_DIR

    # 3. Dedicated per-user directory in user's home folder (~/.jobagent)
    # Guarantees 100% data and profile isolation across different OS users and external users.
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
    LLM_MODEL: str = "gemini-3.8-flash"
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    LLM_TEMPERATURE: float = 0.1

    # Search & Fit Defaults
    DEFAULT_KEYWORD: str = "Software Engineer"
    DEFAULT_LOCATION: str = "Remote"
    DEFAULT_MAX_JOBS: int = 10
    DEFAULT_MIN_SCORE: int = 70
    REQUIRE_HUMAN_APPROVAL: bool = True

settings = Settings()

def get_browser_user_data_dir() -> Path:
    """
    Resolves the persistent browser profile directory.
    Checks:
    1. Project BASE_DIR / .browser_context (only if actively running inside local dev repository)
    2. Centralized settings.USER_DATA_DIR (e.g. ~/.jobagent/.browser_context)
    """
    is_installed = "site-packages" in str(BASE_DIR).lower() or "dist-packages" in str(BASE_DIR).lower()
    if not is_installed and Path.cwd().resolve() == BASE_DIR.resolve():
        local_dir = Path(".browser_context").resolve()
        if (local_dir / "Default").exists():
            return local_dir

        base_dir_context = (BASE_DIR / ".browser_context").resolve()
        if base_dir_context.exists() and (base_dir_context / "Default").exists():
            return base_dir_context

    central_dir = Path(settings.USER_DATA_DIR).resolve()
    central_dir.mkdir(parents=True, exist_ok=True)
    return central_dir

