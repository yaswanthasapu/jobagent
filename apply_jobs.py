import argparse
import asyncio
import logging
from pathlib import Path
import re
import sys

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from typing import Optional, List
from playwright.async_api import async_playwright
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from config.settings import settings, get_browser_user_data_dir
from models.application import ApplicationRecord, ApplicationStatus
from models.evaluation import DecisionType
from models.form import FormField, FormFieldType
from services.db_service import DatabaseService
from services.llm_service import LLMService
from services.profile_loader import ProfileLoader
from services.resume_service import ResumeService
from services.memory_service import MemoryService
from services.setup_service import SetupService
from services.hitl_service import HITLManager
from agents.job_evaluator import JobEvaluator
from agents.form_agent import FormAgent
from automation.pages.job_search_page import JobSearchPage
from automation.pages.job_details_page import JobDetailsPage
from automation.pages.easy_apply_modal import EasyApplyModal
from automation.pages.naukri_page import NaukriPage
from automation.browser import launch_persistent_context_with_retry

logger = logging.getLogger(__name__)

# Active session references for clean cancellation and stopping from UI
CURRENT_BROWSER_CONTEXT = None
CURRENT_PW = None
CURRENT_DB_SERVICE = None

async def stop_current_session():
    """Cleanly closes active browser context, playwright instance, and database connections."""
    global CURRENT_BROWSER_CONTEXT, CURRENT_PW, CURRENT_DB_SERVICE
    if CURRENT_BROWSER_CONTEXT:
        try:
            await CURRENT_BROWSER_CONTEXT.close()
        except Exception:
            pass
        CURRENT_BROWSER_CONTEXT = None
    if CURRENT_PW:
        try:
            await CURRENT_PW.stop()
        except Exception:
            pass
        CURRENT_PW = None
    if CURRENT_DB_SERVICE:
        try:
            await CURRENT_DB_SERVICE.close()
        except Exception:
            pass
        CURRENT_DB_SERVICE = None

async def handle_new_field_hitl(
    modal: EasyApplyModal,
    field: FormField,
    suggested_val: Optional[str],
    memory_service: MemoryService,
    console: Console,
    timeout_sec: int = 60
) -> Optional[str]:
    """
    Handles Human-in-the-Loop interaction when a new, unremembered field is encountered.
    Supports interactive terminal prompt (in CLI mode), direct Web UI response, or polling the visible browser window.
    Saves the user's authentic response to persistent memory.
    """
    clean_label = re.sub(r'[\*\:]', '', field.label).strip()
    is_cli_interactive = bool(sys.stdin and sys.stdin.isatty())
    clean_sug = re.sub(r'^(?:answer|response|selected option):\s*', '', suggested_val or '', flags=re.IGNORECASE).strip()

    opts_display = ""
    if field.options:
        opts_display = "  " + "  |  ".join(f"[{i+1}] {opt}" for i, opt in enumerate(field.options))

    console.print(Panel(
        f"[bold yellow]🔔 Human Interaction Required for New Field[/bold yellow]\n"
        f"[bold white]Question:[/bold white] [bold cyan]{clean_label}[/bold cyan]\n"
        f"[dim]Type:[/dim] {field.field_type.value}" + (f"\n[dim]Options:[/dim]\n{opts_display}" if opts_display else "") +
        (f"\n[dim]Suggested:[/dim] [yellow]{clean_sug}[/yellow]" if clean_sug else "") +
        f"\n[dim]👉 {'Enter answer in console or select/type directly in browser' if is_cli_interactive else 'Please enter or select your answer directly in the visible browser window or Web UI dashboard (waiting up to ' + str(timeout_sec) + 's)...'}[/dim]",
        title="[bold yellow]JobAgent HITL Alert[/bold yellow]",
        border_style="yellow"
    ))

    # Register HITL request so Web UI dashboard displays it immediately
    hitl_future = HITLManager.request_human_input(
        hitl_type="field_input",
        title=f"🔔 Human Input Required: {clean_label[:35]}",
        message=f"Please provide an answer or selection for: '{clean_label}'",
        field_label=field.label,
        options=field.options or [],
        suggested_value=clean_sug or "",
        timeout_sec=timeout_sec
    )

    user_ans = None

    if is_cli_interactive:
        try:
            if field.options:
                def_idx = "1"
                if clean_sug:
                    for i, o in enumerate(field.options):
                        if clean_sug.lower() == o.lower() or clean_sug.lower() in o.lower() or o.lower() in clean_sug.lower():
                            def_idx = str(i + 1)
                            break
                choice = Prompt.ask(f"[bold yellow]Select option number [1-{len(field.options)}] or enter text[/bold yellow]", default=def_idx)
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(field.options):
                        user_ans = field.options[idx]
                    else:
                        user_ans = choice.strip()
                except ValueError:
                    user_ans = choice.strip()
            elif field.field_type == FormFieldType.NUMBER:
                user_ans = Prompt.ask(f"[bold yellow]Enter number for '{clean_label[:40]}'[/bold yellow]", default=clean_sug or "0").strip()
            else:
                user_ans = Prompt.ask(f"[bold yellow]Enter answer for '{clean_label[:40]}'[/bold yellow]", default=clean_sug or "").strip()
        except Exception as e:
            logger.warning(f"Terminal prompt interrupted or failed: {e}")

    # Watch both Web UI response (hitl_future) and browser input
    poll_start = asyncio.get_event_loop().time()
    try:
        while not user_ans and (asyncio.get_event_loop().time() - poll_start) < timeout_sec:
            if hitl_future.done():
                try:
                    res = hitl_future.result()
                    if res.get("action") == "answer" and res.get("value"):
                        user_ans = str(res.get("value")).strip()
                        console.print(f"  [bold green]✓ Received answer from Web UI:[/bold green] '{user_ans}'")
                        break
                    elif res.get("action") == "resolved":
                        cur = await modal.get_field_current_value(field)
                        if cur:
                            user_ans = cur
                            break
                except Exception:
                    pass

            if modal.page.is_closed():
                break

            detected = await modal.get_field_current_value(field)
            if detected and detected.lower() != "select an option":
                user_ans = detected
                console.print(f"  [bold green]✓ Detected user selection in browser:[/bold green] '{user_ans}'")
                break

            await asyncio.sleep(1.0)
    finally:
        HITLManager.clear()

    if not user_ans and clean_sug:
        console.print(f"[yellow]Timeout reached. Using fallback suggested value: '{clean_sug}'[/yellow]")
        user_ans = clean_sug

    # Save to persistent memory
    if user_ans:
        ftype = field.field_type.value if hasattr(field.field_type, "value") else str(field.field_type)
        memory_service.save_form_answer(field.label, user_ans, ftype)
        console.print(f"  [bold green]✓ Remembered response for future applications:[/bold green] '{clean_label[:35]}' -> '[bold cyan]{user_ans}[/bold cyan]'")
        logger.info(f"Saved HITL answer to memory: {field.label} = {user_ans}")

    return user_ans

async def wait_for_user_to_resolve_errors(
    modal: EasyApplyModal,
    errors: List[str],
    memory_service: MemoryService,
    console: Console,
    timeout_sec: int = 120
) -> bool:
    """
    Waits for the user to provide data / fix selections in the open browser window or Web UI
    when validation errors occur, instead of abandoning the application and closing the modal.
    """
    err_text = ", ".join(errors) if errors else "Please make a selection"
    is_cli_interactive = bool(sys.stdin and sys.stdin.isatty())

    # Register with HITLManager so the Web UI dashboard immediately displays the alert card
    hitl_future = HITLManager.request_human_input(
        hitl_type="validation_error",
        title="⚠️ Action Required: Missing or Invalid Selection Detected",
        message=f"Validation Error: {err_text}. The Chromium browser window is open. Please select the required value directly in the browser, or click 'I Resolved It in Browser' below.",
        field_label="Validation Error",
        timeout_sec=timeout_sec
    )

    console.print(Panel(
        f"[bold yellow]⚠️ Action Required: Missing or Invalid Selection Detected[/bold yellow]\n\n"
        f"[bold red]Validation Error:[/bold red] [white]{err_text}[/white]\n\n"
        f"[white]The Chromium browser window is open in front of you.\n"
        f"• Please select or enter the required value directly in the browser.\n"
        f"• If using the Web UI Dashboard, click '[bold green]I Resolved It in Browser[/bold green]'.\n"
        f"• You can also click Next directly in the browser once answered.[/white]\n\n"
        f"[dim]👉 Press [bold green][Enter][/bold green] in this console after selecting in browser.\n"
        f"   (Or type '[bold red]skip[/bold red]' to discard this application)[/dim]",
        title="[bold yellow]JobAgent User Input Wait[/bold yellow]",
        border_style="yellow"
    ))

    # If interactive CLI terminal, listen for Enter keypress non-blockingly
    if is_cli_interactive:
        async def _read_cli_keypress():
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
                if line:
                    if "skip" in line.lower():
                        HITLManager.resolve_request("skip")
                    else:
                        HITLManager.resolve_request("resolved")
            except Exception:
                pass
        asyncio.create_task(_read_cli_keypress())

    # Polling loop: monitor Web UI response, browser state, and CLI
    poll_start = asyncio.get_event_loop().time()
    try:
        while (asyncio.get_event_loop().time() - poll_start) < timeout_sec:
            # 1. Check if user responded via Web UI
            if hitl_future.done():
                try:
                    res = hitl_future.result()
                    if res.get("action") == "skip":
                        console.print("[yellow]Skipping application as requested by user via Web UI.[/yellow]")
                        return False
                except Exception:
                    pass
                console.print("[bold green]✓ Confirmation received from Web UI! Advancing...[/bold green]")
                break

            # 2. Check if browser or modal was closed
            if modal.page.is_closed():
                console.print("[yellow]Browser window was closed by user.[/yellow]")
                return False

            if not await modal.is_modal_open():
                return True
            if await modal.is_review_step():
                return True

            # 3. Check if error cleared in browser (e.g. user selected radio or filled input)
            has_err = await modal.has_validation_errors()
            if not has_err:
                console.print("[bold green]✓ Validation resolved in browser! Advancing...[/bold green]")
                break

            await asyncio.sleep(1.5)

        # Try to advance step once resolved
        if not modal.page.is_closed() and await modal.is_modal_open():
            advanced = await modal.click_next() or await modal.click_review()
            if advanced or await modal.is_review_step():
                console.print("[bold green]✓ Validation resolved! Advancing application...[/bold green]")
                # Capture any newly selected answer into persistent memory
                try:
                    current_fields = await modal.inspect_fields()
                    for cf in current_fields:
                        val = cf.current_value
                        if val and val.lower() != "select an option":
                            ftype = cf.field_type.value if hasattr(cf.field_type, "value") else str(cf.field_type)
                            memory_service.save_form_answer(cf.label, val, ftype)
                except Exception:
                    pass
                return True

            return not (await modal.has_validation_errors())
    finally:
        HITLManager.clear()

    return False

async def is_linkedin_logged_in(page, context) -> bool:
    """Checks if the browser session is authenticated on LinkedIn."""
    try:
        cookies = await context.cookies(["https://www.linkedin.com"])
        for c in cookies:
            if c.get("name") == "li_at" and c.get("value"):
                return True
    except Exception:
        pass

    current_url = page.url.lower()
    if "/feed" in current_url:
        return True

    for anchor in [
        "div.global-nav__me",
        "img.global-nav__me-photo",
        "nav.global-nav",
        "button.global-nav__primary-link-me-menu-trigger",
        "button[aria-label*='Me']",
        "input.search-global-typeahead__input"
    ]:
        try:
            if await page.locator(anchor).first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    return False

async def is_naukri_logged_in(page, context) -> bool:
    """Checks if the browser session is authenticated on Naukri.com."""
    try:
        cookies = await context.cookies(["https://www.naukri.com"])
        for c in cookies:
            if c.get("name") in ["NkAuth", "nauk_user", "n_id"] and c.get("value"):
                return True
    except Exception:
        pass
    current_url = page.url.lower()
    if any(p in current_url for p in ["/mnjuser/profile", "/mnjuser/homepage"]):
        return True
    for sel in ["div.nI-gNb-drawer__user-name", "a[title='Logout']", "div.nI-gNb-drawer__icon"]:
        try:
            if await page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    return False

async def interactive_platform_signin(
    console: Optional[Console] = None,
    platform: str = "all"
) -> bool:
    """
    Guides the user to sign into LinkedIn and/or Naukri in the persistent browser.
    Saves cookies permanently into .browser_context so subsequent runs never ask for login again.
    """
    c = console or Console()
    c.print(Panel(
        "[bold cyan]🔑 Platform Sign-In & Authentication Manager[/bold cyan]\n\n"
        "[white]JobAgent runs with a dedicated persistent browser profile.\n"
        "Sign in once here, and your session cookies are permanently saved for all future runs.[/white]",
        border_style="cyan"
    ))

    user_data_path = get_browser_user_data_dir()
    user_data_path.mkdir(parents=True, exist_ok=True)
    c.print(f"[dim]Browser profile storage: [bold cyan]{user_data_path}[/bold cyan][/dim]")

    pw = await async_playwright().start()
    context = await launch_persistent_context_with_retry(
        pw=pw,
        user_data_dir=user_data_path,
        console=c,
        headless=False,
        slow_mo=100,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        no_viewport=True
    )

    page = context.pages[0] if context.pages else await context.new_page()
    await page.bring_to_front()

    # 1. LinkedIn Sign-In
    if platform in ["all", "both", "linkedin"]:
        c.print("\n[bold cyan]Checking LinkedIn authentication...[/bold cyan]")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2.0)

        if await is_linkedin_logged_in(page, context):
            c.print("[bold green]✓ LinkedIn: Session active and verified! (Already logged in)[/bold green]")
        else:
            c.print(Panel(
                "[bold yellow]🔑 Please Log Into LinkedIn in the Open Browser Window[/bold yellow]\n\n"
                "[white]• Enter your email and password in the browser window.\n"
                "• Complete any 2FA / verification code if requested.\n"
                "• The agent will automatically detect when login succeeds.[/white]\n\n"
                "[dim]Session cookies will be saved permanently.[/dim]",
                title="[bold yellow]LinkedIn Login[/bold yellow]",
                border_style="yellow"
            ))
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
            while not await is_linkedin_logged_in(page, context):
                await asyncio.sleep(2.0)
                if "checkpoint" in page.url or "challenge" in page.url:
                    c.print("[yellow]2FA / Security check detected. Please complete verification in browser...[/yellow]")
                    while "checkpoint" in page.url or "challenge" in page.url:
                        await asyncio.sleep(2.0)
                        if await is_linkedin_logged_in(page, context):
                            break
            c.print("[bold green]✓ LinkedIn: Login successful! Session saved permanently.[/bold green]")

    # 2. Naukri Sign-In
    if platform in ["all", "both", "naukri"]:
        c.print("\n[bold cyan]Checking Naukri.com authentication...[/bold cyan]")
        await page.goto("https://www.naukri.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(2.0)

        if await is_naukri_logged_in(page, context):
            c.print("[bold green]✓ Naukri.com: Session active and verified! (Already logged in)[/bold green]")
        else:
            c.print(Panel(
                "[bold yellow]🔑 Please Log Into Naukri.com in the Open Browser Window[/bold yellow]\n\n"
                "[white]• Enter your credentials in the browser window.\n"
                "• The agent will automatically detect when login succeeds.[/white]\n\n"
                "[dim]Session cookies will be saved permanently.[/dim]",
                title="[bold yellow]Naukri.com Login[/bold yellow]",
                border_style="yellow"
            ))
            await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded", timeout=45000)
            while not await is_naukri_logged_in(page, context):
                await asyncio.sleep(2.0)
                if any(p in page.url for p in ["/mnjuser/profile", "/mnjuser/homepage"]):
                    break
            c.print("[bold green]✓ Naukri.com: Login successful! Session saved permanently.[/bold green]")

    c.print("\n[bold green]✓ Platform authentication setup complete![/bold green]")
    c.print(f"[dim]All sessions permanently stored in: {user_data_path}[/dim]\n")
    await context.close()
    await pw.stop()
    return True

def compute_card_priority(card, profile, target_keyword: str, job_evaluator: JobEvaluator) -> tuple:
    """
    Ranks extracted job cards into priority tiers:
    Priority A: >=95% (tier 1)
    Priority B: 85-94% (tier 2)
    Priority C: 70-84% (tier 3)
    Low: <70% (tier 4)
    Returns (tier_rank, -prelim_score) for sorting in descending order.
    """
    role_check_roles = [target_keyword] + (profile.preferred_roles or []) + (getattr(profile, "target_roles", []) or [])
    role_score = job_evaluator._compute_role_score(card.title, role_check_roles)

    loc_score = 100.0
    if card.location:
        card_loc_lower = card.location.lower()
        if "remote" in card_loc_lower or "anywhere" in card_loc_lower:
            loc_score = 100.0
        elif any(pl.lower() in card_loc_lower for pl in (profile.preferred_locations or [])):
            loc_score = 100.0
        else:
            loc_score = 60.0

    prelim_score = (role_score * 0.75) + (loc_score * 0.25)
    if prelim_score >= 95.0:
        tier_rank = 1
    elif prelim_score >= 85.0:
        tier_rank = 2
    elif prelim_score >= 70.0:
        tier_rank = 3
    else:
        tier_rank = 4

    return (tier_rank, -prelim_score)


async def _process_linkedin_job_card(
    page,
    card,
    details_page,
    modal,
    job_evaluator,
    profile,
    target_keyword: str,
    effective_min_score: float,
    db_service,
    memory_service,
    form_agent,
    resume_path: str,
    prefs: dict,
    auto_approve: bool,
    dry_run: bool,
    console: Console,
    current_applied_count: int
) -> Optional[str]:
    """
    Evaluates and applies to a single LinkedIn job card.
    Returns summary string if successfully applied (or passed dry run), else None.
    """
    is_dup = await db_service.is_already_applied(job_id=card.job_id, job_url=card.job_url, company=card.company)
    if is_dup:
        console.print(f"[yellow]Skipping already processed: {card.title} @ {card.company}[/yellow]")
        return None

    # Fast title relevance pre-filter on extracted card
    role_check_roles = [target_keyword] + (profile.preferred_roles or []) + (getattr(profile, "target_roles", []) or [])
    card_role_score = job_evaluator._compute_role_score(card.title, role_check_roles)
    if card_role_score <= 25.0:
        console.print(f"[yellow]Skipping irrelevant job title '{card.title}' @ {card.company} (does not match target '{target_keyword}')[/yellow]")
        return None

    console.print(f"\n[bold white]----------------------------------------------------[/bold white]")
    console.print(f"[bold]Evaluating LinkedIn #{current_applied_count + 1}:[/bold] [bold cyan]{card.title}[/bold cyan] @ [bold]{card.company}[/bold] ({card.location})")

    details = await details_page.select_and_load_job(card)
    if not details.is_easy_apply:
        console.print("[dim]No active Easy Apply button. Skipping.[/dim]")
        return None

    # Evaluate Job Fit with Autonomous Decision Engine
    eval_res = await job_evaluator.evaluate_fit(details, profile)
    score = eval_res.match_breakdown.overall_score
    matched_skills = eval_res.match_breakdown.matched_skills
    missing_skills = eval_res.match_breakdown.missing_skills
    notes_text = eval_res.match_breakdown.reasoning

    # Record decision for UI dashboard
    try:
        from ui.server import record_evaluation_decision
        record_evaluation_decision(eval_res.to_decision_dict())
    except Exception:
        pass

    # Priority tier determination
    if score >= 95.0:
        priority_tier = "Priority A (>=95%)"
    elif score >= 85.0:
        priority_tier = "Priority B (85-94%)"
    elif score >= 70.0:
        priority_tier = "Priority C (70-84%)"
    else:
        priority_tier = "Low (<70%)"

    dec_val = eval_res.decision.value
    border_col = "green" if dec_val == "APPLY" else ("yellow" if dec_val == "REVIEW" else "red")

    panel_text = (
        f"[bold white]Job:[/bold white] [bold cyan]{card.title}[/bold cyan] @ [bold white]{card.company}[/bold white] ({card.location})\n"
        f"[bold white]Autonomous Decision:[/bold white] [{border_col} bold]{dec_val}[/{border_col} bold]  |  "
        f"[bold white]Match Score:[/bold white] [{border_col} bold]{score}%[/{border_col} bold]  |  "
        f"[bold white]Tier:[/bold white] [cyan]{priority_tier}[/cyan]\n"
        f"[dim]Factor Breakdown:[/dim] "
        f"Tech: [bold]{eval_res.match_breakdown.technical_fit}%[/bold] (50%) | "
        f"Exp: [bold]{eval_res.match_breakdown.experience_fit}%[/bold] (20%) | "
        f"Role: [bold]{eval_res.match_breakdown.role_fit}%[/bold] (15%) | "
        f"Prefs: [bold]{eval_res.match_breakdown.preference_fit}%[/bold] (10%) | "
        f"Risk: [bold]{eval_res.match_breakdown.risk_score}%[/bold] (5%)\n"
        f"[bold]Matched Skills:[/bold] [green]{', '.join(matched_skills) if matched_skills else 'None'}[/green]\n"
        f"[bold]Missing Skills:[/bold] [yellow]{', '.join(missing_skills) if missing_skills else 'None'}[/yellow]"
    )
    if eval_res.match_breakdown.incompatible_skills:
        panel_text += f"\n[bold red]Incompatible Stack:[/bold red] [red]{', '.join(eval_res.match_breakdown.incompatible_skills)}[/red]"
    panel_text += f"\n[dim]Reasoning: {notes_text}[/dim]"

    console.print(Panel(
        panel_text,
        title=f"[bold]Autonomous Job Decision Engine — [{border_col}]{dec_val}[/{border_col}][/bold]",
        border_style=border_col
    ))

    # Route REVIEW decisions through HITL or skip
    if eval_res.decision == DecisionType.REVIEW:
        if not auto_approve:
            console.print(f"[bold yellow]⚠️ Job flagged for Human Review: {notes_text}[/bold yellow]")
            hitl_future = HITLManager.request_human_input(
                hitl_type="job_review",
                title=f"⚠️ Human Review: {card.title} @ {card.company}",
                message=f"Autonomous engine flagged this job for review (Score: {score}%). {notes_text}. Proceed with application?",
                field_label="Job Review",
                options=["Apply", "Skip"],
                suggested_value="Apply" if score >= effective_min_score else "Skip",
                timeout_sec=120
            )
            is_cli_interactive = bool(sys.stdin and sys.stdin.isatty())
            review_choice = "skip"
            if is_cli_interactive:
                try:
                    ans = Prompt.ask("\n[bold yellow]Apply to this job despite review flag? [y/n]: [/bold yellow]", default="n")
                    review_choice = "apply" if ans.lower() == "y" else "skip"
                    HITLManager.resolve_request("resolved", review_choice)
                except Exception:
                    review_choice = "skip"
            else:
                poll_start = asyncio.get_event_loop().time()
                try:
                    while (asyncio.get_event_loop().time() - poll_start) < 120:
                        if hitl_future.done():
                            try:
                                res = hitl_future.result()
                                act = str(res.get("action", "")).lower()
                                val = str(res.get("value", "")).lower()
                                if act in ["apply", "submit", "resolved"] or "apply" in val:
                                    review_choice = "apply"
                                else:
                                    review_choice = "skip"
                            except Exception:
                                pass
                            break
                        if page.is_closed():
                            review_choice = "skip"
                            break
                        await asyncio.sleep(1.0)
                finally:
                    HITLManager.clear()

            if review_choice != "apply":
                console.print(f"[yellow]Skipping post per user review decision ({notes_text})[/yellow]")
                await db_service.record_application(ApplicationRecord(
                    job_id=card.job_id, job_title=card.title, company=card.company,
                    location=card.location, job_url=card.job_url, platform="LinkedIn", match_score=score,
                    status=ApplicationStatus.SKIPPED_LOW_SCORE, notes=f"REVIEW skipped by user: {notes_text}"
                ))
                return None
        else:
            if score < effective_min_score:
                console.print(f"[yellow]Skipping REVIEW post below minimum score ({score}% < {effective_min_score}%) ({notes_text})[/yellow]")
                await db_service.record_application(ApplicationRecord(
                    job_id=card.job_id, job_title=card.title, company=card.company,
                    location=card.location, job_url=card.job_url, platform="LinkedIn", match_score=score,
                    status=ApplicationStatus.SKIPPED_LOW_SCORE, notes=notes_text
                ))
                return None

    elif eval_res.decision in (DecisionType.SKIP, DecisionType.REJECT, DecisionType.BLOCKED) or score < effective_min_score:
        console.print(f"[yellow]Skipping post ({notes_text})[/yellow]")
        await db_service.record_application(ApplicationRecord(
            job_id=card.job_id, job_title=card.title, company=card.company,
            location=card.location, job_url=card.job_url, platform="LinkedIn", match_score=score,
            status=ApplicationStatus.SKIPPED_LOW_SCORE, notes=notes_text
        ))
        return None

    console.print("[bold green]Clicking Easy Apply button...[/bold green]")
    await details_page.click_easy_apply()
    await asyncio.sleep(2.0)

    if not await modal.is_modal_open(timeout_ms=5000):
        page_easy_apply = page.locator("button:visible:has-text('Easy Apply'), div.jobs-s-apply button:visible").first
        if await page_easy_apply.is_visible(timeout=2000):
            await page_easy_apply.click(force=True)
            await asyncio.sleep(2.0)

    if not await modal.is_modal_open(timeout_ms=5000):
        console.print("[yellow]Could not open modal. Skipping.[/yellow]")
        return None

    # Form Filling
    step_num = 0
    while step_num < 10:
        step_num += 1
        if await modal.is_review_step():
            break

        # Progressively fill fields on this step from top to bottom
        scroll_passes = 0
        max_scrolls = 4
        while scroll_passes <= max_scrolls:
            fields = await modal.inspect_fields()
            for f in fields:
                # Skip pre-filled text or numeric fields only
                if (
                    f.field_type in [FormFieldType.TEXT, FormFieldType.NUMBER]
                    and f.current_value
                    and len(f.current_value.strip()) > 1
                ):
                    continue
                if (
                    f.field_type in [FormFieldType.RADIO, FormFieldType.SELECT]
                    and f.current_value
                    and len(f.current_value.strip()) > 0
                    and f.current_value.lower() != "select an option"
                ):
                    continue

                val, needs_hitl = await form_agent.resolve_field_value(f, profile, job_description=details.description_text)
                if needs_hitl or (not val and f.field_type != FormFieldType.FILE_UPLOAD):
                    hitl_val = await handle_new_field_hitl(
                        modal=modal,
                        field=f,
                        suggested_val=val,
                        memory_service=memory_service,
                        console=console
                    )
                    if hitl_val:
                        val = hitl_val

                if val:
                    filled = await modal.fill_field(f, val, resume_file_path=resume_path)
                    if filled:
                        clean_lbl = f.label.replace('*', '').strip()
                        console.print(f"  [dim]• Field '{clean_lbl[:35]}': [bold cyan]{val}[/bold cyan][/dim]")
                elif f.field_type == FormFieldType.FILE_UPLOAD:
                    filled = await modal.fill_field(f, "", resume_file_path=resume_path)
                    if filled:
                        console.print(f"  [dim]• Attached resume: [bold cyan]{Path(resume_path).name}[/bold cyan][/dim]")

            # Scroll modal down to reveal next fields in the list
            scrolled = await modal.scroll_modal_content(step=450)
            if not scrolled:
                break
            scroll_passes += 1
            await asyncio.sleep(0.3)

        # Ensure modal is fully scrolled to reveal all fields down to bottom before clicking next
        await modal.scroll_modal_to_bottom()
        await asyncio.sleep(0.4)

        advanced = await modal.click_next() or await modal.click_review()
        if not advanced:
            if await modal.is_review_step():
                break
            await asyncio.sleep(0.8)
            if await modal.has_validation_errors():
                errors = await modal.get_validation_errors()
                if errors:
                    console.print(f"  [yellow]Validation errors on step: {', '.join(errors)}[/yellow]")

                retry_fields = await modal.inspect_fields()
                has_larger_100_error = any("larger than 100" in e.lower() or "whole number larger" in e.lower() for e in errors)
                has_selection_error = any("make a selection" in e.lower() or "select" in e.lower() or "required" in e.lower() or "valid answer" in e.lower() for e in errors)
                has_whole_number_error = any("whole number" in e.lower() for e in errors)
                has_decimal_gt0_error = any("larger than 0" in e.lower() or "decimal number" in e.lower() for e in errors)

                for f in retry_fields:
                    f_err = (f.validation_error or "").lower()
                    f_label_low = f.label.lower()

                    # 1. Error-based change: "Enter a whole number larger than 100"
                    if has_larger_100_error or "larger than 100" in f_err:
                        cur_val_str = f.current_value or ""
                        try:
                            val_num = float(re.sub(r'[^\d.]', '', cur_val_str))
                        except (ValueError, TypeError):
                            val_num = 0.0

                        is_ctc_field = any(w in f_label_low for w in ["ctc", "salary", "compensation"])
                        if is_ctc_field or (0 < val_num <= 100):
                            if "current" in f_label_low:
                                prefs_inr = memory_service.get_preference("current_ctc_inr")
                                new_val = str(int(prefs_inr)) if prefs_inr else str(int(profile.professional.current_lpa * 100000))
                            elif "expected" in f_label_low or "desired" in f_label_low:
                                prefs_exp = memory_service.get_preference("expected_ctc_inr")
                                new_val = str(int(prefs_exp)) if prefs_exp else str(int(profile.professional.expected_lpa * 100000))
                            elif val_num > 0:
                                new_val = str(int(round(val_num * 100000)))
                            else:
                                new_val = str(int(profile.professional.current_lpa * 100000))

                            console.print(f"  [bold green]✓ Auto-correcting entry based on error 'Enter a whole number larger than 100':[/bold green] '{f.label[:35]}' [{cur_val_str}] -> [bold cyan]{new_val}[/bold cyan]")
                            await modal.fill_field(f, new_val, resume_file_path=resume_path)
                            memory_service.save_form_answer(f.label, new_val, "number")
                            continue

                    # 2. Error-based change: "Enter a whole number" (e.g. for years 3.9 -> 4)
                    if has_whole_number_error or "whole number" in f_err:
                        cur_val_str = f.current_value or ""
                        if "." in cur_val_str:
                            try:
                                rounded_val = str(int(round(float(cur_val_str))))
                                console.print(f"  [bold green]✓ Rounding decimal to whole number based on validation error:[/bold green] '{f.label[:35]}' [{cur_val_str}] -> [bold cyan]{rounded_val}[/bold cyan]")
                                await modal.fill_field(f, rounded_val, resume_file_path=resume_path)
                                memory_service.save_form_answer(f.label, rounded_val, "number")
                                continue
                            except ValueError:
                                pass

                    # 3. Error-based change: "Enter a decimal number larger than 0.0" (e.g. invalid 'Yes' or '0' in numeric field)
                    if has_decimal_gt0_error or "larger than 0" in f_err or "decimal number" in f_err:
                        cur_val_str = f.current_value or ""
                        is_positive_num = False
                        try:
                            chk_f = float(re.sub(r'[^\d.]', '', cur_val_str))
                            if chk_f > 0.0:
                                is_positive_num = True
                        except (ValueError, TypeError):
                            is_positive_num = False

                        if not is_positive_num:
                            if any(w in f_label_low for w in ["how soon", "days", "notice", "join"]):
                                new_val = str(profile.professional.notice_period_days)
                            else:
                                # Extract target skill or default to realistic experience
                                m_sk = re.search(r'(?:with|in|using)\s+([^?*:]+)', f_label_low)
                                sk_name = m_sk.group(1).strip() if m_sk else f_label_low
                                matched_exp = form_agent._match_skill_experience(sk_name, profile)
                                if matched_exp > 0.0:
                                    new_val = str(matched_exp)
                                elif any(s.lower() in f_label_low for s in profile.skills):
                                    new_val = "2.0"
                                else:
                                    new_val = "1.0"

                            console.print(f"  [bold green]✓ Auto-correcting entry based on error 'Enter a decimal number larger than 0.0':[/bold green] '{f.label[:35]}' [{cur_val_str}] -> [bold cyan]{new_val}[/bold cyan]")
                            await modal.fill_field(f, new_val, resume_file_path=resume_path)
                            memory_service.save_form_answer(f.label, new_val, "number")
                            continue

                    # 4. Error-based change: Missing Selection on Select / Radio (e.g. Face-to-Face round)
                    if (has_selection_error or "required" in f_err or "select an option" in f_err or "valid answer" in f_err) and f.field_type in [FormFieldType.SELECT, FormFieldType.RADIO]:
                        if f.options:
                            opt_low = [o.strip().lower() for o in f.options]
                            if "yes" in opt_low:
                                yes_idx = opt_low.index("yes")
                                sel_opt = f.options[yes_idx]
                                console.print(f"  [bold green]✓ Auto-selecting 'Yes' for required choice based on validation error:[/bold green] '{f.label[:35]}'")
                                await modal.fill_field(f, sel_opt, resume_file_path=resume_path)
                                memory_service.save_form_answer(f.label, sel_opt, f.field_type.value)
                                continue

                    # 3. Dynamic resolution fallback for other fields
                    val, needs_hitl = await form_agent.resolve_field_value(f, profile, job_description=details.description_text)
                    if needs_hitl or not val:
                        val = await handle_new_field_hitl(
                            modal=modal,
                            field=f,
                            suggested_val=val or ("4" if "experience" in f.label.lower() else "Yes"),
                            memory_service=memory_service,
                            console=console
                        )
                    if val:
                        await modal.fill_field(f, val, resume_file_path=resume_path)
                await asyncio.sleep(0.5)

            # Re-attempt advancing step after error recovery
            await modal.scroll_modal_to_bottom()
            await asyncio.sleep(0.3)
            if not (await modal.click_next() or await modal.click_review()):
                if await modal.is_review_step():
                    break
                rem_errors = await modal.get_validation_errors()
                if rem_errors:
                    console.print(f"  [red]Persistent validation errors: {', '.join(rem_errors)}[/red]")
                
                # Pause and wait for user to provide data / select in open browser window
                resolved = await wait_for_user_to_resolve_errors(
                    modal=modal,
                    errors=rem_errors or ["Please make a selection"],
                    memory_service=memory_service,
                    console=console
                )
                if not resolved:
                    console.print("[yellow]Could not advance step after waiting for user input. Closing modal.[/yellow]")
                    await modal.dismiss_modal()
                    break

    # Review & Submit Step
    result_summary = None
    if await modal.is_review_step():
        cur_inr = prefs.get('current_ctc_inr', int(profile.professional.current_lpa * 100000))
        exp_inr = prefs.get('expected_ctc_inr', int(profile.professional.expected_lpa * 100000))

        table = Table(title="[bold green]HITL Review: LinkedIn Application Ready[/bold green]")
        table.add_column("Property", style="bold white")
        table.add_column("Details", style="cyan")
        table.add_row("Platform", "LinkedIn")
        table.add_row("Job Title", card.title)
        table.add_row("Company", card.company)
        table.add_row("Current CTC", f"INR {cur_inr:,} ({profile.professional.current_lpa} LPA)")
        table.add_row("Expected CTC", f"INR {exp_inr:,} ({profile.professional.expected_lpa} LPA)")
        table.add_row("Notice Period", f"{profile.professional.notice_period_days} days")
        console.print(table)

        proceed = "y"
        if not auto_approve:
            hitl_future = HITLManager.request_human_input(
                hitl_type="submit_approval",
                title=f"Review & Submit: {card.title} @ {card.company}",
                message=f"Application ready to submit for {card.title} at {card.company}. Current CTC: INR {cur_inr:,} | Expected CTC: INR {exp_inr:,} | Notice: {profile.professional.notice_period_days} days. Please approve submission or skip.",
                suggested_value="submit",
                timeout_sec=120
            )
            is_cli_interactive = bool(sys.stdin and sys.stdin.isatty())
            if is_cli_interactive:
                try:
                    ans = Prompt.ask("\n[bold green]Submit application? [y] Submit / [n] Skip: [/bold green]", default="y")
                    proceed = "y" if ans.lower() == "y" else "n"
                    HITLManager.resolve_request("submit" if proceed == "y" else "skip")
                except Exception:
                    proceed = "y"
            else:
                poll_start = asyncio.get_event_loop().time()
                timeout_sec = 120
                proceed = "n"
                try:
                    while (asyncio.get_event_loop().time() - poll_start) < timeout_sec:
                        if hitl_future.done():
                            try:
                                res = hitl_future.result()
                                if res.get("action") in ["submit", "resolved", "answer"]:
                                    proceed = "y"
                                else:
                                    proceed = "n"
                            except Exception:
                                pass
                            break

                        if modal.page.is_closed():
                            proceed = "n"
                            break

                        if not await modal.is_modal_open(timeout_ms=500):
                            proceed = "submitted_in_browser"
                            break

                        await asyncio.sleep(1.0)
                finally:
                    HITLManager.clear()

        if proceed == "submitted_in_browser":
            console.print("[bold green][OK] Application was submitted directly in browser![/bold green]")
            status = ApplicationStatus.SUBMITTED
            result_summary = f"LinkedIn: {card.title} @ {card.company}"
            await db_service.record_application(ApplicationRecord(
                job_id=card.job_id, job_title=card.title, company=card.company,
                location=card.location, job_url=card.job_url, platform="LinkedIn", match_score=score,
                status=status, notes="Applied directly in browser by user."
            ))
        elif proceed.lower() == "y":
            if dry_run:
                console.print("[bold yellow][DRY RUN] Simulated submit (modal closed).[/bold yellow]")
                await modal.dismiss_modal()
                status = ApplicationStatus.DRY_RUN_PASSED
                result_summary = f"LinkedIn: {card.title} @ {card.company} [DRY RUN]"
            else:
                console.print("[bold green]Submitting LinkedIn application...[/bold green]")
                submitted = await modal.click_submit()
                if submitted:
                    console.print("[bold green][OK] Successfully applied on LinkedIn![/bold green]")
                    status = ApplicationStatus.SUBMITTED
                    result_summary = f"LinkedIn: {card.title} @ {card.company}"
                else:
                    await modal.dismiss_modal()
                    status = ApplicationStatus.FAILED

            await db_service.record_application(ApplicationRecord(
                job_id=card.job_id, job_title=card.title, company=card.company,
                location=card.location, job_url=card.job_url, platform="LinkedIn", match_score=score,
                status=status, notes="Applied automatically via LinkedIn."
            ))
        else:
            console.print("[yellow]Skipped by user.[/yellow]")
            await modal.dismiss_modal()
    else:
        await modal.dismiss_modal()

    await asyncio.sleep(1.5)
    return result_summary


async def run_simple_agent(
    keyword: Optional[str] = None,
    location: Optional[str] = None,
    max_jobs: int = 5,
    min_score: float = 0.0,
    platform: str = "all",
    date_posted: str = "24h",
    sort_by: str = "date",
    remote_only: bool = False,
    email: Optional[str] = None,
    password: Optional[str] = None,
    auto_approve: bool = False,
    dry_run: bool = False,
    api_key: Optional[str] = None,
    llm_provider: str = "auto",
    llm_model: Optional[str] = None
):
    console = Console()
    setup_service = SetupService(console)
    memory_service = MemoryService()

    # 1. Check if user profile and keys are configured
    allow_offline = (llm_provider == "heuristic")
    if not setup_service.is_setup_complete(runtime_api_key=api_key, allow_offline=allow_offline):
        console.print(Panel(
            "[bold yellow]Initial setup required.[/bold yellow]\n"
            "[dim]Candidate profile, resume, or API key not yet fully configured.[/dim]\n"
            "[dim]Launching interactive setup wizard...[/dim]",
            border_style="yellow"
        ))
        await setup_service.run_setup_wizard()

    profile_loader = ProfileLoader()
    profile = profile_loader.profile
    prefs = memory_service.get_all_preferences()

    # Determine runtime parameters (CLI args override saved preferences)
    target_keyword = keyword or (profile.preferred_roles[0] if profile.preferred_roles else "Software Engineer")
    target_location = location or (profile.preferred_locations[0] if profile.preferred_locations else "Remote")
    effective_platform = platform or ("all" if len(prefs.get("preferred_platforms", [])) > 1 else prefs.get("preferred_platforms", ["all"])[0])
    effective_date_filter = date_posted or prefs.get("default_date_filter", "24h")
    effective_min_score = min_score if min_score > 0 else (profile.job_preferences.minimum_match_score or 60.0)

    # Format status dashboard
    llm_key = api_key or settings.GEMINI_API_KEY or settings.OPENAI_API_KEY
    llm_status = f"[bold green]Active ({llm_provider})[/bold green]" if llm_key else "[yellow]Heuristic NLP (Offline)[/yellow]"
    
    notes = memory_service.get_conversation_notes()
    recent_rules = "\n".join([f"  [dim]•[/dim] {n}" for n in notes[-4:]]) if notes else "  [dim]• Default rules loaded[/dim]"

    console.print(Panel.fit(
        f"[bold cyan]AI Job Application Agent (Multi-Platform)[/bold cyan]\n"
        f"[dim]Candidate:[/dim] [bold white]{profile.personal.full_name}[/bold white] ({profile.professional.designation}, {profile.professional.total_experience_years} yrs)\n"
        f"[dim]Target Role:[/dim] [bold yellow]{target_keyword}[/bold yellow] in [bold yellow]{target_location}[/bold yellow]\n"
        f"[dim]Platforms:[/dim] [bold cyan]{effective_platform.upper()}[/bold cyan] | [dim]Freshness:[/dim] [bold]{effective_date_filter}[/bold] | [dim]Min Score:[/dim] [bold]{effective_min_score}%[/bold]\n"
        f"[dim]Submission Mode:[/dim] {'[bold green]Auto-Apply (Autonomous)[/bold green]' if auto_approve else '[bold yellow]Manual Approval (HITL)[/bold yellow]'}\n"
        f"[dim]LLM Engine:[/dim] {llm_status}\n"
        f"[dim]Agent Memory Context:[/dim]\n{recent_rules}\n"
        f"[dim]Execution Mode:[/dim] {'[bold red]DRY RUN[/bold red]' if dry_run else '[bold green]LIVE APPLY[/bold green]'}",
        border_style="cyan"
    ))

    global CURRENT_BROWSER_CONTEXT, CURRENT_PW, CURRENT_DB_SERVICE

    # 2. Initialize Database, LLM & Agents
    db_service = DatabaseService()
    await db_service.init_db()
    CURRENT_DB_SERVICE = db_service
    llm_service = LLMService(api_key=api_key, provider=llm_provider, model=llm_model)
    job_evaluator = JobEvaluator(llm_service)
    memory_service = MemoryService()
    resume_service = ResumeService()
    resume_path = resume_service.get_resume_path(profile)
    form_agent = FormAgent(llm_service, memory_service, resume_service)

    # 3. Launch Visible Desktop Browser
    user_data_path = get_browser_user_data_dir()
    user_data_path.mkdir(parents=True, exist_ok=True)
    console.print(f"\n[dim]Launching persistent desktop browser (profile: [bold cyan]{user_data_path}[/bold cyan])...[/dim]")

    pw = await async_playwright().start()
    CURRENT_PW = pw
    context = await launch_persistent_context_with_retry(
        pw=pw,
        user_data_dir=user_data_path,
        console=console,
        headless=False,
        slow_mo=120,
        args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        no_viewport=True
    )

    CURRENT_BROWSER_CONTEXT = context
    page = context.pages[0] if context.pages else await context.new_page()
    await page.bring_to_front()

    platforms_to_run = ["linkedin", "naukri"] if effective_platform in ["all", "both"] else [effective_platform]
    total_applied = 0
    jobs_summary = []

    # =========================================================================
    # PLATFORM 1: LINKEDIN EASY APPLY
    # =========================================================================
    if "linkedin" in platforms_to_run:
        console.print("\n[bold cyan]=================== STEP 1: LINKEDIN AUTHENTICATION ===================[/bold cyan]")
        console.print("[dim]Checking LinkedIn session...[/dim]")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)

        is_already_signed_in = await is_linkedin_logged_in(page, context)

        if not is_already_signed_in:
            if email and password:
                console.print("[bold yellow]Attempting automated login with provided credentials...[/bold yellow]")
                await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(1.5)
                email_selector = "input#username:visible, input#email-or-phone:visible, input[name='session_key']:visible"
                password_selector = "input#password:visible, input[name='session_password']:visible"
                try:
                    await page.locator(email_selector).first.fill(email)
                    await page.locator(password_selector).first.fill(password)
                    sign_in_btn = page.locator("button:visible:has-text('Sign in'), button[type='submit']:visible").first
                    if await sign_in_btn.is_visible(timeout=2000):
                        await sign_in_btn.click()
                    else:
                        await page.locator(password_selector).first.press("Enter")
                    await asyncio.sleep(4.0)
                except Exception as e:
                    logger.warning(f"Auto login error: {e}")

            if not await is_linkedin_logged_in(page, context):
                console.print(Panel(
                    "[bold yellow]🔑 LinkedIn Sign-In Required[/bold yellow]\n\n"
                    "[white]Please log into LinkedIn in the open browser window.\n"
                    "• Enter your email and password (and 2FA code if prompted).\n"
                    "• The agent will automatically detect when you are signed in.\n"
                    "• Your session cookies will be PERMANENTLY SAVED for all future runs.[/white]",
                    title="[bold yellow]Account Authentication[/bold yellow]",
                    border_style="yellow"
                ))
                if "/login" not in page.url:
                    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=45000)

                while not await is_linkedin_logged_in(page, context):
                    await asyncio.sleep(2.0)
                    if "checkpoint" in page.url or "challenge" in page.url:
                        console.print(Panel(
                            "[bold red]! 2FA / SECURITY VERIFICATION REQUIRED ![/bold red]\n"
                            "[bold white]Approve prompt on your LinkedIn mobile app or enter SMS code in browser.[/bold white]\n"
                            "[dim]Agent will automatically resume upon approval.[/dim]",
                            border_style="yellow"
                        ))
                        while "checkpoint" in page.url or "challenge" in page.url:
                            await asyncio.sleep(2.0)
                            if await is_linkedin_logged_in(page, context):
                                break

                console.print("[bold green]✓ LinkedIn Sign-In verified! Session permanently saved.[/bold green]")
                await asyncio.sleep(1.0)
        else:
            console.print("[bold green]✓ LinkedIn: Session active and verified! (Already logged in)[/bold green]")

        console.print("\n[bold cyan]=================== STEP 2: LINKEDIN JOB SEARCH & APPLICATION ===================[/bold cyan]")
        console.print(f"[bold green][OK] Searching LinkedIn for '{target_keyword}' in '{target_location}' (Freshness: {effective_date_filter})...[/bold green]")
        search_page = JobSearchPage(page)
        details_page = JobDetailsPage(page)
        modal = EasyApplyModal(page)
        linkedin_applied = 0

        page_num = 1
        start_offset = 0
        max_search_pages = max(1, (max_jobs + 9) // 10 * 3)
        consecutive_empty_pages = 0

        need_navigate = True
        while linkedin_applied < max_jobs and page_num <= max_search_pages:
            page_banner = f"--- LinkedIn Search Page {page_num} (Applied: {linkedin_applied}/{max_jobs}) ---"
            console.print(f"\n[bold cyan]{page_banner}[/bold cyan]")

            if need_navigate:
                await search_page.navigate_to_search(
                    keyword=target_keyword,
                    location=target_location,
                    easy_apply_only=True,
                    date_posted=effective_date_filter,
                    sort_by=sort_by,
                    remote_only=remote_only,
                    start=start_offset
                )
                await asyncio.sleep(2.0)

            linkedin_cards = await search_page.extract_job_cards(max_cards=25)
            if not linkedin_cards:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    console.print("[dim]No more job listings found on LinkedIn.[/dim]")
                    break
                start_offset += 25
                page_num += 1
                need_navigate = True
                continue

            consecutive_empty_pages = 0
            console.print(f"[dim]Found {len(linkedin_cards)} Easy Apply job listings on page {page_num}. Prioritizing by fit...[/dim]")
            linkedin_cards.sort(key=lambda c: compute_card_priority(c, profile, target_keyword, job_evaluator))

            for card in linkedin_cards:
                if linkedin_applied >= max_jobs:
                    break

                try:
                    applied_summary = await _process_linkedin_job_card(
                        page=page,
                        card=card,
                        details_page=details_page,
                        modal=modal,
                        job_evaluator=job_evaluator,
                        profile=profile,
                        target_keyword=target_keyword,
                        effective_min_score=effective_min_score,
                        db_service=db_service,
                        memory_service=memory_service,
                        form_agent=form_agent,
                        resume_path=resume_path,
                        prefs=prefs,
                        auto_approve=auto_approve,
                        dry_run=dry_run,
                        console=console,
                        current_applied_count=linkedin_applied
                    )
                except Exception as e:
                    err_msg = str(e).lower()
                    if "closed" in err_msg or "target" in err_msg:
                        console.print("\n[bold yellow]🛑 Browser window was closed by user. Terminating LinkedIn application process gracefully.[/bold yellow]")
                        break
                    logger.error(f"Error processing LinkedIn job card: {e}")
                    applied_summary = None

                if applied_summary:
                    linkedin_applied += 1
                    total_applied += 1
                    jobs_summary.append(applied_summary)

            if page.is_closed() or (getattr(page, "context", None) and page.context.is_closed()):
                break

            if linkedin_applied < max_jobs:
                console.print("[dim]Job list on current screen completed. Scrolling down to click next page...[/dim]")
                next_clicked = await search_page.click_next_page()
                if next_clicked:
                    need_navigate = False
                    page_num += 1
                    await asyncio.sleep(2.0)
                else:
                    start_offset += 25
                    page_num += 1
                    need_navigate = True
            else:
                page_num += 1

    # =========================================================================
    # PLATFORM 2: NAUKRI.COM QUICK APPLY
    # =========================================================================
    if "naukri" in platforms_to_run:
        console.print("\n[bold cyan]=================== STEP 1: NAUKRI.COM AUTHENTICATION ===================[/bold cyan]")
        console.print("[dim]Checking Naukri.com session...[/dim]")
        await page.goto("https://www.naukri.com/", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(1.5)

        if not await is_naukri_logged_in(page, context):
            console.print(Panel(
                "[bold yellow]🔑 Naukri.com Sign-In Required[/bold yellow]\n\n"
                "[white]Please log into Naukri.com in the open browser window.\n"
                "• Enter your email/mobile and password.\n"
                "• The agent will automatically detect when you are signed in.\n"
                "• Your session cookies will be PERMANENTLY SAVED for all future runs.[/white]",
                title="[bold yellow]Account Authentication[/bold yellow]",
                border_style="yellow"
            ))
            await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded", timeout=45000)
            while not await is_naukri_logged_in(page, context):
                await asyncio.sleep(2.0)
                if any(p in page.url for p in ["/mnjuser/profile", "/mnjuser/homepage"]):
                    break
            console.print("[bold green]✓ Naukri.com Sign-In verified! Session permanently saved.[/bold green]")
            await asyncio.sleep(1.0)
        else:
            console.print("[bold green]✓ Naukri.com: Session active and verified! (Already logged in)[/bold green]")

        console.print("\n[bold cyan]=================== STEP 2: NAUKRI.COM JOB SEARCH & APPLICATION ===================[/bold cyan]")
        console.print(f"[dim]Searching Naukri.com for '{target_keyword}' in '{target_location}' (Freshness: {effective_date_filter})...[/dim]")
        
        naukri_page = NaukriPage(page)
        await naukri_page.navigate_and_search(
            keyword=target_keyword,
            location=target_location,
            date_posted=effective_date_filter,
            remote_only=remote_only
        )
        await asyncio.sleep(2.0)

        naukri_cards = await naukri_page.extract_job_cards(max_cards=max_jobs * 2)
        console.print(f"[dim]Extracted {len(naukri_cards)} job cards from Naukri.com. Prioritizing by fit...[/dim]")
        naukri_cards.sort(key=lambda c: compute_card_priority(c, profile, target_keyword, job_evaluator))

        naukri_applied = 0
        for card in naukri_cards:
            if naukri_applied >= max_jobs:
                break

            is_dup = await db_service.is_already_applied(job_id=card.job_id, job_url=card.job_url, company=card.company)
            if is_dup:
                console.print(f"[yellow]Skipping already processed: {card.title} @ {card.company}[/yellow]")
                continue

            # Fast title relevance pre-filter on extracted card
            role_check_roles = [target_keyword] + (profile.preferred_roles or [])
            card_role_score = job_evaluator._compute_role_score(card.title, role_check_roles)
            if card_role_score <= 25.0:
                console.print(f"[yellow]Skipping irrelevant job title '{card.title}' @ {card.company} (does not match target '{target_keyword}')[/yellow]")
                continue

            console.print(f"\n[bold white]----------------------------------------------------[/bold white]")
            console.print(f"[bold]Evaluating Naukri #{naukri_applied + 1}:[/bold] [bold cyan]{card.title}[/bold cyan] @ [bold]{card.company}[/bold] ({card.location})")

            # Apply directly via NaukriPage with skill evaluation
            try:
                apply_res = await naukri_page.open_job_and_apply(
                    card=card,
                    form_agent=form_agent,
                    profile=profile,
                    job_evaluator=job_evaluator,
                    min_score=effective_min_score,
                    auto_approve=auto_approve,
                    dry_run=dry_run
                )
            except Exception as e:
                err_msg = str(e).lower()
                if "closed" in err_msg or "target" in err_msg:
                    console.print("\n[bold yellow]🛑 Browser window was closed by user. Terminating Naukri application process gracefully.[/bold yellow]")
                    break
                logger.error(f"Error processing Naukri job card: {e}")
                continue

            match_score = apply_res.get("match_score", 0.0)
            matched_skills = apply_res.get("matched_skills", [])
            missing_skills = apply_res.get("missing_skills", [])
            reasoning = apply_res.get("reasoning", apply_res.get("message", ""))

            console.print(f"Match Score: [bold green]{match_score}%[/bold green]")
            if matched_skills:
                console.print(f"Skills matched: [green]{', '.join(matched_skills)}[/green]")
            if missing_skills:
                console.print(f"Skills missing: [yellow]{', '.join(missing_skills)}[/yellow]")

            if apply_res.get("status") == "skipped_low_score":
                console.print(f"[yellow]Skipping post: {reasoning}[/yellow]")
                await db_service.record_application(ApplicationRecord(
                    job_id=card.job_id, job_title=card.title, company=card.company,
                    location=card.location, job_url=card.job_url, platform="Naukri",
                    match_score=match_score,
                    status=ApplicationStatus.SKIPPED_LOW_SCORE,
                    notes=reasoning
                ))
                continue

            if apply_res.get("applied", False):
                if dry_run:
                    console.print(f"[bold yellow][DRY RUN] Simulated Naukri application for: {card.title}[/bold yellow]")
                    status = ApplicationStatus.DRY_RUN_PASSED
                else:
                    console.print(f"[bold green][OK] Successfully applied on Naukri for: {card.title}![/bold green]")
                    status = ApplicationStatus.SUBMITTED
                    naukri_applied += 1
                    total_applied += 1
                    jobs_summary.append(f"Naukri: {card.title} @ {card.company}")
            else:
                console.print(f"[yellow]Naukri application status: {apply_res.get('message', 'Skipped')}[/yellow]")
                status = ApplicationStatus.SKIPPED_MANUAL_REQUIRED

            await db_service.record_application(ApplicationRecord(
                job_id=card.job_id, job_title=card.title, company=card.company,
                location=card.location, job_url=card.job_url, platform="Naukri", match_score=match_score,
                status=status, notes=f"Naukri: {apply_res.get('message', '')}"
            ))
            await asyncio.sleep(1.5)
            if page.is_closed() or (getattr(page, "context", None) and page.context.is_closed()):
                break

    # 4. Session Wrap-Up & Persistent Memory Update
    memory_service.record_session({
        "applied_count": total_applied,
        "platforms": platforms_to_run,
        "target_keyword": target_keyword,
        "target_location": target_location,
        "date_filter": effective_date_filter,
        "dry_run": dry_run,
        "jobs_summary": jobs_summary
    })

    console.print(f"\n[bold green]Finished session! Total applied across platforms: {total_applied} jobs.[/bold green]")
    await stop_current_session()

def main():
    parser = argparse.ArgumentParser(description="Universal Multi-Platform AI Job Application Agent (LinkedIn + Naukri)")
    parser.add_argument("--keyword", type=str, default=None, help="Target job role (e.g. QA Automation, SDET, Frontend, Backend)")
    parser.add_argument("--location", type=str, default=None, help="Job location (e.g. Hyderabad, Bangalore, Remote)")
    parser.add_argument("--platform", type=str, choices=["linkedin", "naukri", "all"], default="all", help="Target platform to apply (default: all)")
    parser.add_argument("--date-posted", type=str, choices=["24h", "week", "month", "any"], default="24h", help="Filter by freshness (default: 24h / latest)")
    parser.add_argument("--sort-by", type=str, choices=["date", "relevance"], default="date", help="Sort results by date or relevance (default: date)")
    parser.add_argument("--remote-only", action="store_true", default=False, help="Filter for remote jobs only")
    parser.add_argument("--max-jobs", type=int, default=5, help="Number of jobs to apply per platform")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum fit score (0-100, default: 0 to apply to all matching jobs)")
    parser.add_argument("--email", type=str, default=None, help="LinkedIn Email (optional, or login interactively in browser)")
    parser.add_argument("--password", type=str, default=None, help="LinkedIn Password (optional, or login interactively in browser)")
    parser.add_argument("--auto-approve", action="store_true", default=False, help="Auto submit without asking")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Simulate without submitting")
    parser.add_argument("--api-key", type=str, default=None, help="LLM API Key (Gemini or OpenAI)")
    parser.add_argument("--llm-provider", type=str, choices=["auto", "gemini", "openai", "heuristic"], default="auto", help="LLM Provider")
    parser.add_argument("--llm-model", type=str, default=None, help="LLM Model (e.g. gemini-3.8-flash, gemini-3.7-flash, gpt-4o-mini)")
    parser.add_argument("--signin", "--login", action="store_true", default=False, help="Launch visible browser to sign in and permanently save platform session")
    args = parser.parse_args()

    if args.signin:
        asyncio.run(interactive_platform_signin(platform=args.platform or "all"))
        return

    asyncio.run(run_simple_agent(
        keyword=args.keyword,
        location=args.location,
        max_jobs=args.max_jobs,
        min_score=args.min_score,
        platform=args.platform,
        date_posted=args.date_posted,
        sort_by=args.sort_by,
        remote_only=args.remote_only,
        email=args.email,
        password=args.password,
        auto_approve=args.auto_approve,
        dry_run=args.dry_run,
        api_key=args.api_key,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model
    ))

if __name__ == "__main__":
    main()
