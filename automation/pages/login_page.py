import asyncio
import logging
from typing import Optional
from playwright.async_api import Page
from rich.console import Console

from automation.pages.base_page import BasePage

logger = logging.getLogger(__name__)

class LoginPage(BasePage):
    """
    Handles authentication state detection and pauses for human completion
    of login, CAPTCHA, or 2FA/OTP checkpoints without credential storage or bypasses.
    """

    def __init__(self, page: Page):
        super().__init__(page)

    async def is_authenticated(self) -> bool:
        """
        Determine if the current browser session is in an authenticated LinkedIn state.
        """
        current_url = self.page.url.lower()

        # If on explicit unauthenticated paths, return False
        if any(bad in current_url for bad in ["/login", "/checkpoint", "/challenge", "/authwall", "/uas/login"]):
            return False

        # Check context cookies for authenticated session cookie li_at
        try:
            cookies = await self.page.context.cookies(["https://www.linkedin.com"])
            for c in cookies:
                if c.get("name") == "li_at" and c.get("value"):
                    return True
        except Exception:
            pass

        # Check for core authenticated DOM anchors
        selectors = [
            "div.global-nav__me",
            "img.global-nav__me-photo",
            "button.global-nav__primary-link-me-menu-trigger",
            "button[aria-label*='Me']",
            "input.search-global-typeahead__input"
        ]
        for sel in selectors:
            try:
                elem = self.page.locator(sel).first
                if await elem.is_visible(timeout=1000):
                    return True
            except Exception:
                continue

        # If URL is on personal feed
        if "/feed" in current_url:
            return True

        return False

    async def is_security_checkpoint_or_login(self) -> bool:
        """
        Detect whether a login form, 2FA prompt, or CAPTCHA challenge is currently blocking.
        """
        current_url = self.page.url.lower()
        if any(term in current_url for term in ["login", "checkpoint", "challenge", "authwall"]):
            return True

        # Check for CAPTCHA iframes or challenge containers
        captcha_selectors = [
            "iframe[src*='arkoselabs']",
            "iframe[src*='recaptcha']",
            "div#captcha-internal",
            "form.login__form",
            "input#username",
            "button[data-litms-control-urn='login-submit']",
            "div.checkpoint-dialog"
        ]
        for sel in captcha_selectors:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=1000):
                    return True
            except Exception:
                continue

        return False

    async def attempt_auto_login(
        self,
        email: str,
        password: str,
        console: Optional[Console] = None
    ) -> bool:
        """
        Navigates directly to LinkedIn login page, explicitly waits for the form fields,
        populates credentials, and submits.
        """
        # Comprehensive selectors covering the Sign in form on jobs page, authwall, and /login
        email_selectors = (
            "input#email-or-phone:visible, input[name='email-or-phone']:visible, "
            "input#username:visible, input[name='session_key']:visible, "
            "input#session_key:visible, input[type='email']:visible, "
            "input[autocomplete='username']:visible"
        )
        password_selectors = (
            "input#password:visible, input[name='password']:visible, "
            "input#session_password:visible, input[name='session_password']:visible, "
            "input[type='password']:visible, input[autocomplete='current-password']:visible"
        )

        try:
            email_loc = self.page.locator(email_selectors).first

            # Only navigate to /login if there is no Sign in form on the current page
            if not await email_loc.is_visible(timeout=2500):
                if console:
                    console.print("[dim]Navigating to LinkedIn Sign in page...[/dim]")
                await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
                await self.human_delay(1000, 2000)
                email_loc = self.page.locator(email_selectors).first

            await email_loc.wait_for(state="visible", timeout=12000)
            password_loc = self.page.locator(password_selectors).first
            await password_loc.wait_for(state="visible", timeout=12000)

            if console:
                console.print(f"[bold cyan]Signing in as {email}...[/bold cyan]")

            # Focus and populate email
            await email_loc.click()
            await email_loc.fill("")
            await email_loc.fill(email)
            await self.human_delay(300, 600)

            # Focus and populate password
            await password_loc.click()
            await password_loc.fill("")
            await password_loc.fill(password)
            await self.human_delay(300, 600)

            # Submit via the visible 'Sign in' button or Enter key
            if console:
                console.print("[cyan]Clicking 'Sign in'...[/cyan]")

            sign_in_btn = self.page.locator("button:visible:has-text('Sign in'), button[type='submit']:visible").first
            if await sign_in_btn.is_visible(timeout=2000):
                await sign_in_btn.click()
            else:
                await password_loc.press("Enter")

            await self.human_delay(4000, 7000)

            # Capture post-submit screenshot
            await self.take_screenshot("after_login_submit")

            if await self.is_authenticated():
                if console:
                    console.print("[bold green]✓ Successfully logged in![/bold green]")
                return True

        except Exception as e:
            if console:
                console.print(f"[yellow]Auto-fill form interaction note: {e}[/yellow]")
            logger.warning(f"Could not auto-fill login fields: {e}")

        # If not authenticated, take checkpoint screenshot and await manual resolution
        await self.take_screenshot("auth_checkpoint")
        return await self.wait_for_manual_authentication(console=console)

    async def wait_for_manual_authentication(
        self,
        console: Optional[Console] = None,
        timeout_seconds: int = 600
    ) -> bool:
        """
        Prompts the user in CLI to resolve login / 2FA / CAPTCHA in the open Chromium window,
        and polls until authentication succeeds.
        """
        screenshot_path = await self.take_screenshot("auth_checkpoint")
        msg = (
            "\n[bold yellow]====================================================================[/bold yellow]\n"
            "[bold red]! AUTHENTICATION / SECURITY CHECKPOINT DETECTED ![/bold red]\n"
            f"[dim]Screenshot saved to: {screenshot_path}[/dim]\n"
            "[bold white]Please switch to the open Chromium browser window and:[/bold white]\n"
            "  1. Tap 'Yes' on your LinkedIn mobile app, OR enter the verification code / SMS code.\n"
            "  2. Click 'Verify' or 'Submit' if prompted.\n"
            "[italic cyan]The agent is actively monitoring and will resume automatically once verified.[/italic cyan]\n"
            "[bold yellow]====================================================================[/bold yellow]\n"
        )
        if console:
            console.print(msg)
        else:
            logger.warning(
                "Authentication/Checkpoint detected. Please complete verification in the open browser. "
                "Monitoring for authenticated state..."
            )

        start_time = asyncio.get_event_loop().time()
        last_logged = start_time

        while asyncio.get_event_loop().time() - start_time < timeout_seconds:
            await asyncio.sleep(2.0)
            now = asyncio.get_event_loop().time()

            if await self.is_authenticated():
                if console:
                    console.print("[bold green]✓ Authentication verified! Resuming automation...[/bold green]\n")
                logger.info("Authentication verified successfully.")
                await self.take_screenshot("authenticated_state")
                return True

            # Print countdown status every 20 seconds
            if now - last_logged >= 20.0:
                elapsed = int(now - start_time)
                remaining = max(0, timeout_seconds - elapsed)
                if console:
                    console.print(f"[dim]... waiting for 2FA approval ({remaining}s remaining) ...[/dim]")
                last_logged = now

        if console:
            console.print("[bold red]✗ Timed out waiting for manual authentication.[/bold red]")
        return False
