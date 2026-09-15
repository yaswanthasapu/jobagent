import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from models.evaluation import (
    ClassifiedRequirement,
    DecisionType,
    ExtractedRequirements,
    JobEvaluationResult,
    MatchBreakdown,
    RequirementTier,
    SkillMatchCategory,
)
from models.job import JobDetails
from models.profile import CandidateProfile
from services.llm_service import LLMService

logger = logging.getLogger(__name__)

# Multi-domain discipline clusters: Maps roles and tokens across ALL professional fields
DISCIPLINE_CLUSTERS: Dict[str, Set[str]] = {
    "qa": {
        "qa", "sdet", "test", "testing", "quality", "automation", "tester",
        "quality assurance", "test automation", "manual testing"
    },
    "frontend": {
        "frontend", "react", "angular", "vue", "ui", "css", "html", "web", "front",
        "nextjs", "javascript", "typescript", "tailwind"
    },
    "backend": {
        "backend", "golang", "java", "node", "nodejs", "django", "fastapi", "spring",
        "c#", ".net", "fullstack", "back", "microservices", "ruby", "rails", "php"
    },
    "devops": {
        "devops", "sre", "cloud", "infrastructure", "kubernetes", "platform",
        "docker", "reliability", "site", "sysadmin", "network", "linux", "terraform", "ansible"
    },
    "data": {
        "data", "datascience", "ml", "ai", "machine", "learning", "analytics", "bi",
        "scientist", "pipeline", "genai", "llm", "deep learning", "nlp"
    },
    "mobile": {
        "mobile", "android", "ios", "flutter", "react native", "swift", "kotlin"
    },
    "product": {
        "product", "scrum", "agile", "project", "owner", "program"
    },
    "support": {
        "support", "customer", "service", "helpdesk", "technician", "desk", "operations"
    },
    "sales": {
        "sales", "marketing", "business", "growth", "outreach", "bd", "bdr", "sdr",
        "account", "demand", "seo", "sem", "branding"
    },
    "crm": {
        "salesforce", "crm", "sap", "erp", "workday", "servicenow", "apex"
    },
    "unrelated": {
        "sports", "collector", "scout", "driver", "warehouse", "retail", "security",
        "assistant", "receptionist", "clerk", "plumbing", "construction"
    },
    "finance": {
        "accountant", "audit", "tax", "finance", "teller", "financial", "fp&a",
        "treasury", "bookkeeping", "controller", "cpa", "valuation", "investment"
    },
    "engineering_hardware": {
        "civil", "mechanical", "electrical", "plumbing", "structural", "chemical", "cad"
    },
    "healthcare": {
        "nurse", "nursing", "doctor", "clinical", "medical", "patient", "pharmacist", "healthcare"
    },
    "legal": {
        "legal", "lawyer", "attorney", "paralegal", "compliance", "regulatory", "counsel"
    },
    "hr": {
        "hr", "human resources", "talent", "recruiter", "recruiting", "recruitment", "people operations"
    }
}

# Cross-domain tool, skill and concept aliases
UNIVERSAL_TOOL_ALIASES: Dict[str, Set[str]] = {
    # QA & Test Automation
    "selenium": {"selenium", "selenium webdriver", "webdriver", "selenium grid"},
    "java": {"java", "core java", "j2se", "java 8", "java 11", "java 17", "java8", "java11"},
    "python": {"python", "python3", "pytest", "pyunit", "robot framework"},
    "c#": {"c#", ".net", "csharp", "dotnet", "asp.net", "specflow", "nunit"},
    "playwright": {"playwright"},
    "cypress": {"cypress", "cypress.io"},
    "restassured": {"restassured", "rest-assured", "rest assured", "rest api testing", "api testing", "api automation"},
    "postman": {"postman", "api testing", "api automation", "rest api"},
    "cucumber": {"cucumber", "cucumber bdd", "bdd", "gherkin"},
    "testng": {"testng", "test ng", "junit", "junit5", "junit4"},
    "sql": {"sql", "mysql", "postgresql", "oracle", "pl/sql", "database testing", "database querying", "database", "rdbms"},
    "jenkins": {"jenkins", "ci/cd", "continuous integration"},
    "git": {"git", "github", "gitlab", "version control"},
    "uipath": {"uipath", "rpa", "robotic process automation"},
    "appium": {"appium"},
    "jmeter": {"jmeter", "loadrunner", "gatling", "performance testing", "load testing"},
    
    # Tech & Development
    "react": {"react", "react.js", "reactjs"},
    "node": {"node", "node.js", "nodejs", "express"},
    "docker": {"docker", "containerization", "containers"},
    "kubernetes": {"kubernetes", "k8s"},
    "aws": {"aws", "amazon web services", "cloud"},
    "azure": {"azure", "microsoft azure"},

    # Finance & Accounting
    "financial modeling": {"financial modeling", "financial model", "dcf", "dcf modeling", "valuation", "financial analysis"},
    "excel": {"excel", "advanced excel", "vba", "macros", "spreadsheets"},
    "gaap": {"gaap", "us gaap", "ifrs", "accounting standards"},
    "quickbooks": {"quickbooks", "xero", "freshbooks", "bookkeeping software"},
    "budgeting": {"budgeting", "forecasting", "fp&a", "variance analysis", "financial planning"},

    # Marketing & Growth
    "seo": {"seo", "search engine optimization", "organic search", "on-page seo", "off-page seo"},
    "sem": {"sem", "google ads", "ppc", "search engine marketing", "paid search"},
    "google analytics": {"google analytics", "ga4", "web analytics"},
    "content strategy": {"content strategy", "content marketing", "copywriting"},

    # HR & People
    "talent acquisition": {"talent acquisition", "recruiting", "recruitment", "headhunting", "sourcing"},
    "workday": {"workday", "hris", "bamboohr", "peoplesoft"},

    # Operations & Supply Chain
    "supply chain": {"supply chain", "logistics", "procurement", "inventory management"},
    "lean": {"lean", "six sigma", "continuous improvement", "kaizen"},
}

TOOL_ALIASES = UNIVERSAL_TOOL_ALIASES

# Cross-domain conceptual clusters (generic requirement -> qualifying skill sets)
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
    },
    "financial_analysis": {
        "financial modeling", "dcf", "valuation", "budgeting", "forecasting", "financial analysis", "excel"
    },
    "digital_marketing": {
        "seo", "sem", "google ads", "google analytics", "content marketing", "social media marketing"
    },
    "talent_operations": {
        "talent acquisition", "recruiting", "sourcing", "onboarding", "hris", "workday"
    }
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
        JOB DATA -> UNDERSTAND -> TIER 1 GATEKEEPER -> TIER 2 DEEP EVALUATION -> DECISION
        """
        # 1. Extract requirements via LLM or NLP
        reqs: ExtractedRequirements = await self.llm_service.extract_job_requirements(
            job_title=job.title,
            job_description=job.description_text
        )

        candidate_exp = float(profile.professional.total_experience_years or profile.experience_years or 0.0)
        candidate_skills = [s.lower().strip() for s in profile.skills]
        excluded_skills = [s.lower().strip() for s in getattr(profile, "excluded_skills", []) or []]

        # ---------------------------------------------------------
        # TIER 1: Fast Heuristic Gatekeeper
        # ---------------------------------------------------------
        is_blocked, gate_decision, gate_reason = self._evaluate_tier_1_gatekeeper(
            job=job,
            profile=profile,
            reqs=reqs,
            candidate_exp=candidate_exp
        )
        if is_blocked and gate_decision and gate_reason:
            logger.info(f"Tier 1 Gatekeeper rejected job '{job.title}': {gate_reason}")
            is_exp_block = any(w in gate_reason.lower() for w in ["experience", "years", "exceeds", "threshold"])
            breakdown = MatchBreakdown(
                technical_fit=0.0,
                experience_fit=15.0 if is_exp_block else 50.0,
                role_fit=15.0 if "role" in gate_reason.lower() else 50.0,
                preference_fit=0.0 if "location" in gate_reason.lower() else 50.0,
                risk_score=75.0,
                overall_score=20.0,
                confidence=95.0,
                skill_score=0.0,
                experience_score=15.0 if is_exp_block else 50.0,
                role_score=15.0 if "role" in gate_reason.lower() else 50.0,
                matched_skills=[],
                missing_skills=reqs.required_skills,
                mandatory_missing_skills=reqs.required_skills,
                incompatible_skills=[],
                experience_analysis=gate_reason,
                reasoning=gate_reason,
            )
            return JobEvaluationResult(
                job_id=job.job_id,
                job_title=job.title,
                company=job.company,
                decision=gate_decision,
                match_breakdown=breakdown,
                extracted_requirements=reqs,
            )

        # ---------------------------------------------------------
        # TIER 2: Deep Semantic Evaluation
        # ---------------------------------------------------------
        return self._evaluate_tier_2_deep(
            job=job,
            profile=profile,
            reqs=reqs,
            candidate_exp=candidate_exp,
            candidate_skills=candidate_skills,
            excluded_skills=excluded_skills
        )

    def _evaluate_tier_1_gatekeeper(
        self,
        job: JobDetails,
        profile: CandidateProfile,
        reqs: ExtractedRequirements,
        candidate_exp: float
    ) -> Tuple[bool, Optional[DecisionType], Optional[str]]:
        """
        Fast Heuristic Gatekeeper (Tier 1):
        Instantly flags clear dealbreakers without running full multi-factor scoring.
        """
        combined = f"{job.title} {job.description_text}".lower()
        title_lower = job.title.lower()

        # 1. Extreme experience disparity:
        # e.g., Job requires 12+ years for Director/Head role while candidate has < 5 years
        is_senior_exec = any(w in title_lower for w in ["director", "vp", "vice president", "head of", "chief", "principal"])
        if reqs.min_experience_years >= 12.0 and candidate_exp < 6.0:
            return True, DecisionType.REJECT, (
                f"Requires {reqs.min_experience_years}+ years (Candidate has {candidate_exp} years) - Exceeds upper threshold."
            )
        if reqs.min_experience_years >= candidate_exp + 5.0 and reqs.min_experience_years >= 8.0:
            return True, DecisionType.SKIP, (
                f"Severe experience gap: Job requires minimum {reqs.min_experience_years} years; candidate has {candidate_exp} years."
            )

        # 2. Strict visa / legal blocker if detected in description
        if any(w in combined for w in ["us citizenship required", "must hold active security clearance", "ts/sci clearance required"]):
            return True, DecisionType.SKIP, "Job requires strict national security clearance / specific citizenship not confirmed in profile."

        return False, None, None

    def _evaluate_tier_2_deep(
        self,
        job: JobDetails,
        profile: CandidateProfile,
        reqs: ExtractedRequirements,
        candidate_exp: float,
        candidate_skills: List[str],
        excluded_skills: List[str]
    ) -> JobEvaluationResult:
        """
        Deep Semantic Evaluation (Tier 2):
        Evaluates Domain/Role Fit, Technical & Domain Skills, Experience, Preferences, and Risk.
        """
        # 1. Incompatible Stack / Specialization Check
        is_incomp_stack, incomp_stack_reason, incomp_skills = self._check_incompatible_stack(
            job_title=job.title,
            job_description=job.description_text,
            candidate_skills=candidate_skills,
            excluded_skills=excluded_skills,
            reqs=reqs
        )

        # 2. Role Relevance & Discipline Score (15% weight)
        target_roles = getattr(profile, "target_roles", None) or profile.preferred_roles or []
        if not target_roles:
            if profile.professional.designation:
                target_roles = [profile.professional.designation]
            else:
                target_roles = [job.title]

        role_score = self._compute_role_score(
            job_title=job.title,
            preferred_roles=target_roles
        )
        is_discipline_mismatch = (role_score <= 25.0)
        role_reason = "Unrelated role discipline"

        # 3. Experience Fit Score (20% weight)
        exp_score, exp_analysis, is_exp_hard_reject = self._compute_experience_score(
            candidate_exp=candidate_exp,
            min_exp_req=reqs.min_experience_years,
            max_exp_req=reqs.max_experience_years,
            target_level=reqs.target_role_level,
            job_title=job.title
        )

        # 4. Technical / Domain Skill Fit Score (50% weight)
        tech_score, matched_skills, missing_skills, mandatory_missing, skill_classifications = self._compute_technical_score(
            candidate_skills=candidate_skills,
            excluded_skills=excluded_skills,
            required_skills=reqs.required_skills,
            preferred_skills=reqs.preferred_skills,
            job_title=job.title,
            job_description=job.description_text
        )

        # 5. Preferences Fit Score (10% weight)
        pref_score, pref_reasons, pref_review_triggers, pref_hard_skip = self._compute_preferences_score(
            job=job,
            reqs=reqs,
            profile=profile
        )

        # 6. Application Risk Score (5% weight)
        risk_score, risk_factors = self._compute_risk_score(
            job=job,
            reqs=reqs,
            profile=profile,
            mandatory_missing=mandatory_missing,
            is_exp_gap=(reqs.min_experience_years > candidate_exp + 1.5)
        )

        # 7. Domain Fit Score
        domain_fit = 100.0 if not is_discipline_mismatch else 20.0

        # 8. Overall Composite Score
        overall_score = round(
            (tech_score * 0.50) +
            (exp_score * 0.20) +
            (role_score * 0.15) +
            (pref_score * 0.10) +
            ((100.0 - risk_score) * 0.05),
            1
        )

        min_score = profile.job_preferences.minimum_match_score or 60.0

        # 9. Autonomous Decision Logic
        decision: DecisionType
        reasoning: str
        confidence: float = 90.0

        if is_incomp_stack:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 35.0)
            reasoning = f"Incompatible technology stack: {incomp_stack_reason}. Skipping to next opportunity."

        elif is_discipline_mismatch:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 25.0)
            reasoning = f"Role discipline mismatch: '{job.title}' does not align with candidate target roles ({role_reason})."

        elif is_exp_hard_reject:
            decision = DecisionType.SKIP
            confidence = 95.0
            overall_score = min(overall_score, 20.0 if "exceeds" in exp_analysis.lower() else 30.0)
            reasoning = f"Hard rejection on experience requirement: {exp_analysis}"

        elif pref_hard_skip:
            decision = DecisionType.SKIP
            confidence = 90.0
            overall_score = min(overall_score, 40.0)
            reasoning = f"Job preference mismatch: {pref_hard_skip}."

        elif reqs.required_skills and (len(matched_skills) == 0 or (len(mandatory_missing) >= 2 and tech_score < 40.0)):
            decision = DecisionType.SKIP
            confidence = 90.0
            overall_score = min(overall_score, 40.0)
            reasoning = (
                f"Missing core mandatory skills: {', '.join(mandatory_missing or missing_skills[:4])}. "
                f"Candidate matches {len(matched_skills)}/{len(reqs.required_skills)} required skills ({tech_score:.1f}%)."
            )

        elif overall_score < min_score:
            decision = DecisionType.SKIP
            confidence = 85.0
            reasoning = (
                f"Overall match score ({overall_score:.1f}%) is below minimum threshold ({min_score}%). "
                f"Gaps: {', '.join(missing_skills[:3]) if missing_skills else 'insufficient alignment'}."
            )

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

        else:
            decision = DecisionType.APPLY
            confidence = 95.0
            matched_preview = ", ".join(matched_skills[:4]) if matched_skills else "Core skills aligned"
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
            domain_fit=domain_fit,
            overall_score=overall_score,
            confidence=confidence,
            skill_score=tech_score,
            experience_score=exp_score,
            role_score=role_score,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            mandatory_missing_skills=mandatory_missing,
            incompatible_skills=incomp_skills,
            skill_classifications=skill_classifications,
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
        Determines if a job mandatorily requires a skill or technology stack that candidate excludes
        or lacks, dynamically based on candidate's profile.
        """
        title_l = job_title.lower()
        jd_l = job_description.lower()
        incomp_found: List[str] = []

        # 1. Check explicit excluded skills
        for ex in excluded_skills:
            ex_clean = ex.lower().strip()
            if ex_clean and (re.search(rf'\b{re.escape(ex_clean)}\b', title_l) or ex_clean in [s.lower() for s in reqs.required_skills]):
                incomp_found.append(ex.title())
                return True, f"Candidate excludes {ex.title()}", incomp_found

        # 2. Check programming language exclusivity based on candidate's actual skills:
        has_java = any("java" in s for s in candidate_skills)
        has_python = any("python" in s for s in candidate_skills)
        has_csharp = any(any(k in s for k in ["c#", ".net", "csharp", "dotnet"]) for s in candidate_skills)

        # Python check (if candidate lacks Python):
        if not has_python:
            python_optional = bool(
                re.search(r'(?:java\s*(?:or|/)\s*python|python\s*(?:or|/)\s*java)', jd_l) or
                re.search(r'(?:good to have|nice to have|preferred|optional|bonus|plus|familiarity with).*?python', jd_l, re.DOTALL)
            )
            if re.search(r'\bpython\b', title_l):
                incomp_found.append("Python")
                return True, "Job title specifically demands Python automation", incomp_found

            if not python_optional:
                if re.search(r'\b(strong|expert|hands[- ]on|proficient|minimum \d\+? years?)\s+.*?\bpython\b', jd_l) and \
                   not any(k in jd_l for k in ["java", "playwright", "selenium"]):
                    incomp_found.append("Python")
                    return True, "Job explicitly demands Python as primary programming language", incomp_found

                if "python" in [s.lower() for s in reqs.required_skills] and not has_java and not any(k in reqs.required_skills for k in ["Java", "Selenium"]):
                    incomp_found.append("Python")
                    return True, "Job strictly requires Python automation", incomp_found

        # C# / .NET check (if candidate lacks C#):
        if not has_csharp:
            csharp_optional = bool(re.search(r'(?:good to have|nice to have|preferred|optional|bonus|plus).*?(?:c#|\.net|specflow)', jd_l))
            if not csharp_optional:
                if re.search(r'(?<!\w)(c#|\.net|csharp|dotnet|specflow)(?!\w)', title_l):
                    incomp_found.append("C#/.NET")
                    return True, "Job title specifically demands C# / .NET automation", incomp_found
                if re.search(r'(?<!\w)(c#|\.net|csharp|dotnet|specflow)(?!\w)', jd_l) and not any(k in jd_l for k in ["java", "playwright", "selenium", "python"]):
                    incomp_found.append("C#/.NET")
                    return True, "Job explicitly demands C# / .NET as primary automation stack", incomp_found

        # Salesforce / CRM check:
        has_crm = any(any(k in s for k in ["salesforce", "crm", "apex", "sap"]) for s in candidate_skills)
        if not has_crm:
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
        Universal experience fit calculator:
        - 0 to <= candidate_exp + 0.2: 100.0 (Candidate meets or closely meets required experience)
        - Manageable gap: candidate_exp to candidate_exp + 2.0: 80.0 - 95.0
        - Moderate gap: candidate_exp + 2.1 to candidate_exp + 3.0: 60.0 - 75.0
        - Severe gap: >= candidate_exp + 3.1 or >= 7.0 for junior/mid (exp < 4.5): 15.0 (Hard Skip)
        - Senior/Director role requiring >= 6.0 (when candidate has < 4.5) or >= 12.0: 15.0 (Hard Skip)
        """
        title_lower = job_title.lower()
        is_senior_title = any(w in title_lower for w in ["lead", "principal", "director", "architect", "manager", "head"])

        # Hard rejection: minimum experience requirement is high
        if (candidate_exp < 4.5 and min_exp_req >= 7.0) or \
           (is_senior_title and candidate_exp < 4.5 and min_exp_req >= 6.0) or \
           (min_exp_req >= 12.0 and candidate_exp < 8.0) or \
           (min_exp_req >= candidate_exp + 5.0 and min_exp_req >= 8.0):
            return (
                15.0,
                f"Requires {min_exp_req}+ years (Candidate has {candidate_exp} years) - Exceeds upper threshold.",
                True
            )

        # Ideal fit: candidate meets requirement
        effective_upper = max(4.0, candidate_exp + 0.2)
        if min_exp_req <= effective_upper:
            return (
                100.0,
                f"Candidate experience ({candidate_exp} years) aligns well with required {min_exp_req} years.",
                False
            )

        # Manageable gap: up to +2.1 years stretch
        if min_exp_req <= candidate_exp + 2.1:
            deficit = min_exp_req - candidate_exp
            score = max(70.0, round(100.0 - (deficit * 15.0), 1))
            return (
                score,
                f"Candidate has {candidate_exp} years; role requires {min_exp_req} years (manageable gap).",
                False
            )

        # Marginal gap
        return (
            50.0,
            f"Candidate has {candidate_exp} years; role requires {min_exp_req} years.",
            False
        )

    def _classify_single_skill(
        self,
        req_clean: str,
        candidate_skills: List[str],
        excluded_skills: List[str]
    ) -> SkillMatchCategory:
        """Categorizes a single requirement against candidate skills."""
        # 1. Conflicting / Excluded
        if any(ex in req_clean or req_clean in ex for ex in excluded_skills if ex):
            return SkillMatchCategory.CONFLICTING

        # 2. Direct exact or boundary match
        cand_clean = [c.lower().strip() for c in candidate_skills]
        for cand in cand_clean:
            if cand == req_clean or f" {cand} " in f" {req_clean} " or f" {req_clean} " in f" {cand} ":
                return SkillMatchCategory.DIRECT_MATCH

        # 3. Universal Tool Alias Equivalence
        for tool, aliases in UNIVERSAL_TOOL_ALIASES.items():
            if req_clean in aliases and any(c in aliases for c in cand_clean):
                return SkillMatchCategory.DIRECT_MATCH

        # Specific distinct tools cannot be claimed merely by generic conceptual clusters
        specific_tools = {
            "appium", "cypress", "playwright", "selenium", "jmeter", "postman",
            "restassured", "rest-assured", "rest assured", "cucumber", "testng", "junit"
        }
        if req_clean in specific_tools:
            return SkillMatchCategory.MISSING

        # 4. Conceptual Cluster Equivalence
        for concept, qualifying_set in CONCEPT_CLUSTERS.items():
            if req_clean == concept or req_clean in qualifying_set:
                if any(any(q in c for q in qualifying_set) for c in cand_clean):
                    return SkillMatchCategory.RELATED_MATCH

        # 5. Token overlap / Partial Match
        req_tokens = set(re.findall(r'\b\w+\b', req_clean)) - {"experience", "knowledge", "skills", "good", "strong", "understanding"}
        if req_tokens:
            for cand in cand_clean:
                cand_tokens = set(re.findall(r'\b\w+\b', cand))
                if len(req_tokens.intersection(cand_tokens)) >= max(1, len(req_tokens) // 2):
                    return SkillMatchCategory.PARTIAL_MATCH

        return SkillMatchCategory.MISSING

    def _compute_technical_score(
        self,
        candidate_skills: List[str],
        excluded_skills: List[str],
        required_skills: List[str],
        preferred_skills: List[str],
        job_title: str,
        job_description: str
    ) -> Tuple[float, List[str], List[str], List[str], Dict[str, str]]:
        """
        Calculates domain/technical fit score (0-100) separating mandatory from preferred skills,
        and produces structured SkillMatchCategory classifications for each requirement.
        """
        classifications: Dict[str, str] = {}

        if not required_skills:
            # Dynamic fallback if no structured skills extracted: check candidate skills against JD text
            jd_lower = job_description.lower()
            matched = []
            for s in candidate_skills:
                if len(s) >= 3 and s in jd_lower:
                    matched.append(s.title())
                    classifications[s.title()] = SkillMatchCategory.DIRECT_MATCH.value
            score = 90.0 if len(matched) >= 2 else (75.0 if len(matched) == 1 else 60.0)
            return score, matched or ["Core Alignment"], [], [], classifications

        matched_mandatory: List[str] = []
        missing_mandatory: List[str] = []
        matched_preferred: List[str] = []
        missing_preferred: List[str] = []

        for req in required_skills:
            req_clean = req.lower().strip()
            cat = self._classify_single_skill(req_clean, candidate_skills, excluded_skills)
            classifications[req] = cat.value
            if cat in [SkillMatchCategory.DIRECT_MATCH, SkillMatchCategory.RELATED_MATCH, SkillMatchCategory.TRANSFERABLE, SkillMatchCategory.PARTIAL_MATCH]:
                matched_mandatory.append(req)
            else:
                missing_mandatory.append(req)

        for pref in preferred_skills:
            pref_clean = pref.lower().strip()
            cat = self._classify_single_skill(pref_clean, candidate_skills, excluded_skills)
            classifications[pref] = cat.value
            if cat in [SkillMatchCategory.DIRECT_MATCH, SkillMatchCategory.RELATED_MATCH, SkillMatchCategory.TRANSFERABLE, SkillMatchCategory.PARTIAL_MATCH]:
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

        # Bonus: If candidate has matched >= 2 primary skills, ensure solid score >= 80.0
        if len(matched_mandatory) >= 2 and mand_ratio >= 0.5 and tech_score < 75.0:
            tech_score = max(tech_score, 80.0)

        all_matched = matched_mandatory + matched_preferred
        all_missing = missing_mandatory + missing_preferred

        return tech_score, all_matched, all_missing, missing_mandatory, classifications

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

        candidate_locations = [loc.lower() for loc in getattr(profile, "preferred_locations", []) or []]
        candidate_modes = [mode.lower() for mode in getattr(profile, "preferred_work_modes", []) or ["remote", "hybrid", "on-site"]]
        candidate_notice = getattr(profile, "notice_period_days", 30) or 30
        current_ctc = getattr(profile, "current_ctc_lpa", 0.0) or 0.0
        expected_ctc = getattr(profile, "expected_ctc_lpa", 0.0) or 0.0

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

        if (min_sal is not None or max_sal is not None) and (current_ctc > 0 or expected_ctc > 0):
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
        Universal domain-agnostic role relevance calculator:
        1. Exact or substring match with any candidate preferred/target role -> 100.0.
        2. Dynamic token overlap between candidate target roles and job title.
        3. Cross-discipline domain alignment:
           - Inferred candidate discipline domains vs inferred job discipline domain.
           - If job matches candidate discipline -> high score (90.0 - 100.0).
           - If job title belongs to an unrelated discipline cluster outside candidate's domains -> score <= 25.0.
        """
        title_clean = job_title.lower().strip()
        title_tokens = set(re.findall(r'\b\w+\b', title_clean))

        # 1. Exact or clean substring match with any preferred role
        for role in preferred_roles:
            role_clean = role.lower().strip()
            if role_clean == title_clean or role_clean in title_clean or title_clean in role_clean:
                return 100.0

        # Collect candidate's target tokens and disciplines
        pref_tokens: Set[str] = set()
        for r in preferred_roles:
            pref_tokens.update(re.findall(r'\b\w+\b', r.lower()))

        cand_disciplines: Set[str] = set()
        for disc, keywords in DISCIPLINE_CLUSTERS.items():
            if pref_tokens.intersection(keywords):
                cand_disciplines.add(disc)

        job_disciplines: Set[str] = set()
        for disc, keywords in DISCIPLINE_CLUSTERS.items():
            if title_tokens.intersection(keywords):
                job_disciplines.add(disc)

        # 2. Check if candidate's discipline matches job's discipline
        matching_disciplines = cand_disciplines.intersection(job_disciplines)
        if matching_disciplines:
            # Overlap tokens excluding generic seniority terms
            meaningful_overlap = (title_tokens.intersection(pref_tokens)) - {
                "senior", "lead", "engineer", "developer", "specialist", "manager", "associate", "ii", "i", "iii", "software"
            }
            if meaningful_overlap:
                return 95.0
            return 90.0

        # 3. Check for distinct conflicting/unrelated disciplines
        # If candidate belongs to a specific discipline (e.g. QA, Finance, Marketing, HR, etc.)
        # and job belongs to other discipline clusters, penalize to <= 25.0
        if cand_disciplines:
            for disc, cluster in DISCIPLINE_CLUSTERS.items():
                if disc not in cand_disciplines and title_tokens.intersection(cluster):
                    return 15.0

        if any(w in title_tokens for w in ["developer", "engineer"]):
            if "backend" not in cand_disciplines and "frontend" not in cand_disciplines and "devops" not in cand_disciplines:
                return 20.0
            return 80.0

        return 20.0

    def _is_skill_matched(self, req_skill: str, candidate_skills: List[str]) -> bool:
        cat = self._classify_single_skill(req_skill.lower().strip(), candidate_skills, [])
        return cat in [SkillMatchCategory.DIRECT_MATCH, SkillMatchCategory.RELATED_MATCH, SkillMatchCategory.TRANSFERABLE, SkillMatchCategory.PARTIAL_MATCH]

