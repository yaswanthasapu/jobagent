import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from models.evaluation import (
    DecisionType,
    ExtractedRequirements,
    JobEvaluationResult,
    MatchBreakdown,
)
from models.job import JobDetails
from models.profile import CandidateProfile
from services.llm_service import LLMService

logger = logging.getLogger(__name__)

# Semantic concept clusters: Maps generic job requirements to qualifying skill sets
# Programming languages and distinct automation frameworks are NOT grouped together.
CONCEPT_CLUSTERS: Dict[str, Set[str]] = {
    "api_testing": {
        "restassured", "rest-assured", "rest assured", "postman", "rest api", "restful api",
        "soap", "soapui", "api testing", "api automation", "web services", "microservices", "swagger", "charles"
    },
    "web_automation": {
        "selenium", "selenium webdriver", "webdriver", "playwright",
        "ui automation", "web automation", "browser automation", "test automation"
    },
    "mobile_automation": {
        "appium", "mobile testing", "mobile automation", "espresso", "xcuitest"
    },
    "ci_cd": {
        "jenkins", "git", "github", "gitlab", "ci/cd", "continuous integration", "pipelines", "github actions"
    },
    "bdd": {
        "cucumber", "bdd", "behavior driven development", "gherkin"
    },
    "database": {
        "sql", "mysql", "postgresql", "oracle", "database testing", "database querying", "rdbms"
    },
    "performance": {
        "jmeter", "loadrunner", "gatling", "performance testing", "load testing"
    }
}

# Direct tool and language aliases (synonyms within the SAME technology)
TOOL_ALIASES: Dict[str, Set[str]] = {
    "selenium": {"selenium", "selenium webdriver", "webdriver", "selenium grid"},
    "java": {"java", "core java", "j2se", "java 8", "java 11", "java 17", "java8", "java11"},
    "python": {"python", "python3", "pytest", "pyunit", "robot framework"},
    "c#": {"c#", ".net", "csharp", "dotnet", "asp.net", "specflow", "nunit"},
    "playwright": {"playwright"},
    "cypress": {"cypress", "cypress.io"},
    "restassured": {"restassured", "rest-assured", "rest assured", "rest api testing", "api testing"},
    "postman": {"postman", "api testing"},
    "cucumber": {"cucumber", "cucumber bdd", "bdd"},
    "testng": {"testng", "test ng", "junit", "junit5", "junit4"},
    "sql": {"sql", "mysql", "postgresql", "oracle", "pl/sql", "database testing"},
    "jenkins": {"jenkins", "ci/cd", "continuous integration"},
    "git": {"git", "github", "gitlab", "version control"},
    "uipath": {"uipath", "rpa", "robotic process automation"}
}

class JobEvaluator:
    """
    Autonomous Job Decision Engine:
    Evaluates jobs using a multi-factor weighted scoring architecture:
      - Technical Skills Fit (50%)
      - Experience Fit (20%)
      - Role Relevance (15%)
      - Preferences: Location, Work Mode, Notice Period, Salary (10%)
      - Application Risk (5%)
    Applies hard skip rules, smart notice/salary/location reasoning, and produces explainable decisions.
    """

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

    async def evaluate_fit(
        self,
        job: JobDetails,
        profile: CandidateProfile
    ) -> JobEvaluationResult:
        """
        Execute the autonomous decision pipeline:
        JOB DATA -> UNDERSTAND -> COMPARE -> RISK ANALYSIS -> DECISION -> ACTION
        """
        # 1. Extract requirements via LLM or NLP
        reqs: ExtractedRequirements = await self.llm_service.extract_job_requirements(
            job_title=job.title,
            job_description=job.description_text
        )

        candidate_exp = float(profile.professional.total_experience_years) # 3.9
        candidate_skills = [s.lower().strip() for s in profile.skills]
        excluded_skills = [s.lower().strip() for s in getattr(profile, "excluded_skills", [])]

        # 2. Check Incompatible Stack Layer & Discipline Mismatch (Hard Skip Rules)
        is_incomp_stack, incomp_stack_reason, incomp_skills = self._check_incompatible_stack(
            job_title=job.title,
            job_description=job.description_text,
            candidate_skills=candidate_skills,
            excluded_skills=excluded_skills,
            reqs=reqs
        )

        # 3. Compute Role Relevance Score (15% weight)
        target_roles = getattr(profile, "target_roles", None) or profile.preferred_roles or [
            "QA Automation Engineer", "SDET", "Software QA", "Automation Tester", "Quality Assurance Engineer"
        ]
        role_score = self._compute_role_score(
            job_title=job.title,
            preferred_roles=target_roles
        )
        is_discipline_mismatch = (role_score <= 25.0)
        role_reason = "Unrelated role discipline"

        # 4. Compute Experience Fit Score (20% weight)
        exp_score, exp_analysis, is_exp_hard_reject = self._compute_experience_score(
            candidate_exp=candidate_exp,
            min_exp_req=reqs.min_experience_years,
            max_exp_req=reqs.max_experience_years,
            target_level=reqs.target_role_level,
            job_title=job.title
        )

        # 5. Compute Technical Skill Fit Score (50% weight)
        tech_score, matched_skills, missing_skills, mandatory_missing = self._compute_technical_score(
            candidate_skills=candidate_skills,
            excluded_skills=excluded_skills,
            required_skills=reqs.required_skills,
            preferred_skills=reqs.preferred_skills,
            job_title=job.title,
            job_description=job.description_text
        )

        # 6. Compute Job Preferences Fit Score (10% weight)
        # Location, Work Mode, Notice Period, Salary
        pref_score, pref_reasons, pref_review_triggers, pref_hard_skip = self._compute_preferences_score(
            job=job,
            reqs=reqs,
            profile=profile
        )

        # 7. Compute Application Risk Score (5% weight, lower is better)
        risk_score, risk_factors = self._compute_risk_score(
            job=job,
            reqs=reqs,
            profile=profile,
            mandatory_missing=mandatory_missing,
            is_exp_gap=(reqs.min_experience_years > candidate_exp + 1.5)
        )

        # 8. Calculate Multi-Factor Weighted Overall Score
        # Formula: 50% Tech + 20% Exp + 15% Role + 10% Pref + 5% Safety (100 - risk)
        overall_score = round(
            (tech_score * 0.50) +
            (exp_score * 0.20) +
            (role_score * 0.15) +
            (pref_score * 0.10) +
            ((100.0 - risk_score) * 0.05),
            1
        )

        min_score = profile.job_preferences.minimum_match_score or 70.0

        # 9. Autonomous Decision Logic
        decision: DecisionType
        reasoning: str
        confidence: float = 90.0

        # Hard Rule 1: Incompatible primary technology stack
        if is_incomp_stack:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 35.0)
            reasoning = f"Incompatible technology stack: {incomp_stack_reason}. Skipping to next opportunity."

        # Hard Rule 2: Unrelated discipline mismatch
        elif is_discipline_mismatch:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 25.0)
            reasoning = f"Role discipline mismatch: '{job.title}' does not align with candidate target roles ({role_reason})."

        # Hard Rule 3: Experience requirement >= 7.0 years (candidate has 3.9 years)
        elif is_exp_hard_reject:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 30.0)
            reasoning = f"Hard rejection on experience requirement: {exp_analysis}"

        # Hard Rule 4: Mandatory preference failure (e.g. salary < current CTC or mandatory non-preferred on-site relocation)
        elif pref_hard_skip:
            decision = DecisionType.SKIP
            confidence = 90.0
            overall_score = min(overall_score, 40.0)
            reasoning = f"Job preference mismatch: {pref_hard_skip}."

        # Hard Rule 5: Zero mandatory skills matched or severe mandatory core gap
        elif reqs.required_skills and (len(matched_skills) == 0 or (len(mandatory_missing) >= 2 and tech_score < 40.0)):
            decision = DecisionType.SKIP
            confidence = 90.0
            overall_score = min(overall_score, 40.0)
            reasoning = (
                f"Missing core mandatory skills: {', '.join(mandatory_missing or missing_skills[:4])}. "
                f"Candidate matches {len(matched_skills)}/{len(reqs.required_skills)} required skills ({tech_score:.1f}%)."
            )

        # Hard Rule 6: Score below minimum threshold (< 70)
        elif overall_score < min_score:
            decision = DecisionType.SKIP
            confidence = 85.0
            reasoning = (
                f"Overall match score ({overall_score:.1f}%) is below minimum threshold ({min_score}%). "
                f"Gaps: {', '.join(missing_skills[:3]) if missing_skills else 'insufficient alignment'}."
            )

        # Review Rule: Score is good (>= 70), but contains review triggers
        # (e.g. 30-day notice period gap, immediate joiner requirement, unknown relocation, borderline salary)
        elif pref_review_triggers:
            decision = DecisionType.REVIEW
            confidence = 80.0
            trigger_summary = "; ".join(pref_review_triggers)
            reasoning = (
                f"Potential match ({overall_score:.1f}%) requires user review: {trigger_summary}. "
                f"Skills ({tech_score:.1f}%), Experience ({exp_score:.1f}%), Role ({role_score:.1f}%)."
            )

        elif len(mandatory_missing) == 1 and tech_score < 75.0:
            decision = DecisionType.REVIEW
            confidence = 80.0
            reasoning = (
                f"Candidate matches {len(matched_skills)} skills, but is missing mandatory skill '{mandatory_missing[0]}'. "
                f"Score: {overall_score:.1f}%. Flagged for human review."
            )

        # Default: Strong, validated match
        else:
            decision = DecisionType.APPLY
            confidence = 95.0
            matched_preview = ", ".join(matched_skills[:4]) if matched_skills else "Core QA automation"
            reasoning = (
                f"Strong match ({overall_score:.1f}% >= {min_score}%). "
                f"Matched skills: {matched_preview}. {exp_analysis} "
                f"Preferences: {', '.join(pref_reasons)}."
            )

        breakdown = MatchBreakdown(
            technical_fit=tech_score,
            experience_fit=exp_score,
            role_fit=role_score,
            preference_fit=pref_score,
            risk_score=risk_score,
            overall_score=overall_score,
            confidence=confidence,
            skill_score=tech_score,
            experience_score=exp_score,
            role_score=role_score,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            mandatory_missing_skills=mandatory_missing,
            incompatible_skills=incomp_skills,
            experience_analysis=exp_analysis,
            reasoning=reasoning,
        )

        return JobEvaluationResult(
            job_id=job.job_id,
            job_title=job.title,
            company=job.company,
            decision=decision,
            match_breakdown=breakdown,
            extracted_requirements=reqs,
        )

    def _check_incompatible_stack(
        self,
        job_title: str,
        job_description: str,
        candidate_skills: List[str],
        excluded_skills: List[str],
        reqs: ExtractedRequirements
    ) -> Tuple[bool, str, List[str]]:
        """
        Determines if a job mandatorily requires a technology stack that candidate excludes
        or lacks, while accounting for optional / nice-to-have mentions.
        """
        title_l = job_title.lower()
        jd_l = job_description.lower()
        incomp_found: List[str] = []

        # Check if Python is mentioned as optional / secondary
        has_java = any("java" in s for s in candidate_skills)
        python_optional = False
        if re.search(r'(?:java\s*(?:or|/)\s*python|python\s*(?:or|/)\s*java)', jd_l) or \
           re.search(r'(?:good to have|nice to have|preferred|optional|bonus|plus|familiarity with).*?python', jd_l, re.DOTALL):
            python_optional = True

        # 1. Python Automation:
        # If title demands Python (e.g. "Python Automation Engineer", "SDET - Python")
        if re.search(r'\bpython\b', title_l):
            incomp_found.append("Python")
            return True, "Job title specifically demands Python automation", incomp_found

        # If JD demands Python as primary language and NOT optional
        if not python_optional:
            if re.search(r'\b(strong|expert|hands[- ]on|proficient|minimum \d\+? years?)\s+.*?\bpython\b', jd_l) and \
               not any(k in jd_l for k in ["java", "playwright", "selenium"]):
                incomp_found.append("Python")
                return True, "Job explicitly demands Python as primary programming language", incomp_found

            if "python" in [s.lower() for s in reqs.required_skills] and not has_java and not any(k in reqs.required_skills for k in ["Java", "Selenium"]):
                incomp_found.append("Python")
                return True, "Job strictly requires Python automation", incomp_found

        # 2. C# / .NET / SpecFlow
        csharp_optional = bool(re.search(r'(?:good to have|nice to have|preferred|optional|bonus|plus).*?(?:c#|\.net|specflow)', jd_l))
        if not csharp_optional:
            if re.search(r'(?<!\w)(c#|\.net|csharp|dotnet|specflow)(?!\w)', title_l):
                incomp_found.append("C#/.NET")
                return True, "Job title specifically demands C# / .NET automation", incomp_found
            if re.search(r'(?<!\w)(c#|\.net|csharp|dotnet|specflow)(?!\w)', jd_l) and not any(k in jd_l for k in ["java", "playwright", "selenium"]):
                incomp_found.append("C#/.NET")
                return True, "Job explicitly demands C# / .NET as primary automation stack", incomp_found

        # 3. Ruby / PHP / Golang
        for lang in ["ruby", "php", "golang"]:
            if re.search(rf'\b{lang}\b', title_l):
                incomp_found.append(lang.title())
                return True, f"Job title specifically demands {lang.title()} programming", incomp_found

        # 4. Salesforce / Apex / CRM
        if re.search(r'\b(salesforce|apex|soql|crm developer|sap erp)\b', title_l):
            incomp_found.append("Salesforce")
            return True, "Job title demands Salesforce/CRM specialization", incomp_found

        return False, "", []

    def _compute_experience_score(
        self,
        candidate_exp: float,
        min_exp_req: float,
        max_exp_req: Optional[float],
        target_level: str,
        job_title: str
    ) -> Tuple[float, str, bool]:
        """
        Evaluate experience fit for candidate with 3.9 years experience:
        - 0-4 years: 100.0 (Candidate meets or slightly under 4.0, which is virtually 4)
        - 4-6 years: 85.0 - 90.0 (Reasonable fit)
        - >= 7.0 years or Lead/Principal with >= 6.0 years: 15.0 (Hard Skip)
        """
        title_lower = job_title.lower()
        is_senior_title = any(w in title_lower for w in ["lead", "principal", "director", "architect", "manager", "head"])

        # Hard rejection threshold: >= 7 years required or senior title requiring >= 6 years
        if min_exp_req >= 7.0 or (is_senior_title and min_exp_req >= 6.0):
            return (
                15.0,
                f"Requires {min_exp_req}+ years (Candidate has {candidate_exp} years) - Exceeds upper threshold.",
                True
            )

        # Ideal fit: 0 to 4.0 years (3.9 years is essentially 4 years)
        if min_exp_req <= 4.0:
            return (
                100.0,
                f"Candidate experience ({candidate_exp} years) aligns well with required {min_exp_req} years.",
                False
            )

        # 4.1 to 6.0 years: reasonable gap
        if min_exp_req <= 6.0:
            # 5 years -> 85.0, 6 years -> 75.0
            deficit = min_exp_req - candidate_exp
            score = max(70.0, round(100.0 - (deficit * 15.0), 1))
            return (
                score,
                f"Candidate has {candidate_exp} years; role requires {min_exp_req} years (manageable gap).",
                False
            )

        # 6.1 to 6.9 years: marginal
        return (
            50.0,
            f"Candidate has {candidate_exp} years; role requires {min_exp_req} years.",
            False
        )

    def _compute_technical_score(
        self,
        candidate_skills: List[str],
        excluded_skills: List[str],
        required_skills: List[str],
        preferred_skills: List[str],
        job_title: str,
        job_description: str
    ) -> Tuple[float, List[str], List[str], List[str]]:
        """
        Calculates technical fit score (0-100) separating mandatory from preferred skills.
        Strictly prevents claiming any excluded skill.
        """
        if not required_skills:
            # Fallback if no specific skills extracted: check for candidate's core stack in text
            jd_lower = job_description.lower()
            matched = []
            for s in ["selenium", "java", "testng", "cucumber", "api testing", "rest assured", "sql"]:
                if s in jd_lower:
                    matched.append(s.title())
            score = 90.0 if len(matched) >= 2 else 75.0
            return score, matched or ["Core QA"], [], []

        matched_mandatory: List[str] = []
        missing_mandatory: List[str] = []
        matched_preferred: List[str] = []
        missing_preferred: List[str] = []

        for req in required_skills:
            req_clean = req.lower().strip()
            # If skill is in excluded list, it CANNOT be matched
            if any(ex in req_clean or req_clean in ex for ex in excluded_skills):
                missing_mandatory.append(req)
                continue

            if self._is_skill_matched(req_clean, candidate_skills):
                matched_mandatory.append(req)
            else:
                missing_mandatory.append(req)

        for pref in preferred_skills:
            pref_clean = pref.lower().strip()
            if any(ex in pref_clean or pref_clean in ex for ex in excluded_skills):
                missing_preferred.append(pref)
                continue

            if self._is_skill_matched(pref_clean, candidate_skills):
                matched_preferred.append(pref)
            else:
                missing_preferred.append(pref)

        total_req = len(required_skills)
        mand_ratio = len(matched_mandatory) / total_req if total_req > 0 else 1.0

        if preferred_skills:
            pref_ratio = len(matched_preferred) / len(preferred_skills)
            tech_score = round((mand_ratio * 85.0) + (pref_ratio * 15.0), 1)
        else:
            tech_score = round(mand_ratio * 100.0, 1)

        # Bonus: If candidate has core Java + Selenium + API Testing match, ensure strong score
        core_matches = [m.lower() for m in matched_mandatory]
        has_sel = any("selenium" in m for m in core_matches)
        has_java = any("java" in m for m in core_matches)
        if has_sel and has_java and tech_score < 75.0 and len(matched_mandatory) >= 2:
            tech_score = max(tech_score, 80.0)

        all_matched = matched_mandatory + matched_preferred
        all_missing = missing_mandatory + missing_preferred

        return tech_score, all_matched, all_missing, missing_mandatory

    def _compute_preferences_score(
        self,
        job: JobDetails,
        reqs: ExtractedRequirements,
        profile: CandidateProfile
    ) -> Tuple[float, List[str], List[str], Optional[str]]:
        """
        Evaluates Location (35%), Work Mode (25%), Notice Period (20%), and Salary (20%).
        Returns: (score, positive_reasons, review_triggers, hard_skip_reason)
        """
        reasons: List[str] = []
        review_triggers: List[str] = []
        hard_skip: Optional[str] = None

        candidate_locations = [loc.lower() for loc in getattr(profile, "preferred_locations", []) or ["hyderabad", "bangalore", "pune", "chennai", "remote"]]
        candidate_modes = [mode.lower() for mode in getattr(profile, "preferred_work_modes", []) or ["on-site", "hybrid", "remote"]]
        candidate_notice = getattr(profile, "notice_period_days", 60) # 60
        current_ctc = getattr(profile, "current_ctc_lpa", 8.9) # 8.9
        expected_ctc = getattr(profile, "expected_ctc_lpa", 12.0) # 12.0

        combined_text = f"{job.location} {job.title} {job.description_text}".lower()

        # --- 1. Location Fit (35 Points) ---
        loc_score = 35.0
        job_loc = (reqs.required_location or job.location or "").lower()
        is_remote_job = any(w in job_loc or w in combined_text for w in ["remote", "work from home", "wfh"])

        if is_remote_job:
            reasons.append("Remote work option")
            loc_score = 35.0
        elif any(c_loc in job_loc for c_loc in candidate_locations):
            matched_city = next(c_loc.title() for c_loc in candidate_locations if c_loc in job_loc)
            reasons.append(f"Preferred location ({matched_city})")
            loc_score = 35.0
        elif "relocation" in combined_text or "must relocate" in combined_text or "relocate to" in combined_text:
            review_triggers.append("Role mentions relocation requirement")
            loc_score = 20.0
        elif job_loc and not any(c_loc in job_loc for c_loc in candidate_locations):
            # Non-preferred on-site city
            if any(mode in combined_text for mode in ["on-site", "work from office", "in-office"]):
                loc_score = 10.0
                review_triggers.append(f"On-site location ({job.location}) outside preferred cities")
            else:
                loc_score = 25.0
                review_triggers.append(f"Location ({job.location}) requires verification")
        else:
            loc_score = 30.0
            reasons.append("Location flexible / unstated")

        # --- 2. Work Mode Fit (25 Points) ---
        mode_score = 25.0
        job_mode = (reqs.work_mode or job.workplace_type or "").lower()
        if "remote" in job_mode or is_remote_job:
            mode_score = 25.0
        elif "hybrid" in job_mode:
            mode_score = 25.0
            reasons.append("Hybrid mode")
        elif "on-site" in job_mode or "onsite" in job_mode:
            mode_score = 22.0
            reasons.append("On-site mode")
        else:
            mode_score = 25.0

        # --- 3. Notice Period Fit (20 Points) ---
        notice_score = 20.0
        # Check for explicit notice constraints in JD
        req_notice = reqs.required_notice_days
        is_immediate = bool(re.search(r'\b(immediate\s*joiners?|join\s*immediately|immediate\s*joining|0[- ]15\s*days?)\b', combined_text))
        is_30_days = bool(re.search(r'\b(30\s*days?|1\s*month)\s*(?:notice|joining|joiner)\b', combined_text))
        is_60_days = bool(re.search(r'\b(60\s*days?|2\s*months?)\s*(?:notice|joining|joiner)\b', combined_text))

        if is_60_days or req_notice == 60:
            notice_score = 20.0
            reasons.append("60-day notice period accepted")
        elif is_immediate or (req_notice is not None and req_notice <= 15):
            notice_score = 5.0
            review_triggers.append("Job specifies immediate joiner (< 15 days), candidate notice is 60 days")
        elif is_30_days or (req_notice is not None and req_notice <= 30):
            notice_score = 12.0
            review_triggers.append("Job requests 30-day notice period, candidate notice is 60 days")
        else:
            notice_score = 20.0
            reasons.append(f"Notice period ({candidate_notice} days) standard")

        # --- 4. Salary Fit (20 Points) ---
        sal_score = 20.0
        min_sal = reqs.min_salary_lpa
        max_sal = reqs.max_salary_lpa

        if min_sal is not None or max_sal is not None:
            effective_sal = max_sal or min_sal or 0.0
            if effective_sal >= expected_ctc:
                sal_score = 20.0
                reasons.append(f"Salary meets/exceeds expectation ({effective_sal} LPA >= {expected_ctc} LPA)")
            elif effective_sal >= current_ctc:
                sal_score = 14.0
                review_triggers.append(f"Offered salary ({effective_sal} LPA) is below expected ({expected_ctc} LPA) but above current ({current_ctc} LPA)")
            else:
                sal_score = 0.0
                hard_skip = f"Offered salary ({effective_sal} LPA) is below candidate's current CTC ({current_ctc} LPA)"
        else:
            sal_score = 18.0
            reasons.append("Salary competitive / unstated")

        total_pref = loc_score + mode_score + notice_score + sal_score
        return round(total_pref, 1), reasons, review_triggers, hard_skip

    def _compute_risk_score(
        self,
        job: JobDetails,
        reqs: ExtractedRequirements,
        profile: CandidateProfile,
        mandatory_missing: List[str],
        is_exp_gap: bool
    ) -> Tuple[float, List[str]]:
        """
        Computes application risk (0-100, where 0 is minimal risk, 100 is maximum risk).
        """
        risk = 5.0
        factors: List[str] = []

        combined = f"{job.title} {job.description_text}".lower()

        # Scam keywords
        scam_patterns = [
            "registration fee", "training fee", "security deposit", "pay before", "cheque deposit"
        ]
        if any(p in combined for p in scam_patterns):
            risk += 50.0
            factors.append("Suspicious financial terms")

        # Third-party / staffing without clear company
        if any(w in combined for w in ["freelance", "unpaid", "commission only"]):
            risk += 25.0
            factors.append("Commission or unpaid contract structure")

        if mandatory_missing:
            risk += min(20.0, len(mandatory_missing) * 10.0)
            factors.append(f"Missing mandatory skills: {', '.join(mandatory_missing[:2])}")

        if is_exp_gap:
            risk += 15.0
            factors.append("Noticeable experience requirement gap")

        return min(100.0, round(risk, 1)), factors

    def _compute_role_score(self, job_title: str, preferred_roles: List[str]) -> float:
        """
        Universal role relevance calculator:
        Returns: role_score (float 0-100)
        """
        title_clean = job_title.lower()

        # 1. Exact or substring match with any preferred role
        for role in preferred_roles:
            role_clean = role.lower().strip()
            if role_clean in title_clean or title_clean in role_clean:
                return 100.0

        # 2. Key role keywords token matching
        qa_core_tokens = {"qa", "sdet", "test", "testing", "quality", "automation", "tester"}
        title_tokens = set(re.findall(r'\b\w+\b', title_clean))
        if title_tokens.intersection(qa_core_tokens):
            return 95.0

        # 3. Check for unrelated disciplines
        discipline_clusters = [
            {"frontend", "react", "angular", "vue", "ui", "css", "html", "web", "front"},
            {"backend", "golang", "java", "node", "django", "fastapi", "spring", "c#", ".net", "fullstack", "full", "back"},
            {"devops", "sre", "cloud", "infrastructure", "kubernetes", "platform", "docker", "reliability", "site", "sysadmin", "network", "linux"},
            {"data", "datascience", "ml", "ai", "machine", "learning", "analytics", "bi", "scientist", "pipeline", "genai", "llm"},
            {"mobile", "android", "ios", "flutter", "react native", "swift", "kotlin"},
            {"product", "scrum", "agile", "project"},
            {"support", "customer", "service", "helpdesk", "technician", "desk", "operations"},
            {"sales", "marketing", "business", "growth", "outreach", "bd", "bdr", "sdr", "account"},
            {"salesforce", "crm", "sap", "erp", "workday", "servicenow"},
            {"sports", "collector", "scout", "driver", "warehouse", "retail", "security", "assistant", "receptionist", "clerk"},
            {"civil", "mechanical", "electrical", "plumbing", "construction", "accountant", "audit", "tax", "finance", "teller"}
        ]

        for cluster in discipline_clusters:
            if title_tokens.intersection(cluster):
                return 15.0

        if any(w in title_tokens for w in ["developer", "engineer"]):
            return 20.0

        return 20.0

    def _is_skill_matched(self, req_skill: str, candidate_skills: List[str]) -> bool:
        req_clean = req_skill.lower().strip()
        cand_clean = [c.lower().strip() for c in candidate_skills]

        # 1. Exact or clean boundary match
        for cand in cand_clean:
            if cand == req_clean or f" {cand} " in f" {req_clean} " or f" {req_clean} " in f" {cand} ":
                return True

        # 2. Tool & language exact alias equivalence (within SAME technology)
        for tool, aliases in TOOL_ALIASES.items():
            if req_clean in aliases:
                if any(c in aliases for c in cand_clean):
                    return True

        # Specific distinct tools cannot be claimed merely by having a generic umbrella phrase
        # E.g. Candidate who has "Mobile Testing" does NOT possess "Appium" unless Appium is in skills
        specific_tools = {
            "appium", "cypress", "playwright", "selenium", "jmeter", "postman",
            "restassured", "rest-assured", "rest assured", "cucumber", "testng", "junit"
        }
        if req_clean in specific_tools:
            return False

        # 3. Generic Concept / umbrella requirement matching (e.g. "api testing", "ci/cd", "bdd")
        for concept, qualifying_set in CONCEPT_CLUSTERS.items():
            if req_clean == concept or req_clean in qualifying_set:
                if any(any(q in c for q in qualifying_set) for c in cand_clean):
                    return True

        return False
