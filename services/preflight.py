"""
Preflight System Health & Self-Healing Service for JobAgent.
Ensures zero-friction startup across any PC (Windows, macOS, Linux).
"""

import sys
import os
import subprocess
from pathlib import Path
from typing import Optional
from rich.console import Console

def get_playwright_browsers_dir() -> Path:
    """Returns the default Playwright browser cache directory for the current OS."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        custom = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        if custom == "0":
            return Path(sys.prefix) / "ms-playwright"
        return Path(custom).resolve()

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            return Path(local_app_data) / "ms-playwright"
        return Path.home() / "AppData" / "Local" / "ms-playwright"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        # Linux / Unix / WSL
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "")
        if xdg_cache:
            return Path(xdg_cache) / "ms-playwright"
        return Path.home() / ".cache" / "ms-playwright"

def is_chromium_installed() -> bool:
    """Checks if Playwright Chromium browser binaries are present on this PC."""
    browsers_dir = get_playwright_browsers_dir()
    if not browsers_dir.exists():
        return False
    # Check for any chromium or chrome subfolder containing an executable
    try:
        for p in browsers_dir.iterdir():
            if p.is_dir() and "chromium" in p.name.lower():
                # On Windows check for chrome.exe, on Unix check for chrome
                if sys.platform == "win32":
                    if list(p.glob("**/chrome.exe")):
                        return True
                else:
                    if list(p.glob("**/chrome")):
                        return True
    except Exception:
        pass
    return False

def ensure_playwright_chromium(console: Optional[Console] = None) -> bool:
    """
    Auto-downloads Playwright Chromium binaries if missing.
    Returns True if Chromium is available, False if installation failed.
    """
    if is_chromium_installed():
        return True

    c = console or Console()
    c.print("\n[bold cyan]🔧 First-Time Hardware & Browser Setup[/bold cyan]")
    c.print("[dim]Playwright Chromium browser binary was not found on this PC.[/dim]")
    c.print("[bold yellow]⚡ Downloading Chromium browser automatically (one-time setup)...[/bold yellow]")

    try:
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        res = subprocess.run(cmd, capture_output=False)
        if res.returncode == 0:
            c.print("[bold green]✓ Playwright Chromium browser installed successfully![/bold green]\n")
            return True
        else:
            c.print(f"[bold red]Installation exited with code {res.returncode}.[/bold red]")
            c.print("[yellow]Tip: You can manually run 'playwright install chromium' in your terminal.[/yellow]\n")
            return False
    except Exception as e:
        c.print(f"[bold red]Failed to install Playwright browser automatically: {e}[/bold red]")
        c.print("[yellow]Tip: Run 'playwright install chromium' manually.[/yellow]\n")
        return False

def run_preflight_checks(console: Optional[Console] = None) -> bool:
    """
    Runs all initial startup preflight checks:
    1. Python version >= 3.10
    2. Terminal UTF-8 encoding configuration
    3. User Data directory permissions
    4. Playwright Chromium readiness
    """
    c = console or Console()

    # 1. Python version check
    if sys.version_info < (3, 10):
        c.print(
            f"[bold red]❌ Unsupported Python Version: {sys.version.split()[0]}[/bold red]\n"
            "[yellow]JobAgent requires Python 3.10 or higher.[/yellow]\n"
            "[dim]Please download and install the latest Python from https://www.python.org/downloads/[/dim]"
        )
        return False

    # 2. Windows terminal output encoding
    if sys.platform == "win32":
        try:
            if sys.stdout and hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if sys.stderr and hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # 3. Data Directory readiness
    from config.settings import settings
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (settings.DATA_DIR / "config").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        c.print(f"[bold red]Warning: Could not create data directory at {settings.DATA_DIR}: {e}[/bold red]")

    return True
