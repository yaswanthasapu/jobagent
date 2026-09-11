import asyncio
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Locator

from automation.pages.base_page import BasePage
from models.evaluation import DecisionType
from models.form import FormField, FormFieldType
from models.job import JobCardSummary, JobDetails
from models.profile import CandidateProfile
from agents.form_agent import FormAgent

logger = logging.getLogger(__name__)

class NaukriPage(BasePage):
    """
    Page Object Model for Naukri.com automation:
    - Search & Listings navigation
    - Date posted / Freshness filters (Last 1 Day, Last 3 Days, etc.)
    - Job card extraction
    - Direct / Quick Apply automation with FormAgent questionnaire handling
    """

    def __init__(self, page: Page):
        super().__init__(page)

    async def navigate_and_search(
        self,
        keyword: str = "QA Automation",
        location: str = "Hyderabad",
        date_posted: str = "24h",
        remote_only: bool = False
    ) -> None:
        """
        Navigates to Naukri search with keyword, location, and freshness filters.
        """
        clean_kw = re.sub(r'[^a-zA-Z0-9\s]', '', keyword).strip().lower().replace(" ", "-")
        clean_loc = re.sub(r'[^a-zA-Z0-9\s]', '', location).strip().lower().replace(" ", "-")

        base_url = f"https://www.naukri.com/{clean_kw}-jobs-in-{clean_loc}"
        params: Dict[str, str] = {
            "k": keyword,
            "l": location
        }

        # Naukri Freshness filter mapping:
        # 1 day = freshenss=1, 3 days = freshenss=3, 7 days = freshenss=7
        if date_posted == "24h":
            params["freshenss"] = "1"
        elif date_posted == "week":
            params["freshenss"] = "7"
        elif date_posted == "month":
            params["freshenss"] = "30"

        if remote_only:
            params["wfhType"] = "0"  # Work from home / Remote

        qs = urllib.parse.urlencode(params)
        search_url = f"{base_url}?{qs}"
        logger.info(f"Navigating to Naukri search: {search_url}")

        try:
            await self.page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            logger.warning(f"Direct URL navigation had issue: {e}. Falling back to home search.")
            await self.page.goto("https://www.naukri.com/", wait_until="domcontentloaded", timeout=45000)
            await self._search_via_ui(keyword, location)

        await self.human_delay(2000, 3500)
        await self._apply_freshness_filter_on_page(date_posted)

    async def _search_via_ui(self, keyword: str, location: str) -> None:
        """Fallback UI search inputs if direct URL is redirected."""
        try:
            kw_input = self.page.locator("input.suggestor-input, input[placeholder*='skills'], input[placeholder*='Designation']").first
            if await kw_input.is_visible(timeout=3000):
                await kw_input.fill(keyword)
                await self.human_delay(300, 600)

            loc_input = self.page.locator("input[placeholder*='location'], input[placeholder*='Location']").first
            if await loc_input.is_visible(timeout=2000):
                await loc_input.fill(location)
                await self.human_delay(300, 600)

            btn = self.page.locator("button.qsbSubmit, div.qsbSubmit, button:has-text('Search')").first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await self.human_delay(2000, 3000)
        except Exception as e:
            logger.debug(f"UI search fallback warning: {e}")

    async def _apply_freshness_filter_on_page(self, date_posted: str) -> None:
        """Applies freshness filter by clicking UI pill if available."""
        if date_posted not in ["24h", "week"]:
            return

        filter_text = "1 Day" if date_posted == "24h" else "7 Days"
        try:
            # Check for freshness filter accordion or checkbox
            freshness_pill = self.page.locator(f"span:has-text('{filter_text}'), label:has-text('{filter_text}')").first
            if await freshness_pill.is_visible(timeout=2500):
                logger.info(f"Clicking freshness filter pill: {filter_text}")
                await freshness_pill.click()
                await self.human_delay(1500, 2500)
        except Exception:
            pass

    async def extract_job_cards(self, max_cards: int = 15) -> List[JobCardSummary]:
        """Extract job listings from Naukri search results."""
        cards: List[JobCardSummary] = []
        
        # Scroll to trigger lazy loading
        try:
            for _ in range(3):
                await self.page.mouse.wheel(0, 600)
                await self.human_delay(300, 600)
        except Exception:
            pass

        tuple_selectors = [
            "div.srp-jobtuple-wrapper",
            "div.cust-job-tuple",
            "article.jobTuple",
            "div[data-job-id]"
        ]

        locators = None
        for sel in tuple_selectors:
            locs = self.page.locator(sel)
            if await locs.count() > 0:
                locators = locs
                break

        if not locators:
            logger.warning("No Naukri job tuples found with standard selectors.")
            return cards

        count = await locators.count()
        logger.info(f"Found {count} job tuples on Naukri page.")

        for i in range(min(count, max_cards)):
            loc = locators.nth(i)
            try:
                # 1. Title & URL
                title_loc = loc.locator("a.title, a.job-title").first
                title = (await title_loc.inner_text()).strip() if await title_loc.count() > 0 else "Job Listing"
                href = (await title_loc.get_attribute("href")) if await title_loc.count() > 0 else ""
                
                # Clean URL & Job ID
                job_url = href.split("?")[0] if href else ""
                raw_id_m = re.search(r'-(\d+)(?:\?|$)', href)
                raw_id = raw_id_m.group(1) if raw_id_m else f"naukri_{i}"

                # 2. Company Name
                comp_loc = loc.locator("a.comp-name, a.subTitle, span.comp-name").first
                company = (await comp_loc.inner_text()).strip() if await comp_loc.count() > 0 else "Company"

                # 3. Location
                loc_loc = loc.locator("span.loc-wrap, span.locwdth, span.location, span.loc").first
                location_text = (await loc_loc.inner_text()).strip() if await loc_loc.count() > 0 else "India"

                # 4. Check if Direct Apply (Easy Apply)
                is_easy_apply = True
                card_text = (await loc.inner_text()).lower()
                if "company site" in card_text:
                    is_easy_apply = False

                cards.append(JobCardSummary(
                    job_id=raw_id,
                    title=title,
                    company=company,
                    location=location_text,
                    is_easy_apply=is_easy_apply,
                    job_url=job_url or f"https://www.naukri.com/job-listings-{raw_id}",
                    card_index=i
                ))
            except Exception as e:
                logger.debug(f"Error extracting Naukri card {i}: {e}")

        return cards

    async def open_job_and_apply(
        self,
        card: JobCardSummary,
        form_agent: FormAgent,
        profile: CandidateProfile,
        job_evaluator: Optional[Any] = None,
        min_score: float = 60.0,
        auto_approve: bool = False,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Opens a job details page, extracts job description text, evaluates candidate-job fit,
        checks for direct apply button, answers any questionnaire fields using FormAgent,
        and completes the application.
        """
        result: Dict[str, Any] = {
            "status": "skipped",
            "message": "",
            "applied": False,
            "match_score": 0.0,
            "matched_skills": [],
            "missing_skills": [],
            "reasoning": ""
        }

        if not card.job_url:
            result["message"] = "Invalid job URL"
            return result

        logger.info(f"Opening Naukri job: {card.title} ({card.job_url})")
        new_page = await self.page.context.new_page()
        try:
            await new_page.goto(card.job_url, wait_until="domcontentloaded", timeout=35000)
            await asyncio.sleep(2.0)

            # 1. Extract job description text from page
            jd_text = ""
            desc_selectors = [
                "section.styles_job-desc-container__pv_gZ",
                "section.job-desc-container",
                "div.dang-inner-html",
                "div.job-desc",
                "div.clearall",
                "main"
            ]
            for sel in desc_selectors:
                loc = new_page.locator(sel).first
                if await loc.is_visible(timeout=1000):
                    jd_text = await loc.inner_text()
                    if len(jd_text.strip()) > 60:
                        break

            if not jd_text or len(jd_text.strip()) < 60:
                try:
                    body_loc = new_page.locator("body")
                    jd_text = await body_loc.inner_text()
                except Exception:
                    jd_text = card.snippet or card.title

            # 2. Evaluate candidate fit if job_evaluator is provided
            if job_evaluator:
                job_details = JobDetails(
                    job_id=card.job_id,
                    title=card.title,
                    company=card.company,
                    location=card.location,
                    job_url=card.job_url,
                    description_text=jd_text,
                    platform="Naukri"
                )
                eval_res = await job_evaluator.evaluate_fit(job_details, profile)
                match_score = eval_res.match_breakdown.overall_score
                result["match_score"] = match_score
                result["matched_skills"] = eval_res.match_breakdown.matched_skills
                result["missing_skills"] = eval_res.match_breakdown.missing_skills
                result["reasoning"] = eval_res.match_breakdown.reasoning

                if eval_res.decision != DecisionType.APPLY or match_score < min_score:
                    result["status"] = "skipped_low_score"
                    result["message"] = eval_res.match_breakdown.reasoning
                    logger.info(f"Skipping Naukri job '{card.title}': {eval_res.match_breakdown.reasoning}")
                    return result
            else:
                result["match_score"] = 85.0

            # 3. Check for Apply button
            apply_btn = new_page.locator("button.apply-button, button#apply-button, button:visible:has-text('Apply'), button:visible:has-text('Apply on Naukri')").first
            is_apply_visible = await apply_btn.is_visible(timeout=3000)

            if not is_apply_visible:
                # Check if already applied
                applied_indicator = new_page.locator("span:has-text('Already Applied'), div:has-text('Already applied')").first
                if await applied_indicator.is_visible(timeout=1500):
                    result["status"] = "already_applied"
                    result["message"] = "Already applied to this job."
                    return result
                
                result["status"] = "no_apply_button"
                result["message"] = "No direct apply button found (likely external link)."
                return result

            btn_text = (await apply_btn.inner_text()).lower()
            if "company site" in btn_text or "external" in btn_text:
                result["status"] = "external_redirect"
                result["message"] = "Direct application not available (external company portal)."
                return result

            if dry_run:
                result["status"] = "dry_run"
                result["applied"] = True
                result["message"] = "Dry run simulated successfully."
                return result

            # Click Apply
            logger.info("Clicking Naukri Apply button...")
            await apply_btn.click()
            await asyncio.sleep(2.5)

            # Check if chatbot / modal questions opened
            chat_container = new_page.locator("div.chatbot_Drawer, div.chatbot-container, div.bot-container, div.apply-message").first
            is_chat_visible = await chat_container.is_visible(timeout=3000)

            if is_chat_visible:
                logger.info("Naukri questionnaire chatbot detected. Handling questions...")
                await self._handle_naukri_chatbot(new_page, form_agent, profile)

            # Verify submission
            success_selector = "span:has-text('Applied'), div:has-text('successfully applied'), span:has-text('Application sent')"
            success_elem = new_page.locator(success_selector).first
            is_success = await success_elem.is_visible(timeout=4000)

            if is_success or not is_chat_visible:
                result["status"] = "applied"
                result["applied"] = True
                result["message"] = "Successfully applied on Naukri!"
            else:
                result["status"] = "incomplete"
                result["message"] = "Application flow stopped before confirmation."

        except Exception as e:
            logger.error(f"Error applying on Naukri: {e}")
            result["status"] = "error"
            result["message"] = str(e)
        finally:
            await new_page.close()

        return result

    async def _handle_naukri_chatbot(
        self,
        page: Page,
        form_agent: FormAgent,
        profile: CandidateProfile,
        max_steps: int = 10
    ) -> None:
        """Interactively answers questionnaire chatbot prompts on Naukri."""
        for step in range(max_steps):
            await asyncio.sleep(1.5)
            
            # Check if chatbot has a text input ready
            input_box = page.locator("input.chat-input, input[placeholder*='Type your answer'], textarea.chat-input, input.msg-input").first
            if await input_box.is_visible(timeout=2000):
                # Get last bot question
                question_loc = page.locator("div.bot-message, div.msg-container.bot, div.msg_text").last
                q_text = (await question_loc.inner_text()).strip() if await question_loc.count() > 0 else ""
                logger.info(f"Naukri chatbot question: {q_text}")

                # Resolve with FormAgent
                ff = FormField(
                    field_id=f"naukri_chat_{step}",
                    label=q_text or "Question",
                    field_type=FormFieldType.TEXT
                )
                val, needs_hitl = await form_agent.resolve_field_value(ff, profile)
                answer = str(val or "Yes")
                logger.info(f"Submitting answer to chatbot: {answer}")
                await input_box.fill(answer)
                if q_text:
                    form_agent.memory_service.save_form_answer(q_text, answer, "text")
                await asyncio.sleep(0.3)
                
                # Click send or press Enter
                send_btn = page.locator("button.send-btn, button:has-text('Send'), span.send-icon").first
                if await send_btn.is_visible(timeout=1500):
                    await send_btn.click()
                else:
                    await input_box.press("Enter")
                await asyncio.sleep(1.5)
                continue

            # Check if options/pills to click
            option_chips = page.locator("button.chip, div.chip, button.option-btn:visible")
            if await option_chips.count() > 0:
                first_chip = option_chips.first
                chip_text = (await first_chip.inner_text()).strip()
                logger.info(f"Clicking option chip: {chip_text}")
                await first_chip.click()
                if q_text and chip_text:
                    form_agent.memory_service.save_form_answer(q_text, chip_text, "select")
                await asyncio.sleep(1.5)
                continue

            # If no input or options visible, the chatbot may be finished
            break
