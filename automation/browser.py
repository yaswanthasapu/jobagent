import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Any, Dict
from rich.console import Console

logger = logging.getLogger(__name__)

async def launch_persistent_context_with_retry(
    pw: Any,
    user_data_dir: Path | str,
    console: Optional[Console] = None,
    headless: bool = False,
    slow_mo: int = 100,
    args: Optional[List[str]] = None,
    no_viewport: bool = True,
    viewport: Optional[Dict[str, int]] = None,
    **kwargs
):
    """
    Robust wrapper around pw.chromium.launch_persistent_context that automatically:
    1. Detects missing Playwright Chromium binaries and runs 'playwright install chromium'.
    2. Clears stale ProcessSingleton profile locks if a prior browser session crashed.
    """
    c = console or Console()
    user_data_path = Path(user_data_dir)
    user_data_path.mkdir(parents=True, exist_ok=True)

    launch_args = args or ["--disable-blink-features=AutomationControlled", "--start-maximized"]
    viewport_kwargs = {}
    if no_viewport:
        viewport_kwargs["no_viewport"] = True
    elif viewport:
        viewport_kwargs["viewport"] = viewport

    for attempt in range(2):
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_path),
                headless=headless,
                slow_mo=slow_mo,
                args=launch_args,
                **viewport_kwargs,
                **kwargs
            )
            return context
        except Exception as e:
            err_msg = str(e)
            logger.warning(f"Browser launch attempt {attempt + 1} encountered error: {err_msg}")

            # Case 1: Chromium executable missing on user's machine (e.g. fresh pip install)
            if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
                c.print(
                    "\n[bold yellow]⚡ Playwright Chromium browser not found.[/bold yellow]\n"
                    "[dim]Downloading Chromium browser binaries automatically (one-time setup)...[/dim]"
                )
                try:
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        check=True
                    )
                    c.print("[bold green]✓ Chromium installed successfully! Resuming browser launch...[/bold green]\n")
                    await asyncio.sleep(1.0)
                    continue
                except Exception as install_err:
                    c.print(f"[bold red]Failed to auto-install Playwright Chromium: {install_err}[/bold red]")
                    c.print("[yellow]Please run 'playwright install chromium' manually in your terminal.[/yellow]")
                    raise install_err

            # Case 2: Stale Chrome lock file from previous crashed run
            elif "ProcessSingleton" in err_msg:
                c.print("[yellow]Notice: Clearing stale browser profile lock from previous session...[/yellow]")
                if sys.platform == "win32":
                    subprocess.run(
                        'powershell -Command "Get-Process -Name chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path -like \'*ms-playwright*\' } | Stop-Process -Force"',
                        shell=True,
                        capture_output=True
                    )
                else:
                    subprocess.run(["pkill", "-f", "ms-playwright.*chrome"], capture_output=True)
                await asyncio.sleep(1.5)
                continue

            # Unrecognized error - raise immediately
            raise e

    # Fallback attempt after handling
    return await pw.chromium.launch_persistent_context(
        user_data_dir=str(user_data_path),
        headless=headless,
        slow_mo=slow_mo,
        args=launch_args,
        **viewport_kwargs,
        **kwargs
    )
