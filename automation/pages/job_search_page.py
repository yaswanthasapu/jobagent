import asyncio
import logging
import re
import urllib.parse
from typing import List, Optional
from playwright.async_api import Page, Locator

from automation.pages.base_page import BasePage
from models.job import JobCardSummary
from services.db_service import clean_scraped_text

logger = logging.getLogger(__name__)

class JobSearchPage(BasePage):
    """
    Page Object Model for the LinkedIn Jobs Search & Listings view.
    """

    def __init__(self, page: Page):
        super().__init__(page)

    async def navigate_to_search(
        self,
        keyword: str,
        location: str,
        easy_apply_only: bool = True,
        date_posted: str = "24h",
        sort_by: str = "date",
        remote_only: bool = False,
        start: int = 0
    ) -> None:
        """
        Builds search URL with Easy Apply, Freshness/Date Posted, and Remote filters.
        - date_posted: '24h' (f_TPR=r86400), 'week' (f_TPR=r604800), 'month' (f_TPR=r2592000), or 'any'
        - sort_by: 'date' (sortBy=DD), 'relevance' (sortBy=R)
        - remote_only: True (f_WT=2)
        - start: pagination offset (0, 25, 50, 75...)
        """
        clean_kw = keyword.strip().strip('"').strip("'").strip()

        # When date_posted is "any" and sort_by is "date", LinkedIn sorts broad results by seconds ago,
        # yielding irrelevant postings. Relevance sorting ("R") brings the highest matching targeted posts to the top
        effective_sort = sort_by
        if date_posted == "any" and sort_by == "date":
            effective_sort = "relevance"

        params = {
            "keywords": clean_kw,
            "location": location,
            "sortBy": "DD" if effective_sort == "date" else "R",
        }
        if start > 0:
            params["start"] = str(start)

        if easy_apply_only:
            params["f_AL"] = "true"

        # Date Posted / Freshness filter
        if date_posted == "24h":
            params["f_TPR"] = "r86400"
        elif date_posted == "week":
            params["f_TPR"] = "r604800"
        elif date_posted == "month":
            params["f_TPR"] = "r2592000"

        # Workplace Type: Remote = 2
        if remote_only:
            params["f_WT"] = "2"

        query_string = urllib.parse.urlencode(params)
        url = f"https://www.linkedin.com/jobs/search/?{query_string}"
        logger.info(f"Navigating to LinkedIn search: {url}")

        await self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await self.human_delay(2000, 3500)
        for sel in [
            "div.jobs-search-results-list",
            "div.scaffold-layout__list",
            "ul.jobs-search__results-list",
            "div.job-card-container",
            "li.jobs-search-results__list-item",
            "li.scaffold-layout__list-item"
        ]:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=3000):
                    break
            except Exception:
                pass

    async def scroll_job_list(self, steps: int = 6) -> None:
        """
        Scrolls the job results container to trigger lazy loading of cards.
        """
        container_selectors = [
            "div.jobs-search-results-list",
            "div.scaffold-layout__list",
            "div[class*='jobs-search-results-list']",
            "ul.jobs-search__results-list",
            "div[data-view-name='job-card']"
        ]
        container = None
        for sel in container_selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    container = loc
                    break
            except Exception:
                pass

        target = container if container else self.page.locator("body")

        for _ in range(steps):
            try:
                await target.evaluate("node => node.scrollTop += 800")
            except Exception:
                await self.page.mouse.wheel(0, 800)
            await self.human_delay(350, 600)

    async def scroll_job_list_to_bottom(self) -> None:
        """
        Scrolls the job search container all the way to the bottom
        to ensure pagination controls and all cards are loaded.
        """
        container_selectors = [
            "div.jobs-search-results-list",
            "div.scaffold-layout__list",
            "div[class*='jobs-search-results-list']",
            "ul.jobs-search__results-list"
        ]
        container = None
        for sel in container_selectors:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    container = loc
                    break
            except Exception:
                pass
        target = container if container else self.page.locator("body")
        for _ in range(4):
            try:
                await target.evaluate("node => { node.scrollTop = node.scrollHeight; }")
            except Exception:
                await self.page.mouse.wheel(0, 1500)
            await self.human_delay(300, 500)

    async def click_next_page(self) -> bool:
        """
        Scrolls to the pagination bar at the bottom of the search results list,
        and clicks the 'Next' page button or indicator.
        Returns True if the next page was clicked, False otherwise.
        """
        await self.scroll_job_list_to_bottom()
        await self.human_delay(500, 800)

        next_selectors = [
            "button.artdeco-pagination__button--next:visible",
            "button[aria-label='View next page']:visible",
            "button[aria-label='Next']:visible",
            "li.artdeco-pagination__indicator--number.active + li button:visible",
            "button:visible:has-text('Next')"
        ]
        for sel in next_selectors:
            btn = self.page.locator(sel).first
            if await btn.count() > 0 and await btn.is_visible():
                is_disabled = await btn.get_attribute("disabled") is not None or "disabled" in (await btn.get_attribute("class") or "")
                if not is_disabled:
                    try:
                        await btn.scroll_into_view_if_needed()
                        await btn.click(timeout=3000)
                        await self.human_delay(2000, 3500)
                        return True
                    except Exception as e:
                        logger.debug(f"Failed to click next page button with {sel}: {e}")
        return False

    async def extract_job_cards(self, max_cards: int = 25) -> List[JobCardSummary]:
        """
        Locates and extracts metadata from all visible job cards.
        """
        await self.scroll_job_list(steps=6)
        cards: List[JobCardSummary] = []

        card_selectors = [
            "div.job-card-container",
            "li.jobs-search-results__list-item",
            "div.jobs-search-results-list__list-item",
            "li.scaffold-layout__list-item",
            "li[data-occludable-job-id]",
            "div[data-occludable-job-id]",
            "div[data-job-id]",
            "div[data-view-name='job-card']",
            "ul.jobs-search__results-list > li",
            "div.base-card",
            "div.base-search-card"
        ]

        locators = None
        for attempt in range(3):
            for sel in card_selectors:
                locs = self.page.locator(sel)
                count = await locs.count()
                if count > 0:
                    locators = locs
                    break
            if locators:
                break
            await asyncio.sleep(1.5)
            await self.scroll_job_list(steps=2)

        if not locators:
            logger.warning("No job cards found using standard selectors.")
            return cards

        count = await locators.count()
        logger.info(f"Found {count} raw job card elements on page.")

        for i in range(min(count, max_cards)):
            card_loc = locators.nth(i)
            try:
                # 1. Job ID
                raw_id = (
                    await card_loc.get_attribute("data-occludable-job-id") or
                    await card_loc.get_attribute("data-job-id") or
                    ""
                )

                # Link & Title
                link_loc = card_loc.locator("a.job-card-list__title, a.job-card-container__link, a[href*='/jobs/view/'], a.base-card__full-link, a[data-control-name='job_card_click']").first
                href = await link_loc.get_attribute("href") if await link_loc.count() > 0 else ""

                if not raw_id and href:
                    id_match = re.search(r'/jobs/view/(\d+)', href)
                    if id_match:
                        raw_id = id_match.group(1)

                if not raw_id:
                    raw_id = f"job_card_{i}"

                job_url = href.split("?")[0] if href else f"https://www.linkedin.com/jobs/view/{raw_id}"
                if job_url.startswith("/"):
                    job_url = f"https://www.linkedin.com{job_url}"

                # Title Text
                title_text = ""
                if await link_loc.count() > 0:
                    title_text = (await link_loc.inner_text()).strip()
                if not title_text:
                    title_elem = card_loc.locator("strong, .job-card-list__title, span[aria-hidden='true']").first
                    if await title_elem.count() > 0:
                        title_text = (await title_elem.inner_text()).strip()

                title_text = clean_scraped_text(title_text)

                # Company
                comp_elem = card_loc.locator(
                    ".job-card-container__primary-description, .artdeco-entity-lockup__subtitle, span.company-name, h4.base-search-card__subtitle, .job-card-container__company-name"
                ).first
                company_text = (await comp_elem.inner_text()).strip() if await comp_elem.count() > 0 else "Unknown Company"
                company_text = clean_scraped_text(company_text)

                # Location
                loc_elem = card_loc.locator(
                    ".job-card-container__metadata-item, .artdeco-entity-lockup__caption, li.job-card-container__metadata-item, span.job-search-card__location, .job-card-container__metadata-wrapper"
                ).first
                location_text = (await loc_elem.inner_text()).strip() if await loc_elem.count() > 0 else "Remote / Flexible"
                location_text = clean_scraped_text(location_text)

                # Easy Apply Badge
                card_text = (await card_loc.inner_text()).lower()
                is_easy_apply = "easy apply" in card_text or "inapply" in card_text

                summary = JobCardSummary(
                    job_id=raw_id,
                    title=title_text or "QA Automation Engineer",
                    company=company_text,
                    location=location_text,
                    is_easy_apply=is_easy_apply,
                    job_url=job_url,
                    card_index=i
                )
                cards.append(summary)

            except Exception as e:
                logger.debug(f"Error extracting card {i}: {e}")
                continue

        return cards
