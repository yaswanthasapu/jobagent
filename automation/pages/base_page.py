import asyncio
import logging
import random
from pathlib import Path
from typing import Optional
from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

class BasePage:
    """
    Playwright base Page Object Model abstraction with stealth features,
    humanized pacing, and robust error recovery.
    """

    def __init__(self, page: Page):
        self.page = page

    async def inject_stealth(self) -> None:
        """Inject scripts to mask automation indicators."""
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)

    async def human_delay(self, min_ms: int = 400, max_ms: int = 1200) -> None:
        """Pause execution for a randomized human-like interval."""
        delay = random.uniform(min_ms, max_ms) / 1000.0
        await asyncio.sleep(delay)

    async def type_human_like(self, selector_or_locator, text: str) -> None:
        """Type text character by character with organic timing jitter."""
        if isinstance(selector_or_locator, str):
            locator = self.page.locator(selector_or_locator)
        else:
            locator = selector_or_locator

        await locator.click()
        # Clear field first
        await locator.fill("")
        for char in text:
            await locator.type(char, delay=random.randint(25, 85))
        await self.human_delay(150, 400)

    async def safe_click(self, selector_or_locator, timeout_ms: int = 8000) -> bool:
        """Attempt to click an element, handling scroll and retry."""
        try:
            if isinstance(selector_or_locator, str):
                locator = self.page.locator(selector_or_locator).first
            else:
                locator = selector_or_locator

            await locator.wait_for(state="visible", timeout=timeout_ms)
            await locator.scroll_into_view_if_needed()
            await self.human_delay(100, 300)
            await locator.click()
            return True
        except Exception as e:
            logger.debug(f"safe_click failed on {selector_or_locator}: {e}")
            return False

    async def take_screenshot(self, name: str, folder: str = "screenshots") -> str:
        """Save a screenshot for debugging or audit trail, and mirror to artifact folder for UI display."""
        p = Path(folder)
        p.mkdir(parents=True, exist_ok=True)
        file_path = p / f"{name}.png"
        await self.page.screenshot(path=str(file_path), full_page=False)

        # Also copy to artifact directory if available for markdown embedding
        artifact_dir = Path(r"C:\Users\Yaswanth\.gemini\antigravity\brain\d0995ffe-f6c6-4d4a-8318-05b0c83f3f62")
        if artifact_dir.exists():
            try:
                import shutil
                shutil.copy(file_path, artifact_dir / f"{name}.png")
            except Exception:
                pass

        return str(file_path.resolve())

    async def wait_for_url_change(self, target_pattern: str, timeout_ms: int = 15000) -> bool:
        try:
            await self.page.wait_for_url(lambda u: target_pattern in u, timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            return False
