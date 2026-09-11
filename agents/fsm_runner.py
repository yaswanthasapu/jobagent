import asyncio
from enum import Enum
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from automation.pages.easy_apply_modal import EasyApplyModal
from automation.pages.job_details_page import JobDetailsPage
from automation.pages.job_search_page import JobSearchPage
from automation.pages.login_page import LoginPage
from automation.browser import launch_persistent_context_with_retry
from config.settings import settings
from models.application import ApplicationRecord, ApplicationStatus
from models.evaluation import DecisionType, JobEvaluationResult
from models.form import FormFieldType
from models.job import JobCardSummary, JobDetails
from models.profile import CandidateProfile
from services.db_service import DatabaseService
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.profile_loader import ProfileLoader
from services.resume_service import ResumeService
from .form_agent import FormAgent
from .job_evaluator import JobEvaluator

logger = logging.getLogger(__name__)

class WorkflowState(str, Enum):
    INIT = "INIT"
    LAUNCH_BROWSER = "LAUNCH_BROWSER"
    CHECK_LOGIN = "CHECK_LOGIN"
    SEARCH_JOBS = "SEARCH_JOBS"
    EXTRACT_CARDS = "EXTRACT_CARDS"
    EVALUATE_FIT = "EVALUATE_FIT"
    FILL_FORM = "FILL_FORM"
    AWAIT_APPROVAL = "AWAIT_APPROVAL"
    SUBMIT = "SUBMIT"
    TRACK = "TRACK"
    NEXT_JOB = "NEXT_JOB"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

class FSMRunner:
    """
    Finite State Machine Orchestrator managing end-to-end LinkedIn Easy Apply workflow.
    """

    def __init__(
        self,
        keyword: str = "QA Automation Engineer",
        location: str = "Hyderabad",
        max_jobs: int = 10,
        min_score: float = 70.0,
        headless: bool = False,
        dry_run: bool = False,
        auto_approve: bool = False,
        console: Optional[Console] = None,
        profile_path: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        llm_provider: str = "auto",
        llm_model: Optional[str] = None
    ):
        self.keyword = keyword
        self.location = location
        self.max_jobs = max_jobs
        self.min_score = min_score
        self.headless = headless
        self.dry_run = dry_run
        self.auto_approve = auto_approve
        self.console = console or Console()
        self.profile_path = profile_path
        self.email = email
        self.password = password
        self.api_key = api_key
        self.llm_provider = llm_provider
        self.llm_model = llm_model

        # Current state
        self.state = WorkflowState.INIT

        # Services & Agents
        self.profile_loader: Optional[ProfileLoader] = None
        self.db_service: Optional[DatabaseService] = None
        self.llm_service: Optional[LLMService] = None
        self.resume_service: Optional[ResumeService] = None
        self.job_evaluator: Optional[JobEvaluator] = None
        self.form_agent: Optional[FormAgent] = None

        # Playwright elements
        self._pw: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        # POM Pages
        self.login_page: Optional[LoginPage] = None
        self.search_page: Optional[JobSearchPage] = None
        self.details_page: Optional[JobDetailsPage] = None
        self.modal: Optional[EasyApplyModal] = None

        # Execution tracking
        self.discovered_cards: List[JobCardSummary] = []
        self.current_card_index: int = 0
        self.current_card: Optional[JobCardSummary] = None
        self.current_details: Optional[JobDetails] = None
        self.current_eval: Optional[JobEvaluationResult] = None
        self.processed_count: int = 0
        self.applied_count: int = 0

    async def run(self) -> Dict[str, Any]:
        """Run the finite state machine through its lifecycle."""
        self.console.print(Panel.fit(
            f"[bold cyan]LinkedIn Easy Apply AI Agent[/bold cyan]\n"
            f"[dim]Keyword:[/dim] [bold yellow]{self.keyword}[/bold yellow] | "
            f"[dim]Location:[/dim] [bold yellow]{self.location}[/bold yellow] | "
            f"[dim]Min Score:[/dim] [bold green]{self.min_score}[/bold green] | "
            f"[dim]Max Jobs:[/dim] [bold white]{self.max_jobs}[/bold white]\n"
            f"[dim]Mode:[/dim] {'[bold red]DRY RUN (No Submissions)[/bold red]' if self.dry_run else '[bold green]LIVE APPLY[/bold green]'}",
            border_style="cyan"
        ))

        try:
            while self.state not in [WorkflowState.COMPLETED, WorkflowState.ERROR]:
                logger.info(f"FSM Transition -> {self.state.value}")

                if self.state == WorkflowState.INIT:
                    await self._handle_init()

                elif self.state == WorkflowState.LAUNCH_BROWSER:
                    await self._handle_launch_browser()

                elif self.state == WorkflowState.CHECK_LOGIN:
                    await self._handle_check_login()

                elif self.state == WorkflowState.SEARCH_JOBS:
                    await self._handle_search_jobs()

                elif self.state == WorkflowState.EXTRACT_CARDS:
                    await self._handle_extract_cards()

                elif self.state == WorkflowState.EVALUATE_FIT:
                    await self._handle_evaluate_fit()

                elif self.state == WorkflowState.FILL_FORM:
                    await self._handle_fill_form()

                elif self.state == WorkflowState.AWAIT_APPROVAL:
                    await self._handle_await_approval()

                elif self.state == WorkflowState.SUBMIT:
                    await self._handle_submit()

                elif self.state == WorkflowState.TRACK:
                    await self._handle_track()

                elif self.state == WorkflowState.NEXT_JOB:
                    await self._handle_next_job()

        except Exception as e:
            self.console.print(f"[bold red]Unexpected error in state {self.state.value}: {e}[/bold red]")
            logger.exception("FSM execution failed")
            self.state = WorkflowState.ERROR

        finally:
            await self.cleanup()

        return {
            "processed": self.processed_count,
            "applied": self.applied_count,
            "final_state": self.state.value
        }

    async def _handle_init(self) -> None:
        self.console.print("[dim]Initializing services and candidate profile...[/dim]")
        self.profile_loader = ProfileLoader(self.profile_path)
        self.db_service = DatabaseService()
        await self.db_service.init_db()

        self.llm_service = LLMService(api_key=self.api_key, provider=self.llm_provider, model=self.llm_model)
        self.resume_service = ResumeService()
        self.job_evaluator = JobEvaluator(self.llm_service)
        self.memory_service = MemoryService()
        self.form_agent = FormAgent(self.llm_service, self.memory_service)

        self.state = WorkflowState.LAUNCH_BROWSER

    async def _handle_launch_browser(self) -> None:
        mode_str = "Headless Background" if self.headless else "Visible Desktop Window (HEADLESS=False)"
        self.console.print(f"[bold cyan]Launching Chromium browser in {mode_str}...[/bold cyan]")
        if not self.headless:
            self.console.print("[bold green]Browser window is visible on your screen! (Check taskbar / Alt+Tab if behind terminal)[/bold green]")
        user_data_path = Path(settings.USER_DATA_DIR).resolve()
        user_data_path.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--start-maximized",
        ]

        viewport_opts = (
            {"no_viewport": True}
            if not self.headless
            else {"viewport": {"width": settings.VIEWPORT_WIDTH, "height": settings.VIEWPORT_HEIGHT}}
        )

        self.context = await launch_persistent_context_with_retry(
            pw=self._pw,
            user_data_dir=user_data_path,
            console=self.console,
            headless=self.headless,
            slow_mo=settings.SLOW_MO_MS,
            args=launch_args,
            **viewport_opts
        )

        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        await self.page.bring_to_front()

        # Initialize POM pages
        self.login_page = LoginPage(self.page)
        await self.login_page.inject_stealth()
        self.search_page = JobSearchPage(self.page)
        self.details_page = JobDetailsPage(self.page)
        self.modal = EasyApplyModal(self.page)

        self.state = WorkflowState.CHECK_LOGIN

    async def _handle_check_login(self) -> None:
        self.console.print("[dim]Verifying authentication state...[/dim]")
        await self.page.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=45000)
        await self.page.wait_for_timeout(2000)

        is_auth = await self.login_page.is_authenticated()
        if not is_auth:
            if not self.email or not self.password:
                self.console.print("\n[bold cyan]LinkedIn is currently unauthenticated.[/bold cyan]")
                use_auto = Prompt.ask("Would you like to enter login credentials to sign in automatically? [y/n]", default="y")
                if use_auto.lower() == "y":
                    default_email = self.profile_loader.email or ""
                    self.email = Prompt.ask("Email or phone", default=default_email)
                    self.password = Prompt.ask("Password", password=True)

            if self.email and self.password:
                self.console.print("[cyan]Credentials provided: Attempting automatic login...[/cyan]")
                success = await self.login_page.attempt_auto_login(self.email, self.password, self.console)
            else:
                success = await self.login_page.wait_for_manual_authentication(self.console, timeout_seconds=300)

            if not success:
                self.console.print("[bold red]Authentication failed or timed out. Exiting.[/bold red]")
                self.state = WorkflowState.ERROR
                return

        await self.login_page.take_screenshot("authenticated_state")
        self.state = WorkflowState.SEARCH_JOBS

    async def _handle_search_jobs(self) -> None:
        self.console.print(f"[bold cyan]Searching LinkedIn Jobs:[/bold cyan] '{self.keyword}' in '{self.location}'...")
        await self.search_page.navigate_to_search(
            keyword=self.keyword,
            location=self.location,
            easy_apply_only=True
        )
        await self.search_page.take_screenshot("job_search_results")
        self.state = WorkflowState.EXTRACT_CARDS

    async def _handle_extract_cards(self) -> None:
        self.console.print("[dim]Extracting Easy Apply job postings...[/dim]")
        cards = await self.search_page.extract_job_cards(max_cards=self.max_jobs * 2)

        # Deduplicate against database
        unapplied_cards = []
        for card in cards:
            is_dup = await self.db_service.is_already_applied(
                job_id=card.job_id,
                job_url=card.job_url,
                company=card.company
            )
            if not is_dup:
                unapplied_cards.append(card)
            else:
                logger.info(f"Skipping already applied job: {card.title} at {card.company}")

        self.discovered_cards = unapplied_cards
        self.current_card_index = 0

        if not self.discovered_cards:
            self.console.print("[yellow]No new unapplied Easy Apply jobs found on current page.[/yellow]")
            self.state = WorkflowState.COMPLETED
            return

        self.console.print(f"[bold green]Discovered {len(self.discovered_cards)} unapplied Easy Apply opportunities.[/bold green]")
        self.current_card = self.discovered_cards[0]
        self.state = WorkflowState.EVALUATE_FIT

    async def _handle_evaluate_fit(self) -> None:
        if not self.current_card:
            self.state = WorkflowState.NEXT_JOB
            return

        self.console.print(f"\n[bold]Inspecting Job:[/bold] {self.current_card.title} @ [bold]{self.current_card.company}[/bold]")

        # Load details
        self.current_details = await self.details_page.select_and_load_job(self.current_card)
        await self.details_page.take_screenshot(f"job_details_{self.current_card.job_id}")

        # Check if Easy Apply is available
        if not self.current_details.is_easy_apply:
            self.console.print("[yellow]Notice: Postings does not feature an active Easy Apply button. Skipping.[/yellow]")
            self.state = WorkflowState.NEXT_JOB
            return

        # Semantic Fit Evaluation
        profile = self.profile_loader.profile
        eval_res = await self.job_evaluator.evaluate_fit(self.current_details, profile)
        self.current_eval = eval_res

        # Display Fit Card
        table = Table(title=f"Match Evaluation: {self.current_card.title}", border_style="cyan")
        table.add_column("Metric", style="bold white")
        table.add_column("Score / Details", style="yellow")

        table.add_row("Overall Fit Score", f"[{'bold green' if eval_res.match_breakdown.overall_score >= self.min_score else 'bold red'}]{eval_res.match_breakdown.overall_score}%[/]")
        table.add_row("Experience Match", f"{eval_res.match_breakdown.experience_score}% ({eval_res.match_breakdown.experience_analysis})")
        table.add_row("Skill Overlap", f"{eval_res.match_breakdown.skill_score}%")
        table.add_row("Matched Skills", ", ".join(eval_res.match_breakdown.matched_skills[:6]) or "None")
        table.add_row("Missing Skills", ", ".join(eval_res.match_breakdown.missing_skills[:6]) or "None")
        table.add_row("Decision", f"[{'bold green' if eval_res.decision == DecisionType.APPLY else 'bold red'}]{eval_res.decision.value}[/]")
        table.add_row("Reasoning", eval_res.match_breakdown.reasoning)
        self.console.print(table)

        if eval_res.decision != DecisionType.APPLY:
            self.console.print(f"[yellow]Skipping job (Score {eval_res.match_breakdown.overall_score}% < {self.min_score}% threshold).[/yellow]")
            # Record skip in DB
            await self.db_service.record_application(ApplicationRecord(
                job_id=self.current_card.job_id,
                job_title=self.current_card.title,
                company=self.current_card.company,
                location=self.current_card.location,
                job_url=self.current_card.job_url,
                match_score=eval_res.match_breakdown.overall_score,
                status=ApplicationStatus.SKIPPED_LOW_SCORE,
                notes=eval_res.match_breakdown.reasoning
            ))
            self.state = WorkflowState.NEXT_JOB
            return

        # Open Easy Apply Modal
        opened = await self.details_page.click_easy_apply()
        if not opened or not await self.modal.is_modal_open():
            self.console.print("[red]Could not open Easy Apply modal dialog. Skipping.[/red]")
            self.state = WorkflowState.NEXT_JOB
            return

        self.state = WorkflowState.FILL_FORM

    async def _handle_fill_form(self) -> None:
        self.console.print("[dim]Filling form step-by-step...[/dim]")
        profile = self.profile_loader.profile
        resume_path = self.resume_service.get_resume_path(profile)

        max_steps = 10
        step_num = 0

        while step_num < max_steps:
            step_num += 1
            step_title = await self.modal.get_step_title()
            logger.info(f"Form step {step_num}: {step_title}")
            await self.modal.take_screenshot(f"modal_step_{step_num}")

            # Check if review step reached
            if await self.modal.is_review_step():
                self.state = WorkflowState.AWAIT_APPROVAL
                return

            # Inspect and fill fields in current step with progressive scrolling
            scroll_passes = 0
            max_scrolls = 4
            while scroll_passes <= max_scrolls:
                fields = await self.modal.inspect_fields()
                for field in fields:
                    if (
                        field.field_type in [FormFieldType.TEXT, FormFieldType.NUMBER]
                        and field.current_value
                        and len(field.current_value.strip()) > 1
                    ):
                        continue
                    if (
                        field.field_type in [FormFieldType.RADIO, FormFieldType.SELECT]
                        and field.current_value
                        and len(field.current_value.strip()) > 0
                        and field.current_value.lower() != "select an option"
                    ):
                        continue

                    val, needs_hitl = await self.form_agent.resolve_field_value(field, profile)
                    if needs_hitl:
                        self.console.print(f"[bold yellow]🔔 Human Interaction Required for New Field:[/bold yellow] '{field.label}'")
                        if field.options:
                            opts_str = " / ".join(f"[{i+1}] {o}" for i, o in enumerate(field.options))
                            choice = Prompt.ask(f"Select option ({opts_str})", default="1")
                            try:
                                idx = int(choice) - 1
                                val = field.options[idx] if 0 <= idx < len(field.options) else choice.strip()
                            except ValueError:
                                val = choice.strip()
                        else:
                            val = Prompt.ask(f"Enter answer for '{field.label}'", default=val or "Yes")
                        if val:
                            ftype = field.field_type.value if hasattr(field.field_type, "value") else str(field.field_type)
                            self.form_agent.memory_service.save_form_answer(field.label, val, ftype)
                            self.console.print(f"  [bold green]✓ Remembered response for future applications:[/bold green] '{field.label}' -> '{val}'")

                    if val or field.field_type == FormFieldType.FILE_UPLOAD:
                        await self.modal.fill_field(field, val, resume_file_path=resume_path)

                scrolled = await self.modal.scroll_modal_content(step=450)
                if not scrolled:
                    break
                scroll_passes += 1
                await asyncio.sleep(0.3)

            # Try proceeding
            advanced = await self.modal.click_next() or await self.modal.click_review()
            if not advanced:
                if await self.modal.is_review_step():
                    self.state = WorkflowState.AWAIT_APPROVAL
                    return
                
                # Check for validation errors
                errors = await self.modal.get_validation_errors()
                if errors:
                    self.console.print(f"  [yellow]Validation errors on step: {', '.join(errors)}[/yellow]")
                
                self.console.print(Panel(
                    f"[bold yellow]⚠️ Action Required: Please Answer Question in Browser[/bold yellow]\n\n"
                    f"[white]The browser window is open in front of you.\n"
                    f"• Please select or enter the required value directly in the browser.\n"
                    f"• Press [Enter] once answered (or type 'skip' to discard).[/white]",
                    title="[bold yellow]JobAgent Input Wait[/bold yellow]",
                    border_style="yellow"
                ))

                try:
                    choice = await asyncio.to_thread(
                        Prompt.ask,
                        "[bold cyan]Press [Enter] after selecting in browser (or 'skip' to discard)[/bold cyan]",
                        default=""
                    )
                    if choice.strip().lower() in ["s", "skip", "n", "no"]:
                        self.console.print("[yellow]User skipped application. Closing modal.[/yellow]")
                        await self.modal.dismiss_modal()
                        self.state = WorkflowState.NEXT_JOB
                        return
                except Exception:
                    pass

                # Re-check advance
                advanced = await self.modal.click_next() or await self.modal.click_review()
                if not advanced and not await self.modal.is_review_step():
                    self.console.print("[yellow]Could not advance form to next step. Dismissing modal.[/yellow]")
                    await self.modal.dismiss_modal()
                    self.state = WorkflowState.NEXT_JOB
                    return

        self.state = WorkflowState.AWAIT_APPROVAL

    async def _handle_await_approval(self) -> None:
        """Human-in-the-Loop Gate."""
        profile = self.profile_loader.profile
        resume_path = self.resume_service.get_resume_path(profile)
        await self.modal.take_screenshot("final_review_step")

        summary_table = Table(title="[bold green]HITL GATE: Final Review Before Submission[/bold green]", border_style="green")
        summary_table.add_column("Field", style="bold white")
        summary_table.add_column("Value", style="cyan")

        summary_table.add_row("Job Title", self.current_card.title)
        summary_table.add_row("Company", self.current_card.company)
        summary_table.add_row("Location", self.current_card.location)
        summary_table.add_row("Fit Score", f"{self.current_eval.match_breakdown.overall_score}%")
        summary_table.add_row("Resume Uploaded", Path(resume_path).name)
        summary_table.add_row("Notice Period Filled", f"{profile.professional.notice_period_days} days")
        summary_table.add_row("CTC Expected", f"{profile.professional.expected_lpa} LPA")
        self.console.print(summary_table)

        if self.auto_approve:
            self.console.print("[bold green]Auto-approve enabled: Proceeding to submit.[/bold green]")
            self.state = WorkflowState.SUBMIT
            return

        choice = Prompt.ask(
            "\n[bold white]Take Action:[/bold white] "
            "[bold green][y] Approve & Submit[/bold green] | "
            "[bold yellow][n] Skip Job[/bold yellow] | "
            "[bold red][q] Quit Agent[/bold red]",
            choices=["y", "n", "q"],
            default="y"
        )

        if choice == "y":
            self.state = WorkflowState.SUBMIT
        elif choice == "n":
            self.console.print("[yellow]Application rejected by user.[/yellow]")
            await self.modal.dismiss_modal()
            await self.db_service.record_application(ApplicationRecord(
                job_id=self.current_card.job_id,
                job_title=self.current_card.title,
                company=self.current_card.company,
                location=self.current_card.location,
                job_url=self.current_card.job_url,
                match_score=self.current_eval.match_breakdown.overall_score,
                status=ApplicationStatus.REJECTED_HITL,
                notes="Rejected by user at HITL approval gate."
            ))
            self.state = WorkflowState.NEXT_JOB
        else:
            self.console.print("[dim]Aborting workflow per user request.[/dim]")
            await self.modal.dismiss_modal()
            self.state = WorkflowState.COMPLETED

    async def _handle_submit(self) -> None:
        if self.dry_run:
            self.console.print("[bold yellow][DRY-RUN] Simulated successful submission (modal dismissed).[/bold yellow]")
            await self.modal.dismiss_modal()
        else:
            self.console.print("[bold green]Submitting application...[/bold green]")
            submitted = await self.modal.click_submit()
            if submitted:
                self.console.print("[bold green]✓ Application successfully submitted![/bold green]")
            else:
                self.console.print("[yellow]Submit button click did not register; dismissing modal.[/yellow]")
                await self.modal.dismiss_modal()

        self.applied_count += 1
        self.state = WorkflowState.TRACK

    async def _handle_track(self) -> None:
        status = ApplicationStatus.SUBMITTED if not self.dry_run else ApplicationStatus.SUBMITTED
        await self.db_service.record_application(ApplicationRecord(
            job_id=self.current_card.job_id,
            job_title=self.current_card.title,
            company=self.current_card.company,
            location=self.current_card.location,
            job_url=self.current_card.job_url,
            match_score=self.current_eval.match_breakdown.overall_score if self.current_eval else 0.0,
            status=status,
            notes=f"{'DRY RUN: ' if self.dry_run else ''}Applied successfully."
        ))
        self.state = WorkflowState.NEXT_JOB

    async def _handle_next_job(self) -> None:
        self.processed_count += 1
        self.current_card_index += 1

        if self.applied_count >= self.max_jobs or self.current_card_index >= len(self.discovered_cards):
            self.console.print(f"\n[bold green]Goal reached ({self.applied_count} applied, {self.processed_count} evaluated).[/bold green]")
            self.state = WorkflowState.COMPLETED
            return

        self.current_card = self.discovered_cards[self.current_card_index]
        self.state = WorkflowState.EVALUATE_FIT

    async def cleanup(self) -> None:
        if self.context:
            await self.context.close()
        if self._pw:
            await self._pw.stop()
        if self.db_service:
            await self.db_service.close()
