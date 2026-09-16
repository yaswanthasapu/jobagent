import argparse
import asyncio
import logging
import sys

if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from config.settings import settings
from services.setup_service import SetupService
from services.memory_service import MemoryService
from services.chat_agent import ChatAgent
from services.db_service import DatabaseService
from services.profile_loader import ProfileLoader
from apply_jobs import run_simple_agent

logger = logging.getLogger(__name__)

async def interactive_menu(console: Console, setup_service: SetupService, memory_service: MemoryService):
    """
    Claude-style interactive CLI interface.
    Greets the user, verifies setup, and guides them through all agent actions.
    """
    console.print(Panel.fit(
        "[bold cyan]=====================================================[/bold cyan]\n"
        "[bold white]           AI Job Application Agent CLI              [/bold white]\n"
        "[dim]  Autonomous Multi-Platform Applications: LinkedIn & Naukri [/dim]\n"
        "[bold cyan]=====================================================[/bold cyan]",
        border_style="cyan"
    ))

    # 1. First-time setup check - require profile configuration before opening main menu
    while not setup_service.has_configured_profile():
        console.print(Panel.fit(
            "[bold cyan]=====================================================[/bold cyan]\n"
            "[bold white]           AI JOB APPLICATION AGENT                  [/bold white]\n"
            "[dim]  Autonomous Multi-Platform Applications: LinkedIn & Naukri [/dim]\n"
            "[bold cyan]=====================================================[/bold cyan]",
            border_style="cyan"
        ))
        console.print("\n[bold yellow]Welcome to JobAgent 👋[/bold yellow]")
        console.print("[white]Your candidate profile hasn't been configured yet.[/white]")
        console.print("[dim]Let's set up your candidate profile before we begin.[/dim]\n")
        console.print("  [bold yellow][1][/bold yellow] 🛠️  [bold white]Create Profile / Run Setup Wizard[/bold white] (Step-by-step interactive setup)")
        console.print("  [bold green][2][/bold green] 📄 [bold white]Import Resume[/bold white] (Auto-extract all candidate details from PDF)")
        console.print("  [bold cyan][3][/bold cyan] 🔑 [bold white]Sign In / Connect Job Platforms[/bold white] (Save LinkedIn & Naukri login)")
        console.print("  [bold red][0][/bold red] 🚪 [bold white]Exit[/bold white]")

        init_choice = Prompt.ask("\n[bold cyan]Select an option[/bold cyan]", choices=["1", "2", "3", "0"], default="1")
        if init_choice == "1":
            await setup_service.run_setup_wizard()
        elif init_choice == "2":
            resume_path_str = Prompt.ask("[bold green]Enter path to your Resume PDF (or press Enter to cancel)[/bold green]", default="")
            clean_str = resume_path_str.strip().strip('"').strip("'")
            if clean_str:
                new_path = Path(clean_str)
                if not new_path.exists() or not new_path.is_file():
                    console.print(f"[bold red]Error: Resume file '{clean_str}' not found or is not a valid file.[/bold red]")
                else:
                    await setup_service.update_resume(new_path)
        elif init_choice == "3":
            from apply_jobs import interactive_platform_signin
            plat_choice = Prompt.ask("Select platform to sign into", choices=["all", "linkedin", "naukri"], default="all")
            await interactive_platform_signin(console=console, platform=plat_choice)
            Prompt.ask("\n[dim]Press Enter to return to setup menu[/dim]")
        elif init_choice == "0":
            console.print("\n[bold yellow]Setup exited. Run 'jobagent' anytime to configure your profile.[/bold yellow]\n")
            return

    # 2. Main interactive menu loop
    while True:
        setup_service.display_status()

        console.print("\n[bold cyan]What would you like to do?[/bold cyan]")
        console.print("  [bold yellow][1][/bold yellow] 🔑 [bold white]Sign In / Verify Platform Accounts[/bold white] (Save LinkedIn & Naukri login permanently)")
        console.print("  [bold green][2][/bold green] 🚀 [bold white]Direct Apply to Jobs[/bold white] (LinkedIn, Naukri, or Both)")
        console.print("  [bold magenta][3][/bold magenta] 💬 [bold white]Chat with Agent[/bold white] (Search & apply, update details, ask questions)")
        console.print("  [bold cyan][4][/bold cyan] 📋 [bold white]View All Applied Jobs[/bold white] (Full history, links & status)")
        console.print("  [bold green][5][/bold green] 🖥️  [bold white]Launch Professional Web UI Dashboard[/bold white]")
        console.print("  [bold yellow][6][/bold yellow] 📄 [bold white]Run AI ATS Resume Review & Gap Analysis[/bold white]")
        console.print("  [bold blue][7][/bold blue] ✏️  [bold white]Edit Candidate Profile & Preferences[/bold white]")
        console.print("  [bold magenta][8][/bold magenta] 📎 [bold white]Update Resume PDF[/bold white]")
        console.print("  [bold white][9][/bold white] 📊 [bold white]View Session History & Memory Rules[/bold white]")
        console.print("  [bold red][0][/bold red] 🚪 [bold white]Exit[/bold white]")

        choice = Prompt.ask("\n[bold cyan]Select an option[/bold cyan]", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"], default="1")

        if choice == "1":
            # Sign In / Verify Platform Accounts
            from apply_jobs import interactive_platform_signin
            plat_choice = Prompt.ask("Select platform to sign into", choices=["all", "linkedin", "naukri"], default="all")
            await interactive_platform_signin(console=console, platform=plat_choice)
            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "2":
            # Direct Apply with interactive config
            console.print("\n[bold cyan]Step 1: Account Sign-In Check[/bold cyan]")
            signin_check = Confirm.ask("Verify or sign into your LinkedIn / Naukri account first?", default=False)
            if signin_check:
                from apply_jobs import interactive_platform_signin
                plat_to_sign = Prompt.ask("Platform to sign into", choices=["all", "linkedin", "naukri"], default="linkedin")
                await interactive_platform_signin(console=console, platform=plat_to_sign)

            prefs = memory_service.get_all_preferences()
            profile_loader = ProfileLoader.get_instance()
            cand_profile = profile_loader.profile
            memory_service.seed_from_profile(cand_profile)

            # Load previously used settings so user never starts with empty inputs
            default_role = prefs.get("last_target_role") or (cand_profile.preferred_roles[0] if (cand_profile and cand_profile.preferred_roles) else "Software Engineer")
            default_loc = prefs.get("last_target_location") or (cand_profile.preferred_locations[0] if (cand_profile and cand_profile.preferred_locations) else "Remote")
            default_plat = prefs.get("last_platform") or ("all" if len(prefs.get("preferred_platforms", [])) > 1 else prefs.get("preferred_platforms", ["all"])[0])
            default_date = prefs.get("last_date_filter") or prefs.get("default_date_filter", "24h")
            default_remote = prefs.get("last_remote_only", False)
            default_max_jobs = str(prefs.get("last_max_jobs", 5))

            console.print("\n[bold yellow]Step 2: Job Application Configuration:[/bold yellow]")
            target_role = Prompt.ask("Target Job Role / Keyword", default=default_role)
            target_loc = Prompt.ask("Target Location", default=default_loc)
            plat_choice = Prompt.ask(
                "Target Platform",
                choices=["all", "linkedin", "naukri"],
                default=default_plat
            )
            date_filter = Prompt.ask(
                "Filter Postings By Freshness",
                choices=["24h", "week", "month", "any"],
                default=default_date
            )
            remote_only = Confirm.ask("Filter for Remote jobs only?", default=default_remote)
            max_jobs_str = Prompt.ask("Max jobs to apply per platform", default=default_max_jobs)
            try:
                max_jobs = int(max_jobs_str)
            except ValueError:
                max_jobs = 5

            console.print("\n[bold cyan]Application Submission Mode:[/bold cyan]")
            console.print("  [bold green][1][/bold green] ⚡ [bold white]Auto-Apply[/bold white] (Automatically submit applications without pausing)")
            console.print("  [bold yellow][2][/bold yellow] 👁️  [bold white]Manual Approval[/bold white] (Pause before submitting each application for review)")
            default_mode = "1" if prefs.get("auto_approve_applications", False) else "2"
            approval_choice = Prompt.ask("Select mode", choices=["1", "2"], default=default_mode)
            auto_approve = (approval_choice == "1")

            default_dry_run = prefs.get("last_dry_run", False)
            dry_run = Confirm.ask("Run in Safe DRY-RUN mode (simulate without final submit)?", default=default_dry_run)

            # Store updated inputs immediately to persist for next session
            memory_service.set_preference("last_target_role", target_role)
            memory_service.set_preference("last_target_location", target_loc)
            memory_service.set_preference("last_platform", plat_choice)
            memory_service.set_preference("last_date_filter", date_filter)
            memory_service.set_preference("last_remote_only", remote_only)
            memory_service.set_preference("last_max_jobs", max_jobs)
            memory_service.set_preference("last_dry_run", dry_run)
            memory_service.set_preference("auto_approve_applications", auto_approve)
            memory_service.set_preference("preferred_platforms", [plat_choice] if plat_choice != "all" else ["linkedin", "naukri"])

            console.print("\n[bold green]Starting application automation...[/bold green]\n")
            await run_simple_agent(
                keyword=target_role,
                location=target_loc,
                platform=plat_choice,
                date_posted=date_filter,
                remote_only=remote_only,
                max_jobs=max_jobs,
                auto_approve=auto_approve,
                dry_run=dry_run
            )

            console.print("\n[bold green][OK] Application run completed.[/bold green]")
            if not Confirm.ask("\nReturn to main menu?", default=True):
                break

        elif choice == "3":
            # Interactive Chat Mode
            chat_agent = ChatAgent(console=console, setup_service=setup_service, memory_service=memory_service)
            await chat_agent.run_chat_loop()

        elif choice == "4":
            # View All Applied Jobs
            db_service = DatabaseService()
            await db_service.display_applied_jobs_table(console=console)
            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "5":
            # Launch UI Dashboard
            console.print("\n[bold green]Launching Professional UI Dashboard...[/bold green]\n")
            from ui.server import serve_ui
            await serve_ui()
            break

        elif choice == "6":
            # AI ATS Resume Review
            await setup_service.run_resume_review()
            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "7":
            # Edit profile details
            await setup_service.edit_details()
            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "8":
            # Update resume
            resume_path_str = Prompt.ask("[bold green]Enter path to new Resume PDF (or press Enter to cancel)[/bold green]", default="")
            clean_str = resume_path_str.strip().strip('"').strip("'")
            if not clean_str:
                console.print("[yellow]Update cancelled. No resume path provided.[/yellow]")
            else:
                new_path = Path(clean_str)
                if not new_path.exists() or not new_path.is_file():
                    console.print(f"[bold red]Error: Resume file '{clean_str}' not found or is not a valid file.[/bold red]")
                else:
                    await setup_service.update_resume(new_path)
            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "9":
            # Application history & memory
            console.print("\n[bold cyan]=== Agent Conversation & Learned Rules ===[/bold cyan]")
            notes = memory_service.get_conversation_notes()
            for i, n in enumerate(notes, 1):
                console.print(f"  [dim]{i}.[/dim] {n}")

            # Display saved form answers table
            saved_answers = memory_service.get_all_saved_form_answers()
            if saved_answers:
                console.print("\n[bold cyan]=== Saved Form Data (Reused for Job Applications) ===[/bold cyan]")
                ans_table = Table()
                ans_table.add_column("Question / Form Field", style="bold white", width=42)
                ans_table.add_column("Saved Value", style="bold green", width=26)
                ans_table.add_column("Field Type", style="dim", width=12)
                for k, v in list(saved_answers.items())[:25]:
                    ans_table.add_row(
                        v.get("raw_label", k)[:40],
                        str(v.get("answer", ""))[:25],
                        str(v.get("field_type", "text"))
                    )
                console.print(ans_table)

            history = memory_service.get_session_history()
            if history:
                console.print("\n[bold cyan]=== Past Application Sessions ===[/bold cyan]")
                table = Table()
                table.add_column("Timestamp", style="dim")
                table.add_column("Platforms", style="cyan")
                table.add_column("Applied Count", style="bold green")
                table.add_column("Date Filter", style="white")
                for s in history[-5:]:
                    table.add_row(
                        s.get("timestamp", "")[:16].replace("T", " "),
                        ", ".join(s.get("platforms", [])),
                        str(s.get("applied_count", 0)),
                        s.get("date_filter", "24h")
                    )
                console.print(table)
            else:
                console.print("\n[dim]No application sessions recorded yet.[/dim]")

            Prompt.ask("\n[dim]Press Enter to return to main menu[/dim]")

        elif choice == "0":
            console.print("\n[bold cyan]Thank you for using AI Job Application Agent. Good luck with your job hunt![/bold cyan]\n")
            break

def main():
    parser = argparse.ArgumentParser(
        description="Universal Multi-Platform AI Job Agent (LinkedIn + Naukri)"
    )
    parser.add_argument("--keyword", type=str, default=None, help="Target job role")
    parser.add_argument("--location", type=str, default=None, help="Job location")
    parser.add_argument("--platform", type=str, choices=["linkedin", "naukri", "all"], default=None, help="Platform to apply")
    parser.add_argument("--date-posted", type=str, choices=["24h", "week", "month", "any"], default="24h", help="Filter by freshness")
    parser.add_argument("--sort-by", type=str, choices=["date", "relevance"], default="date", help="Sort results")
    parser.add_argument("--remote-only", action="store_true", default=False, help="Remote jobs only")
    parser.add_argument("--max-jobs", type=int, default=5, help="Number of jobs to apply")
    parser.add_argument("--min-score", type=float, default=0.0, help="Minimum fit score")
    parser.add_argument("--auto-approve", action="store_true", default=False, help="Auto submit without asking")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Simulate without submitting")
    parser.add_argument("--setup", action="store_true", default=False, help="Force launch setup wizard")
    parser.add_argument("--review-resume", action="store_true", default=False, help="Run ATS resume review")
    parser.add_argument("--status", action="store_true", default=False, help="Show profile and status")
    parser.add_argument("--chat", action="store_true", default=False, help="Launch interactive conversational chat with AI agent")
    parser.add_argument("--applied-jobs", "--show-applied", action="store_true", default=False, help="Display all applied jobs till now")
    parser.add_argument("--ui", "--web", action="store_true", default=False, help="Launch professional Web UI dashboard in browser")
    parser.add_argument("--port", type=int, default=8000, help="Port for Web UI dashboard (default: 8000)")
    parser.add_argument("--signin", "--login", action="store_true", default=False, help="Launch visible browser to sign into LinkedIn/Naukri and save session permanently")
    args = parser.parse_args()

    console = Console()
    from services.preflight import run_preflight_checks
    if not run_preflight_checks(console):
        sys.exit(1)

    setup_service = SetupService(console)
    memory_service = MemoryService()

    # If specific flags were passed, run that mode directly
    if args.signin:
        from apply_jobs import interactive_platform_signin
        asyncio.run(interactive_platform_signin(console=console, platform=args.platform or "all"))
        return
    if args.ui:
        from ui.server import launch_ui
        launch_ui(port=args.port)
        return
    if args.setup:
        asyncio.run(setup_service.run_setup_wizard())
        return
    if args.status:
        setup_service.display_status()
        return
    if args.review_resume:
        asyncio.run(setup_service.run_resume_review())
        return
    if args.applied_jobs:
        db_service = DatabaseService()
        asyncio.run(db_service.display_applied_jobs_table(console=console))
        return
    if args.chat:
        chat_agent = ChatAgent(console=console, setup_service=setup_service, memory_service=memory_service)
        asyncio.run(chat_agent.run_chat_loop())
        return
    if args.platform or args.keyword or args.location or args.dry_run:
        # Direct CLI execution
        asyncio.run(run_simple_agent(
            keyword=args.keyword,
            location=args.location,
            platform=args.platform or "all",
            date_posted=args.date_posted,
            sort_by=args.sort_by,
            remote_only=args.remote_only,
            max_jobs=args.max_jobs,
            min_score=args.min_score,
            auto_approve=args.auto_approve,
            dry_run=args.dry_run
        ))
        return

    # Default (No arguments given, e.g. just running 'python main.py' or 'jobagent'):
    # Run the interactive Claude-style assistant experience!
    try:
        asyncio.run(interactive_menu(console, setup_service, memory_service))
    except KeyboardInterrupt:
        console.print("\n[yellow]Exiting job agent.[/yellow]")
        sys.exit(0)

if __name__ == "__main__":
    main()
