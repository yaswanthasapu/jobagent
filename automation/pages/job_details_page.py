import asyncio
import logging
from typing import Optional
from playwright.async_api import Page

from automation.pages.base_page import BasePage
from models.job import JobCardSummary, JobDetails

logger = logging.getLogger(__name__)

class JobDetailsPage(BasePage):
    """
    Page Object Model for the Job Details pane and Easy Apply launcher.
    """

    def __init__(self, page: Page):
        super().__init__(page)

    async def _ensure_page_active(self) -> Page:
        """Verifies that self.page is alive and open, recovering from active context if closed."""
        try:
            if not self.page or self.page.is_closed():
                context = getattr(self.page, "context", None)
                if context and not context.is_closed():
                    if context.pages:
                        self.page = context.pages[-1]
                    else:
                        self.page = await context.new_page()
                else:
                    raise RuntimeError("Browser window was closed by user.")
        except Exception as e:
            if "closed" in str(e).lower():
                raise RuntimeError("Browser window was closed by user.") from e
            raise
        return self.page

    async def select_and_load_job(self, card: JobCardSummary) -> JobDetails:
        """
        Clicks on the specified job card or directly navigates to the job URL,
        expands the description, and extracts comprehensive details.
        """
        await self._ensure_page_active()

        # Try clicking the card in the list first
        clicked = False
        card_item = None

        if card.job_id and not card.job_id.startswith("job_card_"):
            id_loc = self.page.locator(
                f"[data-occludable-job-id='{card.job_id}'], "
                f"[data-job-id='{card.job_id}'], "
                f"div.job-card-container:has(a[href*='{card.job_id}'])"
            ).first
            if await id_loc.count() > 0:
                card_item = id_loc

        if not card_item:
            card_locators = self.page.locator("div.job-card-container, li.jobs-search-results__list-item")
            if await card_locators.count() > card.card_index:
                card_item = card_locators.nth(card.card_index)

        if card_item and await card_item.count() > 0:
            try:
                await card_item.scroll_into_view_if_needed()
                await self.human_delay(200, 400)
                await card_item.click()
                clicked = True
            except Exception:
                clicked = False

        if not clicked:
            # Navigate directly if click in list was unsuccessful
            logger.info(f"Navigating directly to job URL: {card.job_url}")
            await self._ensure_page_active()
            try:
                await self.page.goto(card.job_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                err_lower = str(e).lower()
                if "closed" in err_lower or "target" in err_lower:
                    await self._ensure_page_active()
                    await self.page.goto(card.job_url, wait_until="domcontentloaded", timeout=30000)
                else:
                    raise

        await self.human_delay(1000, 2000)

        # Expand description if 'Show more' is present
        await self._expand_description()

        # Extract full description
        description_text = await self._extract_description_text()

        # Extract metadata
        workplace_type = await self._extract_workplace_type()

        return JobDetails(
            job_id=card.job_id,
            title=card.title,
            company=card.company,
            location=card.location,
            job_url=card.job_url,
            is_easy_apply=await self.has_easy_apply_button(),
            description_text=description_text,
            workplace_type=workplace_type
        )

    async def has_easy_apply_button(self) -> bool:
        """Checks whether the current job detail has an active 'Easy Apply' button."""
        apply_selectors = [
            "button.jobs-apply-button",
            "button[aria-label*='Easy Apply']",
            "div.jobs-apply-button--top-card button",
            "div.jobs-s-apply button",
            "button:has-text('Easy Apply')",
            "span:has-text('Easy Apply')"
        ]
        for sel in apply_selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    return True
            except Exception:
                continue
        return False

    async def click_easy_apply(self) -> bool:
        """Clicks the Easy Apply button to open the application modal."""
        apply_selectors = [
            "button.jobs-apply-button",
            "button[aria-label*='Easy Apply']",
            "div.jobs-apply-button--top-card button",
            "div.jobs-s-apply button",
            "button:has-text('Easy Apply')",
            "span:has-text('Easy Apply')"
        ]
        for sel in apply_selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=1500):
                    await loc.scroll_into_view_if_needed()
                    await self.human_delay(200, 400)
                    try:
                        await loc.click(timeout=3000)
                    except Exception:
                        await loc.click(force=True)
                    await self.human_delay(1200, 2000)
                    return True
            except Exception:
                continue

        logger.warning("Could not find visible Easy Apply button.")
        return False

    async def _expand_description(self) -> None:
        """Clicks 'Show more' button to un-collapse the full job description."""
        expand_buttons = [
            "button.jobs-description__footer-button",
            "button[aria-label*='Show more']",
            "button:has-text('Show more')",
            "#job-details ~ button"
        ]
        for btn in expand_buttons:
            try:
                loc = self.page.locator(btn).first
                if await loc.is_visible(timeout=1000):
                    await loc.click()
                    await self.human_delay(200, 500)
                    break
            except Exception:
                continue

    async def _extract_description_text(self) -> str:
        """Extracts complete text content from job description containers."""
        desc_selectors = [
            "div.jobs-description__content",
            "div#job-details",
            "article.jobs-description__container",
            "div.jobs-box__html-content"
        ]
        for sel in desc_selectors:
            loc = self.page.locator(sel).first
            if await loc.count() > 0:
                text = await loc.inner_text()
                if len(text.strip()) > 50:
                    return text.strip()

        # Fallback to body text snippet
        return "Job description text could not be isolated from page."

    async def _extract_workplace_type(self) -> Optional[str]:
        """Detects Remote, Hybrid, or On-site from details badges."""
        selectors = [
            "span.job-details-jobs-unified-top-card__workplace-type",
            "li.job-details-jobs-unified-top-card__job-insight",
            "div.job-details-jobs-unified-top-card__primary-description-container"
        ]
        for sel in selectors:
            loc = self.page.locator(sel).first
            if await loc.count() > 0:
                txt = (await loc.inner_text()).lower()
                if "remote" in txt:
                    return "Remote"
                if "hybrid" in txt:
                    return "Hybrid"
                if "on-site" in txt or "onsite" in txt:
                    return "On-site"
        return None
