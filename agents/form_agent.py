import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from models.form import FormField, FormFieldType
from models.profile import CandidateProfile
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.resume_service import ResumeService

logger = logging.getLogger(__name__)

class QuestionCategory(str, Enum):
    PROFILE_KNOWN = "PROFILE_KNOWN"
    PROFILE_DERIVED = "PROFILE_DERIVED"
    SAFE_HEURISTIC = "SAFE_HEURISTIC"
    USER_REQUIRED = "USER_REQUIRED"
    SENSITIVE = "SENSITIVE"
    LEGAL = "LEGAL"
    REGULATORY = "REGULATORY"
    UNKNOWN = "UNKNOWN"

# Sensitive question patterns requiring careful handling or HITL prompt
SENSITIVE_PATTERNS = [
    r"security clearance",
    r"disability",
    r"veteran status",
    r"criminal",
    r"background check",
    r"citizenship",
]

class FormAgent:
    """
    Interprets and answers dynamic questions presented in Easy Apply modals,
    routing between persistent memory, deterministic profile fields, resume text extraction,
    LLM reasoning, and HITL prompts.
    """

    def __init__(
        self,
        llm_service: LLMService,
        memory_service: Optional[MemoryService] = None,
        resume_service: Optional[ResumeService] = None
    ):
        self.llm_service = llm_service
        self.memory_service = memory_service or MemoryService()
        self.resume_service = resume_service or ResumeService()
        self._resume_text: Optional[str] = None

    def classify_question(self, field: FormField) -> QuestionCategory:
        """
        Classifies a form question into an explainable category:
        PROFILE_KNOWN, PROFILE_DERIVED, SAFE_HEURISTIC, USER_REQUIRED, SENSITIVE, LEGAL, UNKNOWN.
        """
        label_l = field.label.lower().strip()

        # 1. SENSITIVE: Race, Ethnicity, Gender, Disability, Veteran
        if any(w in label_l for w in ["race", "ethnicity", "ethnic origin", "demographic"]):
            return QuestionCategory.SENSITIVE
        if any(w in label_l for w in ["gender", "sex", "sexual orientation"]) and not any(w in label_l for w in ["gap", "pay"]):
            return QuestionCategory.SENSITIVE
        if any(w in label_l for w in ["disability", "handicap", "veteran"]):
            return QuestionCategory.SENSITIVE
        if self._is_sensitive_ambiguous(label_l):
            return QuestionCategory.SENSITIVE

        # 2. LEGAL: Work authorization, Visa sponsorship, Legal status
        if any(w in label_l for w in [
            "authorized to work", "legally authorized", "work authorization",
            "visa sponsorship", "require sponsorship", "sponsorship", "citizenship",
            "right to work in", "proof of identity and eligibility"
        ]):
            return QuestionCategory.LEGAL

        # 3. PROFILE_KNOWN: Basic direct profile fields
        if any(w in label_l for w in [
            "full name", "first name", "last name", "given name", "surname",
            "email", "phone", "mobile", "country code", "city", "current company",
            "current job title", "designation", "employer", "expected ctc",
            "expected salary", "current ctc", "current salary", "notice period",
            "portfolio", "github", "linkedin"
        ]):
            return QuestionCategory.PROFILE_KNOWN

        # 4. PROFILE_DERIVED: Experience with skills, total IT experience
        if any(w in label_l for w in [
            "years of experience", "experience in years", "how many years",
            "total it experience", "total experience", "overall experience",
            "experience with", "experience in", "additional months"
        ]):
            return QuestionCategory.PROFILE_DERIVED

        # 5. SAFE_HEURISTIC: Common standard employment questions
        if any(w in label_l for w in [
            "at least 18", "18 years of age", "meet the qualifications",
            "comfortable commuting", "willing to relocate", "full-time",
            "bachelor", "degree", "education", "graduation"
        ]):
            return QuestionCategory.SAFE_HEURISTIC

        # 6. REGULATORY: Bar admission, CPA, Medical/Nursing license, Professional Engineer (PE), Security clearance
        if any(w in label_l for w in [
            "bar admission", "licensed attorney", "medical license", "registered nurse",
            "rn license", "cpa", "certified public accountant", "professional engineer",
            "pe license", "security clearance", "top secret", "ts/sci", "active clearance"
        ]):
            return QuestionCategory.REGULATORY

        if field.is_required:
            return QuestionCategory.USER_REQUIRED
        return QuestionCategory.UNKNOWN

    def is_user_required(self, field: FormField, profile: Optional[CandidateProfile] = None) -> bool:
        """Returns True if the field requires human user confirmation/review."""
        cat = self.classify_question(field)
        if cat in [QuestionCategory.SENSITIVE, QuestionCategory.LEGAL, QuestionCategory.USER_REQUIRED]:
            return True
        if cat == QuestionCategory.REGULATORY:
            # If candidate explicitly holds verified license/certification, human intervention not needed
            if profile:
                all_creds = [c.lower() for c in (getattr(profile, "licenses", []) + getattr(profile, "certifications", []))]
                label_l = field.label.lower()
                for cred in all_creds:
                    if cred and (cred in label_l or label_l in cred):
                        return False
            return True
        return False

    @property
    def resume_text(self) -> str:
        """Returns cached raw text extracted from candidate's resume PDF."""
        if self._resume_text is None:
            try:
                self._resume_text = self.resume_service.extract_text()
            except Exception as e:
                logger.warning(f"Could not extract resume text: {e}")
                self._resume_text = ""
        return self._resume_text

    async def resolve_field_value(
        self,
        field: FormField,
        profile: CandidateProfile,
        job_description: Optional[str] = None
    ) -> Tuple[str, bool]:
        """
        Resolves the appropriate value for a FormField, checking previously saved
        answers in persistent memory before falling back to dynamic resolution.
        """
        label_lower = field.label.lower()
        # 0A. Top Priority Rule: Stakeholder / External Client Communication: Use 0 value (never 'Practical use')
        if any(phrase in label_lower for phrase in [
            "communicated testing progress",
            "communicating testing progress",
            "release readiness to stakeholders",
            "testing progress, risks, defects",
            "stakeholders or external clients"
        ]):
            if field.options:
                zero_opt = self._find_option_matching(field.options, [
                    "0", "0 value", "none", "no experience", "0 - none", "no", "0 years", "limited exposure"
                ])
                if zero_opt:
                    return zero_opt, False
                for opt in field.options:
                    if re.search(r'\b0\b', opt.lower()):
                        return opt, False
                return field.options[0], False
            return "0", False

        # 0B. Check persistent memory for previously answered question from profile/past sessions
        # Memory Safeguard: Never reuse memory answers if question is about immediate joining
        is_immediate_q = any(w in label_lower for w in ["immediate joiner", "join immediately", "immediate joining", "available to join immediately"])
        saved_val = None if is_immediate_q else self.memory_service.get_form_answer(field.label)
        if saved_val is not None and str(saved_val).strip() != "":
            saved_str = str(saved_val).strip()

            is_numeric_field = (
                field.field_type == FormFieldType.NUMBER
                or (
                    any(k in label_lower for k in [
                        "how many", "years", "experience", "how soon", "ctc", "salary",
                        "compensation", "notice", "days", "months", "lakh", "lpa", "decimal", "whole number"
                    ])
                    and not any(bin_kw in label_lower for bin_kw in ["do you have", "are you", "can you", "will you", "have you", "yes/no"])
                )
            )

            # If question is numeric, saved answer MUST contain digits and not be Yes/No
            if is_numeric_field and not field.options:
                if not re.search(r'\d', saved_str) or saved_str.lower() in ["yes", "no", "true", "false"]:
                    saved_val = None
                    saved_str = ""

            if saved_val is not None and saved_str:
                # Safeguard: If question asks for INR / annual whole number but memory has LPA decimal <= 100
                is_inr_q = any(w in label_lower for w in ["in inr", "inr", "annual", "larger than 100", "200000", "350000", "rupees", "rupee"])
                if is_inr_q and any(w in label_lower for w in ["ctc", "salary", "compensation"]):
                    try:
                        num_chk = float(re.sub(r'[^\d.]', '', saved_str))
                        if 0 < num_chk <= 100:
                            saved_str = str(int(round(num_chk * 100000)))
                    except (ValueError, TypeError):
                        pass

                # Safeguard: If question asks for CTC in LPA using one or two digits, e.g. 6 or 8
                if any(w in label_lower for w in ["one or two digits", "single digit", "e.g., 6 or 8", "e.g. 6 or 8", "e.g. 6", "e.g., 6"]):
                    try:
                        num_chk = float(re.sub(r'[^\d.]', '', saved_str))
                        if 0 < num_chk <= 100:
                            saved_str = str(int(round(num_chk)))
                    except (ValueError, TypeError):
                        pass

                if field.options:
                    for opt in field.options:
                        if saved_str.lower() == opt.lower() or saved_str.lower() in opt.lower():
                            return opt, False
                    decl = self._find_decline_or_privacy_option(field.options)
                    if decl and any(p in saved_str.lower() for p in ["prefer not", "decline"]):
                        return decl, False
                else:
                    if is_numeric_field:
                        m = re.search(r'(\d+(?:\.\d+)?)', saved_str)
                        if m:
                            saved_str = m.group(1)
                            return saved_str, False
                    else:
                        return saved_str, False

        val, needs_hitl = await self._resolve_internal(field, profile, job_description)

        # If resolved successfully, persist to memory so future job applications never start from empty
        if val is not None and str(val).strip() != "" and not needs_hitl:
            # Prevent overwriting an existing positive answer in memory with 0
            existing = self.memory_service.get_form_answer(field.label)
            if not (existing and str(existing).strip() not in ["0", ""] and str(val).strip() == "0"):
                ftype = field.field_type.value if hasattr(field.field_type, "value") else str(field.field_type)
                self.memory_service.save_form_answer(field.label, val, ftype)

        return val, needs_hitl

    async def _resolve_internal(
        self,
        field: FormField,
        profile: CandidateProfile,
        job_description: Optional[str] = None
    ) -> Tuple[str, bool]:
        label_lower = field.label.lower()
        help_txt = (getattr(field, "help_text", "") or "").lower()
        val_err = (getattr(field, "validation_error", "") or "").lower()
        combined_text = f"{label_lower} {help_txt} {val_err}".strip()

        # 0A. Notice Period / How soon can you join in days (e.g. "How soon can you join? (Please mention the number of days...)")
        if any(phrase in label_lower for phrase in [
            "how soon can you join", "how soon", "number of days", "mention the number of days",
            "notice period in days", "notice in days", "notice period (in days)", "joining in days"
        ]):
            days = profile.professional.notice_period_days  # 60
            if field.options:
                for opt in field.options:
                    opt_l = opt.lower()
                    if str(days) in opt_l or "60 days" in opt_l or "2 months" in opt_l:
                        return opt, False
                return field.options[0], False
            return str(days), False

        # 0B. Immediate Joiner / Availability: Candidate has 60 days notice! NEVER answer Yes.
        if any(phrase in label_lower for phrase in [
            "immediate joiner", "join immediately", "immediate joining",
            "can you join immediately", "available to join immediately",
            "available immediately", "are you an immediate joiner"
        ]):
            if field.options:
                for opt in field.options:
                    if opt.strip().lower() in ["no", "false"]:
                        return opt, False
                for opt in field.options:
                    if any(k in opt.lower() for k in ["60", "2 months", "standard", "serving notice", "more than 30"]):
                        return opt, False
                return field.options[-1], False
            if field.field_type == FormFieldType.NUMBER or any(k in label_lower for k in ["days", "number", "how many", "decimal"]):
                return str(profile.professional.notice_period_days), False
            return "No", False

        # 0C. Face-to-Face / In-Person Interviews & Rounds: Candidate is in Hyderabad and always ready to attend
        if any(phrase in label_lower for phrase in [
            "face to face", "f2f", "in-person", "in person", "offline round",
            "offline interview", "walk-in", "walk in", "come for round",
            "attend interview", "f2f round", "face to face round"
        ]):
            if field.options:
                yes_opt = self._find_option_matching(field.options, ["yes", "ready", "available", "agree", "willing"])
                if yes_opt:
                    return yes_opt, False
                return field.options[0], False
            return "Yes", False

        # 0D. REGULATORY: Bar admission, CPA, Medical/Nursing license, Professional Engineer (PE), Security clearance
        cat = self.classify_question(field)
        if cat == QuestionCategory.REGULATORY:
            all_creds = [c.lower() for c in (getattr(profile, "licenses", []) + getattr(profile, "certifications", []))]
            has_license = False
            for cred in all_creds:
                if cred and (cred in label_lower or any(w in label_lower for w in cred.split() if len(w) > 3)):
                    has_license = True
                    break
            if has_license:
                if field.options:
                    yes_opt = self._find_option_matching(field.options, ["yes", "true", "active", "hold license", "licensed"])
                    return yes_opt or field.options[0], False
                return "Yes", False
            else:
                # Unverified regulatory licensing requires human review
                if field.options:
                    no_opt = self._find_option_matching(field.options, ["no", "false", "do not hold", "not licensed"])
                    return no_opt or field.options[-1], True
                return "No", True

        # 1. Demographic & Voluntary EEO Self-Identification (Race, Ethnicity, Gender, Disability, Veteran)
        if any(w in label_lower for w in ["race", "ethnicity", "ethnic origin", "demographic"]):
            prof_race = getattr(profile.personal, "race_ethnicity", "Asian") or "Asian"
            if field.options:
                if prof_race and prof_race.lower() not in ["i prefer not to specify", "decline"]:
                    matched_race = self._find_option_matching(field.options, [prof_race.lower()])
                    if matched_race:
                        return matched_race, False
                decline_opt = self._find_decline_or_privacy_option(field.options)
                if decline_opt:
                    return decline_opt, False
                asian_opt = self._find_option_matching(field.options, ["asian"])
                if asian_opt:
                    return asian_opt, False
                return field.options[-1], False
            return prof_race, False

        if any(w in label_lower for w in ["gender", "sex", "sexual orientation"]) and not any(w in label_lower for w in ["gap", "pay"]):
            prof_gender = getattr(profile.personal, "gender", "Male") or "Male"
            if field.options:
                if prof_gender and prof_gender.lower() not in ["i prefer not to specify", "decline"]:
                    for opt in field.options:
                        if prof_gender.lower() == opt.lower() or prof_gender.lower() in opt.lower():
                            return opt, False
                decline_opt = self._find_decline_or_privacy_option(field.options)
                if decline_opt:
                    return decline_opt, False
                return field.options[-1], False
            return prof_gender, False

        if "disability" in label_lower or "handicap" in label_lower:
            if field.options:
                no_opt = self._find_option_matching(field.options, [
                    "no, i don't have a disability",
                    "no, i do not have a disability",
                    "i do not have a disability",
                    "not have a disability",
                    "do not have a disability",
                    "no"
                ])
                if no_opt:
                    return no_opt, False
                decline_opt = self._find_decline_or_privacy_option(field.options)
                if decline_opt:
                    return decline_opt, False
                return field.options[0], False
            return "No", False

        if "veteran" in label_lower:
            if field.options:
                not_vet_opt = self._find_option_matching(field.options, [
                    "not a protected veteran",
                    "not a veteran",
                    "i am not a protected veteran",
                    "i am not a veteran",
                    "no"
                ])
                if not_vet_opt:
                    return not_vet_opt, False
                decline_opt = self._find_decline_or_privacy_option(field.options)
                if decline_opt:
                    return decline_opt, False
                return field.options[0], False
            return "No", False

        # Check if sensitive and potentially ambiguous (e.g. security clearance, criminal history)
        if self._is_sensitive_ambiguous(label_lower):
            if field.options:
                decline_opt = self._find_decline_or_privacy_option(field.options)
                if decline_opt:
                    return decline_opt, False
            return "", True

        # 2. Contact / Personal fields
        if any(w in label_lower for w in ["first name", "given name"]):
            parts = profile.personal.full_name.split()
            return parts[0] if parts else "", False

        if any(w in label_lower for w in ["last name", "surname", "family name"]):
            parts = profile.personal.full_name.split()
            return " ".join(parts[1:]) if len(parts) > 1 else "", False

        if "full name" in label_lower or "your name" in label_lower:
            return profile.personal.full_name, False

        if "email" in label_lower:
            return profile.personal.email, False

        if "country code" in label_lower or "phone code" in label_lower:
            if field.options:
                for opt in field.options:
                    if "+91" in opt or "India" in opt:
                        return opt, False
            return "India (+91)", False

        if "phone" in label_lower or "mobile" in label_lower:
            digits = re.sub(r'\D', '', profile.personal.phone)
            if digits.startswith("91") and len(digits) == 12:
                digits = digits[2:]
            return digits, False

        # City / Current Location / Address (excluding interview questions, work mode questions, and Yes/No questions)
        is_location_q = (
            "city" in label_lower
            or "address" in label_lower
            or "current location" in label_lower
            or "your location" in label_lower
            or "residing in" in label_lower
            or "where are you based" in label_lower
            or (
                "location" in label_lower
                and not any(term in label_lower for term in [
                    "face to face", "f2f", "round", "interview", "ready to come", "come for",
                    "commute", "commuting", "relocate", "relocating", "onsite", "hybrid", "remote",
                    "are you", "do you", "can you", "will you", "would you", "is it ok", "comfortable"
                ])
            )
        )
        if is_location_q:
            if field.options:
                matched_loc = self._find_option_matching(field.options, ["hyderabad", "telangana", "india"])
                if matched_loc:
                    return matched_loc, False
                if any(opt.strip().lower() in ["yes", "no"] for opt in field.options):
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                    return field.options[0], False
            return profile.personal.location, False

        # 3. Professional & Company
        if "current company" in label_lower or "employer" in label_lower:
            return profile.professional.current_company, False

        if "current job title" in label_lower or "designation" in label_lower:
            return profile.professional.designation, False

        # 4. CTC & Compensation (Handles INR raw numbers, LPA/Lakhs, and salary expectations)
        if any(phrase in combined_text for phrase in [
            "expected ctc", "expected salary", "salary expectations", "expected compensation",
            "expected annual compensation", "desired compensation", "salary expectation"
        ]):
            exp_lpa = profile.professional.expected_lpa
            exp_inr = int(exp_lpa * 100000)
            if "month" in combined_text:
                return str(int(exp_inr / 12)), False

            # Prompt asking for CTC in LPA using one or two digits, e.g. 6 or 8
            if any(w in combined_text for w in ["one or two digits", "single digit", "e.g., 6 or 8", "e.g. 6 or 8", "e.g. 6", "e.g., 6"]):
                return str(int(round(exp_lpa))), False

            needs_inr = (
                any(w in combined_text for w in [
                    "in inr", "inr", "annual", "larger than 100", "350000", "200000", "rupees", "rupee", "whole number"
                ])
                or "larger than 100" in val_err
            )
            is_lakhs = (
                any(w in combined_text for w in ["lakh", "lakhs", "lpa", "lac"])
                and not needs_inr
            )
            if is_lakhs:
                return str(int(exp_lpa)) if exp_lpa.is_integer() else str(exp_lpa), False
            if needs_inr:
                return str(exp_inr), False
            return str(int(exp_lpa)) if exp_lpa.is_integer() else str(exp_lpa), False

        if any(phrase in combined_text for phrase in [
            "current ctc", "current salary", "current compensation", "current annual compensation"
        ]):
            cur_lpa = profile.professional.current_lpa
            prefs_inr = self.memory_service.get_preference("current_ctc_inr")
            cur_inr = int(prefs_inr) if prefs_inr else int(cur_lpa * 100000)
            if "month" in combined_text:
                return str(int(cur_inr / 12)), False

            # Prompt asking for CTC in LPA using one or two digits, e.g. 6 or 8
            if any(w in combined_text for w in ["one or two digits", "single digit", "e.g., 6 or 8", "e.g. 6 or 8", "e.g. 6", "e.g., 6"]):
                return str(int(round(cur_lpa))), False

            needs_inr = (
                any(w in combined_text for w in [
                    "in inr", "inr", "annual", "larger than 100", "200000", "350000", "rupees", "rupee", "whole number"
                ])
                or "larger than 100" in val_err
            )
            is_lakhs = (
                any(w in combined_text for w in ["lakh", "lakhs", "lpa", "lac"])
                and not needs_inr
            )
            if is_lakhs:
                return str(int(cur_lpa)) if cur_lpa.is_integer() else str(cur_lpa), False
            if needs_inr:
                return str(cur_inr), False
            return str(int(cur_lpa)) if cur_lpa.is_integer() else str(cur_lpa), False

        # 5. Portfolio / Website URL
        if "portfolio" in label_lower or "website" in label_lower or "github" in label_lower or "online profile" in label_lower:
            linkedin_url = getattr(profile.personal, "linkedin_url", None)
            if not linkedin_url:
                slug = profile.personal.full_name.lower().replace(" ", "-")
                linkedin_url = f"https://www.linkedin.com/in/{slug}"
            return "" if not field.is_required else linkedin_url, False

        # 6. Additional Months of Experience
        if any(phrase in label_lower for phrase in ["additional months", "remaining number of months", "months of experience"]):
            if field.options:
                for opt in field.options:
                    if opt.strip().startswith("0"):
                        return opt, False
                return field.options[0], False
            return "0", False

        # 7. Total Years of Professional / IT / QA Experience (Pure Decimal e.g. 3.9)
        is_total_exp = any(phrase in label_lower for phrase in [
            "total it experience", "it experience", "total it", "years of it experience",
            "total years of professional experience", "total number of years", "total years",
            "total work experience", "overall experience", "professional experience",
            "qa search experience", "qa experience", "software testing experience",
            "quality assurance experience", "experience (after graduation)",
            "relevant experience", "relevant work experience", "total experience",
            "years of experience", "experience in years", "experience (years)", "total yrs"
        ])
        # Make sure it's not asking for experience with a specific other tool (e.g. "total experience with python")
        if is_total_exp and not re.search(r'(?:with|in)\s+(?!it\b)[a-z]+', label_lower):
            exp = float(profile.professional.total_experience_years)
            if abs(exp - 3.9) < 0.2:
                exp = float(round(exp))
            exp_str = str(int(exp)) if exp.is_integer() else str(exp)
            if field.options:
                for opt in field.options:
                    if opt.strip().startswith(exp_str) or opt.strip().startswith(str(int(exp))):
                        return opt, False
                return field.options[0], False
            return exp_str, False

        # 7. Education / Degree questions (e.g. Bachelor's Degree)
        if any(w in label_lower for w in ["bachelor", "degree", "education", "graduation", "diploma", "b.tech", "btech"]):
            if field.options:
                for opt in field.options:
                    if any(w in opt.lower() for w in ["yes", "bachelor", "graduate", "b.tech", "btech"]):
                        return opt, False
                return field.options[0], False
            return "Bachelor's Degree", False

        # 8. Notice Period (Days, Weeks, Months)
        if any(phrase in combined_text for phrase in [
            "notice period", "notice in days", "notice (days)", "notice period in days",
            "notice period (in days)", "how soon can you join"
        ]) or ("notice" in combined_text and any(w in combined_text for w in ["days", "period", "joining", "example"])):
            days = profile.professional.notice_period_days # 60
            if field.options:
                # Find best matching option
                for opt in field.options:
                    opt_l = opt.lower()
                    if str(days) in opt_l or "60 days" in opt_l or "2 months" in opt_l or "8 weeks" in opt_l:
                        return opt, False
                    if "immediate" in opt_l and days <= 15:
                        return opt, False
                    if "1 month" in opt_l and days <= 30:
                        return opt, False
                    if "2 months" in opt_l and 31 <= days <= 60:
                        return opt, False
                return field.options[0], False

            if "week" in combined_text:
                return str(round(days / 7)), False # "8" or "9"
            if "month" in combined_text:
                return str(round(days / 30)), False # "2"
            return str(days), False # "60"

        # 8. Yes / No Questions (Dropdown or Radio: experience, skills, tools, availability, background)
        is_yes_no = False
        if field.options:
            opt_lower_set = {o.strip().lower() for o in field.options}
            if "yes" in opt_lower_set and "no" in opt_lower_set:
                is_yes_no = True
        elif "yes / no" in label_lower or "yes/no" in label_lower or "(yes/no)" in label_lower:
            is_yes_no = True

        if is_yes_no:
            # 1. Eligibility to work without sponsorship / Proof of identity / Right to work in India
            if any(kw in label_lower for kw in [
                "without sponsorship", "without requiring sponsorship", "proof of identity",
                "eligibility to work in india", "authorized to work in india", "legally authorized",
                "proof of identity and eligibility"
            ]):
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                return "Yes", False

            # 2. Minimum age requirement (e.g. at least 18 years of age)
            if any(kw in label_lower for kw in [
                "at least 18", "18 years of age", "18 years old", "age 18", "legal age", "are you 18"
            ]):
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                return "Yes", False

            # 3. Requisite education / certifications / qualifications for position
            if any(kw in label_lower for kw in [
                "requisite education", "required education", "requisite certification",
                "education and/or certification", "meet the qualifications", "required qualification",
                "requisite qualification", "certification for this position"
            ]):
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                return "Yes", False

            # 4. Background checks, drug screens, commuting, full-time
            if any(kw in label_lower for kw in [
                "background check", "drug screen", "drug test", "comfortable commuting",
                "full-time", "full time", "overtime", "willing to relocate"
            ]):
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                return "Yes", False

            # 5. Negative keywords (require visa sponsorship, criminal history, conflicts)
            negative_keywords = [
                "require visa", "visa sponsorship", "need sponsorship", "require sponsorship",
                "will you require", "felony", "criminal", "misdemeanor", "convicted", "terminated", "fired",
                "non-compete", "previously employed by", "relatives working"
            ]
            if any(kw in label_lower for kw in negative_keywords) and "without" not in label_lower:
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "no":
                            return opt, False
                return "No", False

            # 6. Profile skills, testing, tools, automation, and QA technical experience questions -> YES
            if any(term in label_lower for term in [
                "experience with", "experience in", "hands-on", "proficient in", "knowledge of",
                "worked with", "familiar with", "testing", "automation", "qa",
                "tool", "framework", "agile", "scrum", "software"
            ]) or any(s.lower() in label_lower for s in profile.skills):
                if field.options:
                    for opt in field.options:
                        if opt.strip().lower() == "yes":
                            return opt, False
                return "Yes", False

            # Any other unrecognized Yes/No question: flag for HITL with Yes as suggested default
            if field.options:
                for opt in field.options:
                    if opt.strip().lower() == "yes":
                        return opt, True
                return field.options[0], True
            return "Yes", True

        # 9. Skill Experience Questions: "How many years of work experience do you have with [Skill]?"
        skill_target = None
        m1 = re.search(r'(?:experience (?:do you have )?(?:with|in|using)|how many years .*?(?:with|in|using))\s+([^?*:]+)', label_lower)
        if m1:
            skill_target = m1.group(1)
        else:
            m2 = re.search(r'how many years of\s+([^?*:]+?)\s+(?:work\s+|hands-on\s+|professional\s+)?experience', label_lower)
            if m2:
                skill_target = m2.group(1)
            else:
                cleaned_label_test = re.sub(r'[\?\*\:\(\)]', '', label_lower).strip()
                if self._is_known_skill(cleaned_label_test, profile):
                    skill_target = cleaned_label_test

        if skill_target:
            skill_target = re.sub(r'[\?\*\:\(\)]', '', skill_target).strip()
            total_exp = float(profile.professional.total_experience_years)
            if any(t in skill_target for t in ["total it", "it experience", "total experience", "overall experience"]):
                return (str(int(total_exp)) if total_exp.is_integer() else str(total_exp)), False

            years: Optional[float] = None
            # 1. Ask AI if LLM is enabled
            if self.llm_service.can_use_llm():
                try:
                    context = {
                        "full_name": profile.personal.full_name,
                        "headline": getattr(profile.personal, "headline", ""),
                        "skills": profile.skills,
                        "total_experience_years": total_exp,
                        "current_company": profile.professional.current_company,
                        "current_role": getattr(profile.professional, "current_role", ""),
                        "resume_text": self.resume_text[:4000] if self.resume_text else "",
                    }
                    ai_years = await self.llm_service.evaluate_skill_experience(
                        skill=skill_target,
                        candidate_context=context,
                        total_experience_years=total_exp,
                        job_description=job_description
                    )
                    if ai_years >= 0.0:
                        years = ai_years
                        logger.info(f"AI evaluated experience for '{skill_target}': {years} years (out of {total_exp})")
                except Exception as e:
                    logger.warning(f"AI skill experience evaluation failed ({e}), falling back to heuristic.")

            # 2. Offline / Heuristic fallback
            if years is None:
                years = self._match_skill_experience(skill_target, profile)
                logger.info(f"Heuristic matched experience for '{skill_target}': {years} years")

            years_str = str(int(years)) if years.is_integer() else str(years)
            if field.options:
                for opt in field.options:
                    opt_clean = opt.strip()
                    if opt_clean == years_str or opt_clean == str(int(years)):
                        return opt, False
                for opt in field.options:
                    opt_clean = opt.strip()
                    if opt_clean.startswith(years_str) or opt_clean.startswith(str(int(years))):
                        return opt, False
                # Range matching e.g. "1-2 years", "3-5 years", "4+ years"
                for opt in field.options:
                    m_rng = re.search(r'(\d+)\s*(?:-|to)\s*(\d+)', opt)
                    if m_rng:
                        low, high = float(m_rng.group(1)), float(m_rng.group(2))
                        if low <= years <= high:
                            return opt, False
                    m_plus = re.search(r'(\d+)\s*\+', opt)
                    if m_plus:
                        low = float(m_plus.group(1))
                        if years >= low:
                            return opt, False
                if years == 0:
                    for opt in field.options:
                        if any(no_w in opt.lower() for no_w in ["none", "no experience", "0"]):
                            return opt, False
                return field.options[0], False
            return years_str, False

        # 10. Work authorization & Sponsorship
        if "authorized to work" in label_lower or "legally authorized" in label_lower:
            if field.options:
                for opt in field.options:
                    if "yes" in opt.lower():
                        return opt, False
            return "Yes", False

        if "sponsorship" in label_lower or "require visa" in label_lower:
            if field.options:
                for opt in field.options:
                    if "no" in opt.lower():
                        return opt, False
            return "No", False

        # Specific Employer / Domain / Technology Questions
        # 1. Product-based companies worked for
        if any(w in label_lower for w in ["product-based", "product based", "product companies"]):
            val = "Enterprise Software / SaaS Product Company"
            if field.options:
                matched = self._find_option_matching(field.options, ["enterprise software", "saas", "product", "enterprise"])
                if matched:
                    return matched, False
                return field.options[0], False
            return val, False

        # 2. Automation testing for iPaaS, SaaS, or cloud-based platforms
        if any(w in label_lower for w in ["ipaas", "saas", "cloud-based", "cloud based"]):
            if field.options:
                for opt in field.options:
                    if opt.strip().lower() == "yes":
                        return opt, False
                return field.options[0], False
            return "Yes", False

        # 3. API Testing – Postman/Swagger/etc.
        if "api testing" in label_lower and any(w in label_lower for w in ["postman", "swagger", "level", "proficiency", "etc"]):
            val = "Advanced"
            if field.options:
                matched = self._find_option_matching(field.options, ["advanced", "expert", "intermediate"])
                if matched:
                    return matched, False
                return field.options[0], False
            return val, False

        # 4. Frameworks worked with
        if any(w in label_lower for w in ["frameworks worked with", "frameworks used", "test frameworks", "automation frameworks"]):
            val = "Selenium"
            if field.options:
                matched = self._find_option_matching(field.options, ["selenium", "hybrid", "testng"])
                if matched:
                    return matched, False
                return field.options[0], False
            return val, False

        # 5. Python Experience
        if "python experience" in label_lower or ("python" in label_lower and any(w in label_lower for w in ["level", "proficiency", "knowledge"])):
            val = "Beginner"
            if field.options:
                matched = self._find_option_matching(field.options, ["beginner", "basic", "intermediate", "1-2 years", "1 year"])
                if matched:
                    return matched, False
                return field.options[0], False
            if field.field_type == FormFieldType.NUMBER or "how many" in label_lower or "years" in label_lower:
                return "1", False
            return val, False

        # 6. PyScript experience
        if "pyscript" in label_lower:
            val = "No experience"
            if field.options:
                matched = self._find_option_matching(field.options, ["no experience", "none", "0", "no"])
                if matched:
                    return matched, False
                return field.options[0], False
            if field.field_type == FormFieldType.NUMBER or "how many" in label_lower or "years" in label_lower:
                return "0", False
            return val, False

        # 7. Backend applications/services hosted on AWS
        if any(w in label_lower for w in ["hosted on aws", "services hosted on aws", "backend applications on aws", "backend applications/services hosted on aws"]):
            val = "No experience"
            if field.options:
                matched = self._find_option_matching(field.options, ["no experience", "none", "0", "no"])
                if matched:
                    return matched, False
                return field.options[0], False
            if field.field_type == FormFieldType.NUMBER or "how many" in label_lower or "years" in label_lower:
                return "0", False
            return val, False

        # 8. Language proficiency (English, etc.)
        if any(w in label_lower for w in ["english", "language"]) and any(w in label_lower for w in ["proficiency", "level", "speak", "fluent", "skill"]):
            if field.options:
                matched = self._find_option_matching(field.options, [
                    "professional", "native or bilingual", "conversational", "fluent", "advanced", "proficient", "yes"
                ])
                if matched:
                    return matched, False
                return field.options[-1], False
            return "Professional", False

        # 9. Multi-choice Experience Level Questions (e.g. Smart Working / consultancy questions)
        # "What level of professional experience do you have developing, executing and maintaining automated test suites..."
        # Options: "Limited exposure", "Practical use", "Extensive use"
        if any(w in label_lower for w in ["level of professional experience", "level of experience", "what level of"]) and field.options:
            is_comm = any(w in label_lower for w in [
                "stakeholder", "external client", "communicated testing progress",
                "communicating testing progress", "release readiness", "clients"
            ])
            if is_comm:
                zero_opt = self._find_option_matching(field.options, ["0", "0 value", "none", "no experience", "0 - none", "no", "limited exposure"])
                if zero_opt:
                    return zero_opt, False
                return field.options[0], False

            is_core_qa = any(w in label_lower for w in [
                "automated test", "test automation", "testing", "test strategies", "test plans",
                "git", "version control", "api", "apis", "defect", "debugging",
                "software", "quality", "ci/cd", "pipeline"
            ])
            if is_core_qa:
                # Candidate has 4 years of solid QA automation experience
                matched = self._find_option_matching(field.options, ["extensive use", "practical use", "advanced", "expert", "intermediate"])
                if matched:
                    return matched, False

        # Free-text questions, numbers, or Dropdown / Radio options requiring reasoning
        if field.options or (self.llm_service.can_use_llm() and field.field_type in [FormFieldType.TEXT, FormFieldType.NUMBER]):
            context = {
                "full_name": profile.personal.full_name,
                "location": profile.personal.location,
                "total_experience_years": profile.professional.total_experience_years,
                "current_company": profile.professional.current_company,
                "current_lpa": profile.professional.current_lpa,
                "expected_lpa": profile.professional.expected_lpa,
                "notice_period_days": profile.professional.notice_period_days,
                "skills": profile.skills,
                "preferred_locations": profile.preferred_locations,
                "resume_text": self.resume_text[:3500] if self.resume_text else "",
            }
            chosen = await self.llm_service.answer_form_question(
                question_text=field.label,
                options=field.options,
                candidate_context=context,
                job_description=job_description
            )
            if chosen:
                chosen = re.sub(r'^(?:answer|response|selected option):\s*', '', chosen, flags=re.IGNORECASE).strip()
                if field.options:
                    # Match exact option if possible
                    for opt in field.options:
                        if opt.strip().lower() == chosen.lower() or opt.strip().lower() in chosen.lower() or chosen.lower() in opt.strip().lower():
                            return opt, False
                    return chosen, False

                is_num = (
                    field.field_type == FormFieldType.NUMBER or
                    any(k in label_lower for k in [
                        "year", "experience", "how many", "ctc", "salary", "notice",
                        "days", "months", "lakh", "lpa", "total it"
                    ])
                )
                if is_num:
                    m = re.search(r'(\d+(?:\.\d+)?)', chosen)
                    if m:
                        chosen = m.group(1)
                return chosen, False

        # Fallback for unrecognized number/experience input: flag for HITL
        if field.field_type == FormFieldType.NUMBER or "how many" in label_lower or "years" in label_lower:
            return "0", True

        # Unrecognized dropdown / radio options: flag for HITL with top option suggested
        if field.options:
            return field.options[0], True

        # Any completely unknown free-text field: flag for HITL
        return "", True

    def _match_skill_experience(self, skill_target: str, profile: CandidateProfile) -> float:
        """
        Calculates nuanced experience for a specific skill from candidate profile and resume.
        Department-agnostic: Works across Tech, QA, Finance, Marketing, HR, Operations, Healthcare, etc.
        Differentiates primary core skills (full experience), secondary skills (proportional exp),
        and unpossessed/unlisted skills (0.0 yrs).
        Strictly prevents claiming any excluded skill.
        """
        skill_clean = re.sub(r'[\?\*\:\(\)]', '', skill_target).lower().strip()
        total_exp = float(profile.professional.total_experience_years or profile.experience_years or 0.0)

        # 0. Check excluded skills first: strictly return 0.0
        if hasattr(profile, "is_excluded_skill") and profile.is_excluded_skill(skill_clean):
            return 0.0
        for ex in getattr(profile, "excluded_skills", []) or []:
            ex_l = ex.lower().strip()
            if ex_l and (ex_l == skill_clean or f" {ex_l} " in f" {skill_clean} " or f" {skill_clean} " in f" {ex_l} "):
                return 0.0

        # 1. Check structured experience history if present:
        if getattr(profile, "experience_history", None):
            matching_years = 0.0
            found_role = False
            for exp_item in profile.experience_history:
                skills_in_role = [s.lower() for s in exp_item.skills_used]
                desc_lower = (exp_item.description or "").lower()
                title_lower = (exp_item.title or "").lower()
                if any(skill_clean in s or s in skill_clean for s in skills_in_role) or \
                   (re.search(rf"\b{re.escape(skill_clean)}\b", desc_lower) or skill_clean in title_lower):
                    found_role = True
                    matching_years += (exp_item.years or 1.0)
            if found_role and matching_years > 0:
                return min(float(round(matching_years, 1)), total_exp)

        # 2. Check Candidate's Primary Core Skills across any domain:
        cand_skills_clean = [s.lower().strip() for s in profile.skills]
        cand_role = (profile.professional.designation or profile.current_role or "").lower()
        cand_target_roles = [r.lower() for r in (getattr(profile, "target_roles", None) or profile.preferred_roles or [])]

        # Top skills in candidate's inventory
        top_skills = set(cand_skills_clean[:6]) if cand_skills_clean else set()

        # 2. Candidate's primary core stack across disciplines:
        core_primary_indicators = [
            "selenium", "selenium webdriver", "core java", "java", "testng",
            "qa automation", "automation testing", "test automation", "software testing",
            "functional testing", "manual testing", "web testing", "regression testing",
            "test execution", "test planning", "bug tracking",
            "financial modeling", "dcf", "valuation", "seo", "sem", "talent acquisition"
        ]

        # Explicit primary matches get full experience:
        if (any(term == skill_clean or term in skill_clean for term in core_primary_indicators) and
            any(term in s for term in core_primary_indicators for s in cand_skills_clean[:6])) or \
           (cand_role and (skill_clean in cand_role or (len(skill_clean) >= 4 and cand_role in skill_clean))):
            return float(round(total_exp)) if abs(total_exp - 3.9) < 0.2 else total_exp

        # 3. Secondary Tools and Frameworks across domains
        # Secondary Programming / Scripting Languages & Automation Frameworks:
        if any(term in skill_clean for term in ["javascript", "typescript", "playwright", "cypress"]):
            has_js = any(any(k in s for k in ["javascript", "typescript", "playwright", "cypress"]) for s in cand_skills_clean)
            if has_js or (self.resume_text and re.search(r'\b(javascript|playwright|typescript|cypress)\b', self.resume_text, re.I)):
                return min(2.0, total_exp) if total_exp > 0 else 0.0
            return 0.0

        # API Testing
        if any(term in skill_clean for term in ["rest assured", "restassured", "rest-assured", "api testing", "postman", "soapui", "web services"]):
            has_api = any(any(k in s for k in ["rest assured", "restassured", "api testing", "postman"]) for s in cand_skills_clean)
            if has_api or (self.resume_text and re.search(r'\b(rest\s*assured|postman|api\s*testing)\b', self.resume_text, re.I)):
                return min(2.5, total_exp) if total_exp > 0 else 0.0

        # Database / SQL
        if any(term in skill_clean for term in ["sql", "mysql", "database", "rdbms", "queries"]):
            has_sql = any("sql" in s for s in cand_skills_clean)
            if has_sql or (self.resume_text and re.search(r'\b(sql|mysql|database)\b', self.resume_text, re.I)):
                return min(2.0, total_exp) if total_exp > 0 else 0.0

        # Version Control & Agile
        if any(term in skill_clean for term in ["git", "github", "gitlab", "jira", "agile", "scrum"]):
            return min(3.0, total_exp) if total_exp > 0 else 0.0

        # CI/CD & Build
        if any(term in skill_clean for term in ["jenkins", "ci/cd", "maven", "pipeline"]):
            has_build = any(any(k in s for k in ["jenkins", "ci/cd", "maven"]) for s in cand_skills_clean)
            if has_build or (self.resume_text and re.search(r'\b(jenkins|maven|ci/cd)\b', self.resume_text, re.I)):
                return min(2.0, total_exp) if total_exp > 0 else 0.0

        # Cloud & Containers
        if any(term in skill_clean for term in ["docker", "kubernetes", "aws", "azure", "gcp", "cloud", "linux"]):
            has_devops = any(any(k in s for k in ["docker", "kubernetes", "aws", "azure", "linux"]) for s in cand_skills_clean)
            if has_devops or (self.resume_text and re.search(r'\b(docker|kubernetes|aws|linux)\b', self.resume_text, re.I)):
                return min(1.0, total_exp) if total_exp > 0 else 0.0
            return 0.0

        # Finance Tools
        if any(term in skill_clean for term in ["excel", "spreadsheets", "vba"]):
            has_excel = any("excel" in s for s in cand_skills_clean)
            if has_excel or (self.resume_text and re.search(r'\b(excel|spreadsheets)\b', self.resume_text, re.I)):
                return min(3.0, total_exp) if total_exp > 0 else 0.0

        if any(term in skill_clean for term in ["quickbooks", "xero", "bookkeeping"]):
            has_qb = any(any(k in s for k in ["quickbooks", "xero", "bookkeeping"]) for s in cand_skills_clean)
            if has_qb or (self.resume_text and re.search(r'\b(quickbooks|xero)\b', self.resume_text, re.I)):
                return min(2.0, total_exp) if total_exp > 0 else 0.0

        # Marketing Tools
        if any(term in skill_clean for term in ["google analytics", "ga4", "google ads", "hubspot"]):
            has_mkt = any(any(k in s for k in ["google analytics", "ga4", "google ads", "hubspot"]) for s in cand_skills_clean)
            if has_mkt or (self.resume_text and re.search(r'\b(google\s*analytics|hubspot|google\s*ads)\b', self.resume_text, re.I)):
                return min(2.0, total_exp) if total_exp > 0 else 0.0

        # 4. Exact or alias match against candidate's profile skills list:
        for cand_skill in profile.skills:
            cs_clean = cand_skill.lower().strip()
            if cs_clean == skill_clean or cs_clean in skill_clean or skill_clean in cs_clean:
                return min(2.0, total_exp)

        # 5. Direct keyword match in candidate's uploaded resume text:
        if self.resume_text:
            resume_lower = self.resume_text.lower()
            if len(skill_clean) >= 3 and re.search(rf"\b{re.escape(skill_clean)}\b", resume_lower):
                return min(1.5, total_exp)

        # 6. Unmatched / unpossessed skill:
        return 0.0

    def _find_option_matching(self, options: List[str], patterns: List[str]) -> Optional[str]:
        """Finds first option containing any pattern as a substring (case-insensitive)."""
        if not options:
            return None
        for pat in patterns:
            pat_l = pat.lower()
            for opt in options:
                if pat_l in opt.lower():
                    return opt
        return None

    def _find_decline_or_privacy_option(self, options: List[str]) -> Optional[str]:
        """Finds standard EEO decline or privacy option."""
        decline_phrases = [
            "prefer not to specify",
            "prefer not to say",
            "prefer not to answer",
            "prefer not to disclose",
            "decline to self-identify",
            "decline to state",
            "decline to specify",
            "decline to answer",
            "do not wish to specify",
            "do not wish to answer",
            "don't wish to answer",
            "choose not to disclose",
            "choose not to specify",
            "not disclosed",
            "prefer not",
            "decline",
        ]
        return self._find_option_matching(options, decline_phrases)

    def _is_known_skill(self, text: str, profile: CandidateProfile) -> bool:
        """Determines if a string corresponds directly to a technical skill."""
        t_clean = text.lower().strip()
        if len(t_clean) < 2 or len(t_clean) > 40:
            return False
        if any(w in t_clean for w in ["name", "email", "phone", "city", "salary", "ctc", "company", "notice", "gender", "race"]):
            return False
        for s in profile.skills:
            if t_clean == s.lower() or t_clean in s.lower() or s.lower() in t_clean:
                return True
        common_skills = [
            "selenium", "core java", "java", "rest assured", "restassured", "playwright",
            "cypress", "testng", "cucumber", "junit", "postman", "sql", "jenkins",
            "git", "maven", "docker", "python", "javascript", "typescript", "api testing"
        ]
        return any(cs == t_clean or cs in t_clean for cs in common_skills)

    def _is_sensitive_ambiguous(self, label: str) -> bool:
        for pat in SENSITIVE_PATTERNS:
            if re.search(pat, label):
                return True
        return False
