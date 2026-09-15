import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from config.settings import settings
from models.profile import CandidateProfile, PersonalInfo, ProfessionalInfo, JobPreferences
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.profile_loader import ProfileLoader
from services.setup_service import SetupService, PROFILE_PATH, RESUME_PATH

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPT = """You are the AI Assistant for the Universal Multi-Platform Job Application Agent (LinkedIn & Naukri).
You are having an interactive chat with the job seeker.
Your role:
1. Understand their intent and extract any profile updates, search commands, or instructions/rules they provide.
2. If the user specifies or updates details (e.g. CTC, notice period, skills, role, location, company, experience), extract them cleanly.
3. If the user commands you to search or apply for jobs, extract all search parameters (role/keyword, location, platform [linkedin/naukri/all], date_posted [24h/week/month/any], remote_only, max_jobs, dry_run).
4. If the user gives rules or guidelines (e.g. "always select yes for testing questions", "remember I only want remote jobs"), extract it as a memory rule.
5. If the user asks to see profile, review resume, view history, or just chats, handle it accurately.

Return ONLY a valid JSON object matching this schema:
{
  "intent": "search_and_apply" | "update_profile" | "add_rule" | "show_profile" | "review_resume" | "view_history" | "view_applied_jobs" | "general_chat" | "exit",
  "search_params": {
    "keyword": "string or null",
    "location": "string or null",
    "platform": "linkedin" | "naukri" | "all" | null,
    "date_posted": "24h" | "week" | "month" | "any" | null,
    "remote_only": boolean or null,
    "max_jobs": integer or null,
    "dry_run": boolean or null
  },
  "profile_updates": {
    "full_name": "string or null",
    "phone": "string or null",
    "email": "string or null",
    "location": "string or null",
    "designation": "string or null",
    "current_company": "string or null",
    "total_experience_years": float or null,
    "current_lpa": float or null,
    "expected_lpa": float or null,
    "current_ctc_inr": integer or null,
    "expected_ctc_inr": integer or null,
    "notice_period_days": integer or null,
    "add_skills": ["string"] or [],
    "remove_skills": ["string"] or [],
    "preferred_roles": ["string"] or [],
    "preferred_locations": ["string"] or []
  },
  "memory_rule": "string or null",
  "response_message": "Friendly, clear assistant response summarizing what was understood or updated."
}
"""

class ChatAgent:
    """
    Conversational agent allowing the candidate to chat, specify/update profile details,
    store persistent memory rules, and give natural language commands to search and apply for jobs.
    """

    def __init__(
        self,
        console: Optional[Console] = None,
        llm_service: Optional[LLMService] = None,
        profile_loader: Optional[ProfileLoader] = None,
        memory_service: Optional[MemoryService] = None,
        setup_service: Optional[SetupService] = None
    ):
        self.console = console or Console()
        self.llm_service = llm_service or LLMService()
        self.profile_loader = profile_loader or ProfileLoader.get_instance()
        self.memory_service = memory_service or MemoryService()
        self.setup_service = setup_service or SetupService(self.console)
        self.chat_history: List[Dict[str, str]] = []

    def get_candidate_profile(self) -> Optional[CandidateProfile]:
        """Loads and returns current profile if available."""
        if PROFILE_PATH.exists():
            try:
                with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                    return CandidateProfile(**json.load(f))
            except Exception as e:
                logger.warning(f"Error loading profile: {e}")
        return None

    def _build_context_summary(self) -> str:
        """Constructs candidate profile and memory summary for LLM prompt."""
        profile = self.get_candidate_profile()
        prefs = self.memory_service.get_all_preferences()
        notes = self.memory_service.get_conversation_notes()

        lines = ["Current Candidate Context:"]
        if profile:
            lines.append(f"- Name: {profile.personal.full_name}")
            lines.append(f"- Phone: {profile.personal.phone} | Email: {profile.personal.email}")
            lines.append(f"- Designation: {profile.professional.designation} at {profile.professional.current_company}")
            lines.append(f"- Total Experience: {profile.professional.total_experience_years} years")
            lines.append(f"- Current CTC: INR {prefs.get('current_ctc_inr', int(profile.professional.current_lpa * 100000)):,} ({profile.professional.current_lpa} LPA)")
            lines.append(f"- Expected CTC: INR {prefs.get('expected_ctc_inr', int(profile.professional.expected_lpa * 100000)):,} ({profile.professional.expected_lpa} LPA)")
            lines.append(f"- Notice Period: {profile.professional.notice_period_days} days")
            lines.append(f"- Skills: {', '.join(profile.skills)}")
            lines.append(f"- Target Roles: {', '.join(profile.preferred_roles)}")
            lines.append(f"- Locations: {', '.join(profile.preferred_locations)}")
        else:
            lines.append("- Candidate profile not yet configured.")

        if notes:
            lines.append("Agent Memory Rules & Notes:")
            for n in notes[-4:]:
                lines.append(f"  • {n}")

        return "\n".join(lines)

    async def interpret_message(self, user_message: str) -> Dict[str, Any]:
        """
        Interprets candidate message using Gemini (or fallback heuristic)
        to identify intent, search parameters, profile updates, and rules.
        """
        context = self._build_context_summary()

        # Format chat history
        history_str = ""
        if self.chat_history:
            history_str = "Recent Chat History:\n"
            for turn in self.chat_history[-4:]:
                role = "User" if turn["role"] == "user" else "Assistant"
                history_str += f"{role}: {turn['content']}\n"

        prompt = (
            f"{CHAT_SYSTEM_PROMPT}\n\n"
            f"{context}\n\n"
            f"{history_str}\n"
            f"User Message: {user_message}\n\n"
            "Respond ONLY with valid JSON."
        )

        if self.llm_service.can_use_llm():
            try:
                response_text = await self.llm_service.call_gemini(prompt, json_output=True)
                # Clean markdown fences if any
                clean_json = re.sub(r"^```json\s*", "", response_text.strip(), flags=re.IGNORECASE)
                clean_json = re.sub(r"```$", "", clean_json.strip()).strip()
                data = json.loads(clean_json)
                return data
            except Exception as e:
                logger.warning(f"LLM chat interpretation failed ({e}), falling back to heuristic parsing.")

        return self._heuristic_parse(user_message)

    def _heuristic_parse(self, msg: str) -> Dict[str, Any]:
        """
        Deterministic regex/keyword parser for offline environments or LLM failures.
        """
        msg_lower = msg.lower().strip()

        # 1. Exit check
        if msg_lower in ["exit", "quit", "bye", "close", "stop", "/exit", "/quit"]:
            return {
                "intent": "exit",
                "search_params": {},
                "profile_updates": {},
                "memory_rule": None,
                "response_message": "Goodbye! Best of luck with your job applications."
            }

        # 2. View applied jobs
        if any(w in msg_lower for w in [
            "applied jobs", "jobs applied", "which jobs", "show applied", "list applied",
            "all applied", "jobs were applied", "view applied", "/applied", "/jobs"
        ]):
            return {
                "intent": "view_applied_jobs",
                "search_params": {},
                "profile_updates": {},
                "memory_rule": None,
                "response_message": "Displaying all jobs applied till now from the database."
            }

        # 3. View session history & memory rules
        if any(w in msg_lower for w in ["history", "past sessions", "session history", "/history"]):
            return {
                "intent": "view_history",
                "search_params": {},
                "profile_updates": {},
                "memory_rule": None,
                "response_message": "Displaying your session history and learned rules."
            }

        # 3. Show profile / status
        if any(w in msg_lower for w in ["show profile", "view profile", "my profile", "my details", "status", "who am i", "/profile"]):
            return {
                "intent": "show_profile",
                "search_params": {},
                "profile_updates": {},
                "memory_rule": None,
                "response_message": "Here is your current candidate profile and configured preferences."
            }

        # 4. Review resume
        if any(w in msg_lower for w in ["review resume", "ats score", "gap analysis", "audit resume", "check resume", "/review"]):
            return {
                "intent": "review_resume",
                "search_params": {},
                "profile_updates": {},
                "memory_rule": None,
                "response_message": "Starting AI ATS Resume Review and gap analysis."
            }

        # 5. Search and apply commands
        is_search = any(w in msg_lower for w in ["search", "apply", "find", "look for", "hunt", "run application"])
        if is_search and not any(w in msg_lower for w in ["how to search", "where to search"]):
            platform = "all"
            if "linkedin" in msg_lower and "naukri" not in msg_lower:
                platform = "linkedin"
            elif "naukri" in msg_lower and "linkedin" not in msg_lower:
                platform = "naukri"

            date_posted = "24h"
            if any(w in msg_lower for w in ["today", "24h", "24 hours", "1 day", "latest", "recent"]):
                date_posted = "24h"
            elif any(w in msg_lower for w in ["week", "7 days"]):
                date_posted = "week"
            elif any(w in msg_lower for w in ["month", "30 days"]):
                date_posted = "month"

            remote_only = "remote" in msg_lower
            dry_run = any(w in msg_lower for w in ["dry run", "test", "simulate", "safe mode"])

            # Extract max jobs if specified (e.g. "5 jobs", "3 jobs")
            max_jobs = 5
            job_count_match = re.search(r"(\d+)\s*(?:jobs?|positions?)", msg_lower)
            if job_count_match:
                try:
                    max_jobs = int(job_count_match.group(1))
                except ValueError:
                    pass

            # Extract location
            location = None
            for loc in ["hyderabad", "bangalore", "bengaluru", "pune", "mumbai", "delhi", "chennai", "noida", "gurgaon"]:
                if loc in msg_lower:
                    location = loc.capitalize()
                    break

            # Extract role/keyword if mentioned
            keyword = None
            role_matches = [
                "sdet", "qa automation engineer", "qa lead", "qa engineer",
                "automation engineer", "test engineer", "software engineer",
                "frontend engineer", "backend engineer", "fullstack engineer",
                "devops engineer", "data engineer", "data scientist"
            ]
            for r in role_matches:
                if r in msg_lower:
                    keyword = r.title()
                    break

            return {
                "intent": "search_and_apply",
                "search_params": {
                    "keyword": keyword,
                    "location": location,
                    "platform": platform,
                    "date_posted": date_posted,
                    "remote_only": remote_only,
                    "max_jobs": max_jobs,
                    "dry_run": dry_run
                },
                "profile_updates": {},
                "memory_rule": None,
                "response_message": f"Prepared job search for {keyword or 'default role'} in {location or 'default location'} on {platform.upper()} (Freshness: {date_posted})."
            }

        # 6. Profile updates detection
        profile_updates: Dict[str, Any] = {}
        updates_detected = False

        # Preferred / Target Job Roles extraction
        # Matches formats like:
        # "Confirm or edit target job roles (comma separated) (QA Automation Engineer, SDET): Backend Engineer, Full Stack Engineer, Java Engineer, Java Developer"
        # "Target roles: Backend Engineer, Full Stack Engineer"
        # "Target job roles are Java Developer, Backend Engineer"
        roles_match = re.search(
            r'(?:target\s*(?:job\s*)?roles?|preferred\s*roles?)[^:\n=]*(?:[:=]|\bis\b|\bto\b|\bare\b)\s*([^\n\r]+)',
            msg,
            re.IGNORECASE
        )
        if roles_match:
            roles_text = roles_match.group(1).strip()
            roles_list = [r.strip() for r in roles_text.split(",") if r.strip()]
            if roles_list:
                profile_updates["preferred_roles"] = roles_list
                updates_detected = True

        # CTC extraction
        # Handles terminal output pastes like "Current CTC in INR (e.g. 600000) (600000): 576000"
        # as well as natural chat like "current ctc is 5.76 LPA" or "current ctc 576000"
        cur_ctc_match = re.search(r'(?:current\s*ctc|current\s*salary|current\s*lpa)[^:\n]*:\s*(\d[\d,]*(?:\.\d+)?)', msg_lower)
        if not cur_ctc_match:
            cur_ctc_match = re.search(r'(?:current\s*ctc|current\s*salary|current\s*lpa)\s*(?:is|to|=|:)?\s*(\d[\d,]*(?:\.\d+)?)', msg_lower)

        if cur_ctc_match:
            num_str = cur_ctc_match.group(1).replace(",", "")
            try:
                val = float(num_str)
                if val < 100:  # Given in LPA (e.g. 5.76)
                    profile_updates["current_lpa"] = val
                    profile_updates["current_ctc_inr"] = int(val * 100000)
                else:
                    profile_updates["current_ctc_inr"] = int(val)
                    profile_updates["current_lpa"] = round(val / 100000, 2)
                updates_detected = True
            except ValueError:
                pass

        # Expected CTC extraction
        # Handles terminal output pastes like "Expected CTC in INR (e.g. 1200000) (1200000): 9000000"
        # as well as natural chat like "expected ctc is 90 LPA" or "expected salary 9000000"
        exp_ctc_match = re.search(r'(?:expected\s*ctc|expected\s*salary|expected\s*lpa)[^:\n]*:\s*(\d[\d,]*(?:\.\d+)?)', msg_lower)
        if not exp_ctc_match:
            exp_ctc_match = re.search(r'(?:expected\s*ctc|expected\s*salary|expected\s*lpa)\s*(?:is|to|=|:)?\s*(\d[\d,]*(?:\.\d+)?)', msg_lower)

        if exp_ctc_match:
            num_str = exp_ctc_match.group(1).replace(",", "")
            try:
                val = float(num_str)
                if val < 100:  # Given in LPA (e.g. 90.0)
                    profile_updates["expected_lpa"] = val
                    profile_updates["expected_ctc_inr"] = int(val * 100000)
                else:
                    profile_updates["expected_ctc_inr"] = int(val)
                    profile_updates["expected_lpa"] = round(val / 100000, 2)
                updates_detected = True
            except ValueError:
                pass

        # Notice period extraction
        notice_match = re.search(r'(?:notice\s*period|notice)[^:\n]*:\s*(\d+)', msg_lower)
        if not notice_match:
            notice_match = re.search(r'(?:notice\s*period|notice)\s*(?:is|to|=|:)?\s*(\d+)', msg_lower)

        if notice_match:
            val = int(notice_match.group(1))
            if "month" in msg_lower:
                val = val * 30
            elif "week" in msg_lower:
                val = val * 7
            profile_updates["notice_period_days"] = val
            updates_detected = True

        # Skills addition
        skills_match = re.search(r"(?:add\s*skills?|add\s*(.+?)\s*to\s*(?:my\s*)?skills?)", msg_lower)
        if skills_match:
            extracted_raw = skills_match.group(1) or ""
            skills = [s.strip().title() for s in re.split(r"[,&]|\band\b", extracted_raw) if s.strip()]
            if skills:
                profile_updates["add_skills"] = skills
                updates_detected = True

        # Rule extraction
        rule_match = re.search(r"(?:remember|rule|note|keep\s*in\s*mind)\s*[:\-\s]+(.+)", msg, re.IGNORECASE)
        memory_rule = rule_match.group(1).strip() if rule_match else None

        if updates_detected or memory_rule:
            return {
                "intent": "update_profile" if updates_detected else "add_rule",
                "search_params": {},
                "profile_updates": profile_updates,
                "memory_rule": memory_rule,
                "response_message": "Processed your profile update / instruction."
            }

        # 7. General chat fallback
        return {
            "intent": "general_chat",
            "search_params": {},
            "profile_updates": {},
            "memory_rule": None,
            "response_message": "I am here to help you apply for jobs, update your profile details, review your resume, and remember your job preferences. How can I help you today?"
        }

    def apply_profile_updates(self, updates: Dict[str, Any]) -> List[str]:
        """
        Applies verified profile modifications to candidate_profile.json
        and synchronizes with MemoryService. Returns a list of change descriptions.
        """
        if not PROFILE_PATH.exists():
            return []

        try:
            with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            profile = CandidateProfile(**data)
        except Exception as e:
            logger.error(f"Failed to read candidate profile for update: {e}")
            return []

        changes: List[str] = []

        # CTC Updates
        if updates.get("current_lpa") or updates.get("current_ctc_inr"):
            cur_lpa = updates.get("current_lpa")
            cur_inr = updates.get("current_ctc_inr")
            if cur_lpa and not cur_inr:
                cur_inr = int(cur_lpa * 100000)
            elif cur_inr and not cur_lpa:
                cur_lpa = round(cur_inr / 100000, 2)
            profile.professional.current_lpa = float(cur_lpa)
            self.memory_service.set_preference("current_ctc_inr", int(cur_inr))
            changes.append(f"Current CTC updated to INR {int(cur_inr):,} ({cur_lpa} LPA)")

        if updates.get("expected_lpa") or updates.get("expected_ctc_inr"):
            exp_lpa = updates.get("expected_lpa")
            exp_inr = updates.get("expected_ctc_inr")
            if exp_lpa and not exp_inr:
                exp_inr = int(exp_lpa * 100000)
            elif exp_inr and not exp_lpa:
                exp_lpa = round(exp_inr / 100000, 2)
            profile.professional.expected_lpa = float(exp_lpa)
            self.memory_service.set_preference("expected_ctc_inr", int(exp_inr))
            changes.append(f"Expected CTC updated to INR {int(exp_inr):,} ({exp_lpa} LPA)")

        # Notice Period
        if updates.get("notice_period_days") is not None:
            nd = int(updates["notice_period_days"])
            profile.professional.notice_period_days = nd
            self.memory_service.set_preference("notice_period_days", nd)
            changes.append(f"Notice Period updated to {nd} days (~{round(nd / 7)} weeks)")

        # Experience
        if updates.get("total_experience_years") is not None:
            exp = float(updates["total_experience_years"])
            profile.professional.total_experience_years = exp
            self.memory_service.set_preference("total_experience_years", exp)
            changes.append(f"Total Experience updated to {exp} years")

        # Designation & Company
        if updates.get("designation"):
            profile.professional.designation = updates["designation"]
            changes.append(f"Designation updated to {updates['designation']}")

        if updates.get("current_company"):
            profile.professional.current_company = updates["current_company"]
            changes.append(f"Company updated to {updates['current_company']}")

        # Personal details
        if updates.get("full_name"):
            profile.personal.full_name = updates["full_name"]
            changes.append(f"Name updated to {updates['full_name']}")

        if updates.get("phone"):
            clean_phone = re.sub(r"[^\d]", "", updates["phone"])[-10:]
            profile.personal.phone = clean_phone
            changes.append(f"Phone updated to {clean_phone}")

        if updates.get("email"):
            profile.personal.email = updates["email"]
            changes.append(f"Email updated to {updates['email']}")

        if updates.get("location"):
            profile.personal.location = updates["location"]
            changes.append(f"Location updated to {updates['location']}")

        # Add Skills
        if updates.get("add_skills"):
            added = []
            existing_lower = {s.lower(): s for s in profile.skills}
            for s in updates["add_skills"]:
                if s.strip().lower() not in existing_lower:
                    profile.skills.append(s.strip())
                    added.append(s.strip())
            if added:
                changes.append(f"Added skills: {', '.join(added)}")

        # Remove Skills
        if updates.get("remove_skills"):
            to_remove = [s.strip().lower() for s in updates["remove_skills"]]
            profile.skills = [s for s in profile.skills if s.lower() not in to_remove]
            changes.append(f"Removed skills: {', '.join(updates['remove_skills'])}")

        # Preferred Roles
        if updates.get("preferred_roles"):
            roles = [r.strip() for r in updates["preferred_roles"] if r.strip()]
            if roles:
                profile.preferred_roles = roles
                self.memory_service.set_preference("preferred_roles", roles)
                changes.append(f"Target roles set to: {', '.join(roles)}")

        # Preferred Locations
        if updates.get("preferred_locations"):
            locs = [l.strip() for l in updates["preferred_locations"] if l.strip()]
            if locs:
                profile.preferred_locations = locs
                self.memory_service.set_preference("preferred_locations", locs)
                changes.append(f"Preferred locations set to: {', '.join(locs)}")

        # Save to disk
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))

        # Reset singleton cache
        ProfileLoader.reset()

        # Save note in memory
        if changes:
            note = f"Profile updated via chat: {'; '.join(changes)}."
            self.memory_service.add_conversation_note(note)

        return changes

    async def execute_search_and_apply(self, search_params: Dict[str, Any]) -> None:
        """
        Presents search parameters, requests confirmation, and runs the application automation.
        """
        from apply_jobs import run_simple_agent

        profile = self.get_candidate_profile()
        prefs = self.memory_service.get_all_preferences()

        # Resolve parameters with defaults
        target_role = (
            search_params.get("keyword")
            or (profile.preferred_roles[0] if profile and profile.preferred_roles else "Software Engineer")
        )
        target_location = (
            search_params.get("location")
            or (profile.preferred_locations[0] if profile and profile.preferred_locations else "Remote")
        )
        target_platform = search_params.get("platform") or "all"
        target_date_filter = search_params.get("date_posted") or prefs.get("default_date_filter", "24h")
        target_remote = bool(search_params.get("remote_only", False))
        target_max_jobs = int(search_params.get("max_jobs") or 5)
        target_dry_run = bool(search_params.get("dry_run", False))
        # Prompt user explicitly for Auto-Apply vs Manual Approval
        self.console.print("\n[bold cyan]Application Submission Mode:[/bold cyan]")
        self.console.print("  [bold green][1][/bold green] ⚡ [bold white]Auto-Apply[/bold white] (Automatically submit applications without pausing)")
        self.console.print("  [bold yellow][2][/bold yellow] 👁️  [bold white]Manual Approval[/bold white] (Pause at final review screen for your approval)")
        default_mode = "1" if prefs.get("auto_approve_applications", False) else "2"
        approval_choice = Prompt.ask("Select submission mode", choices=["1", "2"], default=default_mode)
        target_auto_approve = (approval_choice == "1")

        # Render Search Confirmation Table
        table = Table(title="[bold cyan]Job Search & Application Parameters[/bold cyan]")
        table.add_column("Parameter", style="bold white", width=20)
        table.add_column("Setting", style="yellow")
        table.add_row("Target Role", target_role)
        table.add_row("Location", target_location)
        table.add_row("Platform(s)", target_platform.upper())
        table.add_row("Date Posted / Freshness", target_date_filter)
        table.add_row("Remote Only", "Yes" if target_remote else "No")
        table.add_row("Max Jobs to Apply", str(target_max_jobs))
        table.add_row("Submission Mode", "[bold green]⚡ AUTO-APPLY (Autonomous)[/bold green]" if target_auto_approve else "[bold yellow]👁️  MANUAL APPROVAL (Review each)[/bold yellow]")
        table.add_row("Execution Mode", "[bold red]DRY RUN (Simulate)[/bold red]" if target_dry_run else "[bold green]LIVE APPLY[/bold green]")

        self.console.print("\n")
        self.console.print(table)

        if Confirm.ask("\n[bold green]Ready to launch job search with these settings?[/bold green]", default=True):
            self.console.print("\n[bold cyan]🚀 Starting automation browser session...[/bold cyan]\n")
            try:
                await run_simple_agent(
                    keyword=target_role,
                    location=target_location,
                    platform=target_platform,
                    date_posted=target_date_filter,
                    remote_only=target_remote,
                    max_jobs=target_max_jobs,
                    auto_approve=target_auto_approve,
                    dry_run=target_dry_run
                )
                self.console.print("\n[bold green][OK] Application session completed.[/bold green]")
            except Exception as e:
                self.console.print(f"\n[bold red]Error during application session: {e}[/bold red]")
        else:
            self.console.print("[dim]Search cancelled. Continuing chat session...[/dim]")

    async def run_chat_loop(self) -> None:
        """
        Interactive conversational REPL loop.
        Allows continuous dialogue, status checks, profile updates, and command execution.
        """
        self.console.print(Panel.fit(
            "[bold cyan]=====================================================[/bold cyan]\n"
            "[bold white]           💬 AI Job Agent Chat Assistant             [/bold white]\n"
            "[dim]  Talk naturally to update details, set rules, or search jobs [/dim]\n"
            "[bold cyan]=====================================================[/bold cyan]\n"
            "[dim]Examples:[/dim]\n"
            "  • [yellow]\"Search for remote SDET jobs on LinkedIn posted in 24h\"[/yellow]\n"
            "  • [yellow]\"Update my expected CTC to 15 LPA and notice period to 30 days\"[/yellow]\n"
            "  • [yellow]\"Add Cypress and Docker to my skills\"[/yellow]\n"
            "  • [yellow]\"Remember: always select Yes for experience questions\"[/yellow]\n"
            "  • [yellow]Type /help, /profile, /review, /history, or /exit[/yellow]",
            border_style="cyan"
        ))

        while True:
            try:
                user_msg = Prompt.ask("\n[bold cyan]You[/bold cyan]")
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[yellow]Exiting chat mode.[/yellow]")
                break

            clean_msg = user_msg.strip()
            if not clean_msg:
                continue

            msg_lower = clean_msg.lower()

            # Fast Exit / Menu option 8
            if msg_lower in ["/exit", "exit", "quit", "/quit", "8", "bye", "q"]:
                self.console.print("\n[bold cyan]Returning to main menu. Goodbye![/bold cyan]\n")
                break

            # Fast Clear screen
            if msg_lower in ["clear", "cls", "/clear"]:
                self.chat_history.clear()
                import os
                os.system("cls" if os.name == "nt" else "clear")
                self.console.print("[dim]Chat history cleared.[/dim]")
                continue

            # Fast Greetings (instant 0ms response)
            if msg_lower in ["hi", "hello", "hey", "hola", "namaste", "good morning", "good afternoon", "good evening", "yo", "sup"]:
                profile = self.get_candidate_profile()
                first_name = (profile.personal.full_name.split()[0]) if profile and profile.personal and profile.personal.full_name else "there"
                greeting_reply = f"Hello {first_name}! How can I help you today? You can tell me to search & apply for jobs, update profile details, or review your resume."
                self.console.print(f"\n[bold purple]Agent[/bold purple]: {greeting_reply}\n")
                self.chat_history.append({"role": "assistant", "content": greeting_reply})
                continue

            # Fast Numeric menu shortcuts if user types options inside chat
            if msg_lower == "1":
                self.console.print("\n[bold purple]Agent[/bold purple]: You are currently in Chat Mode! Ask me anything, update your profile, or tell me which jobs to apply for.\n")
                continue
            if msg_lower == "2":
                self.console.print("\n[bold purple]Agent[/bold purple]: Tell me what role and location you'd like to apply for (e.g. 'Apply for Software Engineer jobs in Remote on LinkedIn').\n")
                continue
            if msg_lower == "3" or msg_lower in ["/applied", "/jobs", "applied"]:
                from services.db_service import DatabaseService
                db_service = DatabaseService()
                await db_service.display_applied_jobs_table(console=self.console)
                continue
            if msg_lower == "4" or msg_lower in ["/review", "review"]:
                await self.setup_service.run_resume_review()
                continue
            if msg_lower == "5" or msg_lower in ["/profile", "profile"]:
                self.setup_service.display_status()
                continue
            if msg_lower == "7" or msg_lower in ["/history", "history"]:
                self._print_history()
                continue
            if msg_lower in ["/help", "help", "?"]:
                self._print_help()
                continue

            # Record turn in chat history
            self.chat_history.append({"role": "user", "content": clean_msg})

            self.console.print("[dim]Thinking...[/dim]")
            interpretation = await self.interpret_message(clean_msg)

            intent = interpretation.get("intent", "general_chat")
            response_msg = interpretation.get("response_message", "")
            profile_updates = interpretation.get("profile_updates", {})
            memory_rule = interpretation.get("memory_rule")
            search_params = interpretation.get("search_params", {})

            # 1. Handle profile updates
            if profile_updates:
                changes = self.apply_profile_updates(profile_updates)
                if changes:
                    changes_text = "\n".join([f"  • [green]{c}[/green]" for c in changes])
                    self.console.print(Panel(
                        f"[bold green]Profile Updated Successfully:[/bold green]\n{changes_text}",
                        border_style="green"
                    ))

            # 2. Handle memory rule
            if memory_rule:
                self.memory_service.add_conversation_note(f"Rule: {memory_rule}")
                self.console.print(Panel(
                    f"[bold cyan]🧠 Saved to Agent Memory:[/bold cyan]\n  [white]{memory_rule}[/white]\n"
                    f"[dim]The agent will automatically apply this rule in all future form fillings.[/dim]",
                    border_style="cyan"
                ))

            # 3. Print assistant response message (for non-search intents)
            if response_msg and intent != "search_and_apply":
                self.console.print(f"\n[bold purple]Agent[/bold purple]: {response_msg}\n")
                self.chat_history.append({"role": "assistant", "content": response_msg})

            # 4. Handle specific intents
            if intent == "show_profile":
                self.setup_service.display_status()

            elif intent == "review_resume":
                await self.setup_service.run_resume_review()

            elif intent == "view_applied_jobs":
                from services.db_service import DatabaseService
                db_service = DatabaseService()
                await db_service.display_applied_jobs_table(console=self.console)

            elif intent == "view_history":
                self._print_history()

            elif intent == "search_and_apply":
                if response_msg:
                    self.console.print(f"\n[bold purple]Agent[/bold purple]: {response_msg}")
                await self.execute_search_and_apply(search_params)

            elif intent == "exit":
                self.console.print("\n[bold cyan]Exiting chat mode.[/bold cyan]\n")
                break

    def _print_help(self) -> None:
        table = Table(title="[bold cyan]Available Commands & Chat Actions[/bold cyan]")
        table.add_column("Command / Intent", style="bold white", width=25)
        table.add_column("Example Natural Language Prompt", style="yellow")
        table.add_row("Search & Apply", "\"Search for remote SDET jobs on LinkedIn posted in 24h\"")
        table.add_row("Multi-Platform Apply", "\"Find QA Automation jobs in Hyderabad on both platforms\"")
        table.add_row("Dry Run Mode", "\"Search Naukri for Test Lead jobs in dry run mode\"")
        table.add_row("Update Compensation", "\"Change my expected CTC to 15 LPA and current to 9 LPA\"")
        table.add_row("Update Skills", "\"Add Cypress, Playwright, and Docker to my skills\"")
        table.add_row("Update Notice Period", "\"My notice period is now 30 days\"")
        table.add_row("Save Form Rules", "\"Remember: always select Yes for experience questions\"")
        table.add_row("/profile", "Displays candidate profile and configured values")
        table.add_row("/applied", "Displays all jobs applied till now with links and status")
        table.add_row("/review", "Runs AI ATS Resume Review and gap analysis")
        table.add_row("/history", "Displays past application sessions and learned memory")
        table.add_row("/exit", "Exits chat mode and returns to main menu")
        self.console.print(table)

    def _print_history(self) -> None:
        notes = self.memory_service.get_conversation_notes()
        self.console.print("\n[bold cyan]=== Agent Learned Rules & Notes ===[/bold cyan]")
        for i, n in enumerate(notes, 1):
            self.console.print(f"  [dim]{i}.[/dim] {n}")

        history = self.memory_service.get_session_history()
        if history:
            self.console.print("\n[bold cyan]=== Past Application Sessions ===[/bold cyan]")
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
            self.console.print(table)
        else:
            self.console.print("\n[dim]No application sessions recorded yet.[/dim]")
