import copy
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

MEMORY_FILE_PATH = Path(settings.DATA_DIR / "config" / "agent_memory.json")

DEFAULT_MEMORY: Dict[str, Any] = {
    "version": "1.0",
    "last_updated": datetime.now(timezone.utc).isoformat(),
    "user_preferences": {
        "preferred_platforms": ["linkedin", "naukri"],
        "default_date_filter": "24h",
        "default_sort_by": "date",
        "preferred_locations": ["Hyderabad", "Remote"],
        "auto_approve_applications": False,
        "easy_apply_only": True
    },
    "conversation_notes": [
        "Rule: For profile, testing, tools, or experience questions ending in Yes/No, automatically select Yes.",
        "Rule: Use candidate's existing LinkedIn uploaded resume directly without re-uploading every time.",
        "Rule: Phone input should only contain the clean 10-digit number (without +91 country code prefix)."
    ],
    "learned_form_rules": {
        "ctc_inr_format": "raw_integer",
        "notice_weeks_calc": "days_divided_by_7_rounded",
        "profile_yes_no": "always_yes_for_profile_skills",
        "sponsorship_yes_no": "always_no"
    },
    "saved_form_answers": {},
    "session_history": []
}

class MemoryService:
    """
    Manages persistent cross-session memory for the AI agent.
    Ensures the agent remembers user instructions, past conversation preferences,
    filter settings, and job application history.
    """

    def __init__(self, memory_path: Optional[Path] = None):
        self.memory_path = memory_path or MEMORY_FILE_PATH
        self._memory: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        default_copy = copy.deepcopy(DEFAULT_MEMORY)
        if self.memory_path.exists():
            try:
                with open(self.memory_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    for k, v in default_copy.items():
                        if k not in data:
                            data[k] = v
                    return data
            except Exception as e:
                logger.warning(f"Failed to load agent memory ({e}), initializing default.")
        
        self._save(default_copy)
        return default_copy

    def _save(self, data: Dict[str, Any]) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.memory_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Retrieve a specific user preference."""
        return self._memory.get("user_preferences", {}).get(key, default)

    def set_preference(self, key: str, value: Any) -> None:
        """Update or set a user preference and persist to disk."""
        if "user_preferences" not in self._memory:
            self._memory["user_preferences"] = {}
        self._memory["user_preferences"][key] = value
        self._save(self._memory)

    def get_all_preferences(self) -> Dict[str, Any]:
        """Return all user preferences."""
        return dict(self._memory.get("user_preferences", {}))

    def add_conversation_note(self, note: str) -> None:
        """Record an important instruction or decision from the user conversation."""
        if "conversation_notes" not in self._memory:
            self._memory["conversation_notes"] = []
        if note not in self._memory["conversation_notes"]:
            self._memory["conversation_notes"].append(note)
            self._save(self._memory)

    def get_conversation_notes(self) -> List[str]:
        """Return all recorded conversation notes."""
        return list(self._memory.get("conversation_notes", []))

    def record_session(self, session_summary: Dict[str, Any]) -> None:
        """Log a completed application session."""
        if "session_history" not in self._memory:
            self._memory["session_history"] = []
        session_summary["timestamp"] = datetime.now(timezone.utc).isoformat()
        self._memory["session_history"].append(session_summary)
        if len(self._memory["session_history"]) > 50:
            self._memory["session_history"] = self._memory["session_history"][-50:]
        self._save(self._memory)

    def get_session_history(self) -> List[Dict[str, Any]]:
        """Return past session summaries."""
        return list(self._memory.get("session_history", []))

    def get_context_prompt_for_llm(self) -> str:
        """
        Generate a memory context string to include in LLM prompts.
        """
        notes = self.get_conversation_notes()
        prefs = self.get_all_preferences()
        return (
            "User Preferences & Historical Chat Memory:\n"
            f"- Preferred CTC: Current INR {prefs.get('current_ctc_inr', 890000):,} | Expected INR {prefs.get('expected_ctc_inr', 1200000):,}\n"
            f"- Notice Period: {prefs.get('notice_period_days', 60)} days\n"
            f"- Past Conversation Rules:\n  * " + "\n  * ".join(notes[-5:])
        )

    def _normalize_key(self, key: str) -> str:
        """Normalizes a form question label for matching across variations."""
        k = re.sub(r'[\?\*\:\(\)]', '', key).lower().strip()
        k = re.sub(r'\s+', ' ', k)
        return k

    def save_form_answer(self, label: str, answer: str, field_type: Optional[str] = None) -> None:
        """Saves an answer to a form question so future applications can reuse it."""
        if not label or answer is None or str(answer).strip() == "":
            return
        norm_key = self._normalize_key(label)
        if "saved_form_answers" not in self._memory:
            self._memory["saved_form_answers"] = {}
        self._memory["saved_form_answers"][norm_key] = {
            "answer": str(answer).strip(),
            "raw_label": label,
            "field_type": field_type or "text",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        self._save(self._memory)

    def get_form_answer(self, label: str) -> Optional[str]:
        """Retrieves a previously saved answer for a form question by exact or key match."""
        if not label:
            return None
        saved = self._memory.get("saved_form_answers", {})
        if not saved:
            return None
        norm_key = self._normalize_key(label)

        def _sanitize_answer(ans: Optional[str], q_label: str) -> Optional[str]:
            if ans is None:
                return None
            q_lower = q_label.lower()

            # Rule: Stakeholder communication question always uses 0 value
            if any(phrase in q_lower for phrase in [
                "communicated testing progress",
                "communicating testing progress",
                "release readiness to stakeholders",
                "testing progress, risks, defects",
                "stakeholders or external clients"
            ]):
                return "0"

            # Rule: INR / Annual compensation / Whole number > 100
            if any(term in q_lower for term in ["in inr", "inr", "annual", "larger than 100", "200000", "350000", "rupees", "rupee"]):
                try:
                    num_val = float(re.sub(r'[^\d.]', '', ans))
                    if num_val > 0 and num_val <= 100:
                        # Convert LPA e.g. 8.99 -> 899000, 15 -> 1500000
                        return str(int(round(num_val * 100000)))
                except (ValueError, TypeError):
                    pass
            # Rule: Numeric/experience questions must never return non-numeric answers (e.g. 'Yes', 'No', or long descriptions)
            is_numeric_q = (
                any(q_kw in q_lower for q_kw in [
                    "how many", "years of", "years experience", "number of",
                    "decimal number", "how soon", "notice period in days",
                    "larger than 0", "larger than 100", "ctc in lpa", "ctc in inr"
                ])
                and not any(bin_kw in q_lower for bin_kw in ["do you have", "are you", "can you", "will you", "have you", "yes/no"])
            )
            if is_numeric_q:
                # If the candidate answer has no digits, it cannot be valid for a numeric question
                if not re.search(r'\d', ans):
                    return None

            # Rule: Notice period / How soon can you join must never return 'No'
            if any(term in q_lower for term in ["how soon can you join", "notice period in days", "mention the number of days"]):
                if ans.strip().lower() in ["no", "yes", "false", "true"]:
                    return "60"

            # Rule: Face to Face / interview questions must never return a location string
            if any(term in q_lower for term in ["face to face", "f2f", "come for round", "walk-in"]):
                if "india" in ans.lower() or "hyderabad" in ans.lower() or len(ans) > 20:
                    return "Yes"

            return ans

        # 1. Exact match
        if norm_key in saved:
            return _sanitize_answer(saved[norm_key].get("answer"), label)

        # Check multi-line label or labels containing 'Required' / '*'
        first_line = label.split("\n")[0]
        norm_first_line = self._normalize_key(first_line.replace("Required", "").replace("*", ""))
        if norm_first_line and norm_first_line in saved:
            return _sanitize_answer(saved[norm_first_line].get("answer"), label)

        # Strip standard conversational question prefixes
        clean_prompt = norm_key
        prefixes_to_strip = [
            r"^how many years of (?:work )?experience do you have (?:with|in)\s+",
            r"^how many years of\s+",
            r"^years of (?:work )?experience (?:with|in)\s+",
            r"^do you have (?:any )?experience (?:with|in)\s+",
            r"^what is your\s+",
            r"^please (?:enter|specify|select) your\s+",
            r"^select your\s+",
            r"^rate your proficiency in\s+",
        ]
        for pat in prefixes_to_strip:
            clean_prompt = re.sub(pat, "", clean_prompt).strip()

        if clean_prompt in saved:
            return _sanitize_answer(saved[clean_prompt].get("answer"), label)

        clean_first_prompt = norm_first_line
        for pat in prefixes_to_strip:
            clean_first_prompt = re.sub(pat, "", clean_first_prompt).strip()
        if clean_first_prompt in saved:
            return _sanitize_answer(saved[clean_first_prompt].get("answer"), label)

        is_incoming_numeric = any(q_kw in label.lower() for q_kw in ["how many", "years of", "number of", "decimal number"])

        # 2. Key boundary match sorted by descending length (most specific keys match first)
        sorted_keys = sorted(saved.keys(), key=lambda k: len(k), reverse=True)
        for k in sorted_keys:
            entry = saved[k]
            # If the specific key is contained as whole words in norm_key, clean_prompt, or clean_first_prompt
            if len(k) >= 3 and (
                re.search(rf"\b{re.escape(k)}\b", norm_key)
                or re.search(rf"\b{re.escape(k)}\b", clean_prompt)
                or re.search(rf"\b{re.escape(k)}\b", clean_first_prompt)
            ):
                ans = entry.get("answer")
                if is_incoming_numeric and (not ans or not re.search(r'\d', str(ans))):
                    continue
                return _sanitize_answer(ans, label)

        # 3. Reverse boundary match (if norm_key is contained as whole words in saved key)
        for k in sorted_keys:
            entry = saved[k]
            ans = entry.get("answer")
            if is_incoming_numeric and (not ans or not re.search(r'\d', str(ans))):
                continue
            # Do not match short skill names against long descriptive questions
            if len(clean_prompt) >= 3 and re.search(rf"\b{re.escape(clean_prompt)}\b", k):
                if is_incoming_numeric and entry.get("field_type") in ["RADIO", "SELECT"]:
                    continue
                return _sanitize_answer(ans, label)
            if len(clean_first_prompt) >= 3 and re.search(rf"\b{re.escape(clean_first_prompt)}\b", k):
                if is_incoming_numeric and entry.get("field_type") in ["RADIO", "SELECT"]:
                    continue
                return _sanitize_answer(ans, label)

        return None

    def get_all_saved_form_answers(self) -> Dict[str, Any]:
        """Returns all remembered form answers."""
        return dict(self._memory.get("saved_form_answers", {}))

    def seed_from_profile(self, profile: Any) -> None:
        """Seeds memory with candidate profile data so the agent never starts freshly with empty."""
        if not profile:
            return
        try:
            total_exp = str(int(round(profile.professional.total_experience_years))) if profile.professional.total_experience_years >= 1 else str(profile.professional.total_experience_years)
            cur_lpa = str(int(profile.professional.current_lpa)) if profile.professional.current_lpa.is_integer() else str(profile.professional.current_lpa)
            exp_lpa = str(int(profile.professional.expected_lpa)) if profile.professional.expected_lpa.is_integer() else str(profile.professional.expected_lpa)
            cur_inr = str(int(profile.professional.current_lpa * 100000))
            exp_inr = str(int(profile.professional.expected_lpa * 100000))

            gender = getattr(profile.personal, "gender", "Male") or "Male"
            race_eth = getattr(profile.personal, "race_ethnicity", "Asian") or "Asian"

            initial_mappings = [
                ("total it experience", total_exp, "number"),
                ("total experience", total_exp, "number"),
                ("overall experience", total_exp, "number"),
                ("it experience", total_exp, "number"),
                ("years of experience", total_exp, "number"),
                ("experience in years", total_exp, "number"),
                ("experience (years)", total_exp, "number"),
                ("how many years of experience do you have in total", total_exp, "number"),
                ("total work experience", total_exp, "number"),
                ("selenium", total_exp, "number"),
                ("selenium experience", total_exp, "number"),
                ("core java", total_exp, "number"),
                ("core java experience", total_exp, "number"),
                ("java", total_exp, "number"),
                ("rest assured", total_exp, "number"),
                ("restassured", total_exp, "number"),
                ("current ctc in inr", cur_inr, "number"),
                ("expected ctc in inr", exp_inr, "number"),
                ("please enter your current ctc in inr", cur_inr, "number"),
                ("please enter your expected ctc in inr", exp_inr, "number"),
                ("current annual compensation in inr", cur_inr, "number"),
                ("expected annual compensation in inr", exp_inr, "number"),
                ("current ctc inr", cur_inr, "number"),
                ("expected ctc inr", exp_inr, "number"),
                ("current ctc", cur_lpa, "number"),
                ("expected ctc", exp_lpa, "number"),
                ("notice period", str(profile.professional.notice_period_days), "number"),
                ("current company", profile.professional.current_company, "text"),
                ("current designation", profile.professional.designation, "text"),
                ("full name", profile.personal.full_name, "text"),
                ("email", profile.personal.email, "text"),
                ("phone", profile.personal.phone, "text"),
                ("location", profile.personal.location, "text"),
                ("race/ethnicity", race_eth, "radio"),
                ("race", race_eth, "radio"),
                ("ethnicity", race_eth, "radio"),
                ("gender", gender, "radio"),
                ("disability status", "No, I don't have a disability", "radio"),
                ("veteran status", "I am not a protected veteran", "radio"),
                ("legally authorized to work in country", "Yes", "radio"),
                ("legally authorized to work", "Yes", "radio"),
                ("require sponsorship", "No", "radio"),
                ("18 years of age", "Yes", "radio"),
                ("product-based companies worked for", "Enterprise Software / SaaS Product Company", "text"),
                ("product based companies", "Enterprise Software / SaaS Product Company", "text"),
                ("automation testing for ipaas, saas, or cloud-based platforms", "Yes", "radio"),
                ("automation testing for ipaas", "Yes", "radio"),
                ("api testing – postman/swagger/etc", "Advanced", "select"),
                ("api testing postman/swagger/etc", "Advanced", "select"),
                ("api testing postman", "Advanced", "select"),
                ("frameworks worked with", "Selenium", "text"),
                ("python experience", "Beginner", "select"),
                ("pyscript experience", "No experience", "select"),
                ("pyscript", "No experience", "select"),
                ("backend applications/services hosted on aws", "No experience", "select"),
                ("hosted on aws", "No experience", "select"),
            ]
            saved = self._memory.setdefault("saved_form_answers", {})
            changed = False
            for label, ans, ftype in initial_mappings:
                norm_k = self._normalize_key(label)
                if norm_k not in saved or (norm_k in ["race/ethnicity", "race", "ethnicity", "gender", "selenium", "core java", "total it experience", "rest assured", "restassured"] and saved[norm_k]["answer"] != ans):
                    saved[norm_k] = {
                        "answer": ans,
                        "raw_label": label,
                        "field_type": ftype,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    changed = True
            if changed:
                self._save(self._memory)
        except Exception as e:
            logger.debug(f"Failed to seed memory from profile: {e}")

