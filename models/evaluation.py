from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class DecisionType(str, Enum):
    APPLY = "APPLY"
    SKIP = "SKIP"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    REJECT = "REJECT"  # Retained for backwards compatibility

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, DecisionType):
            if self.value in ("SKIP", "REJECT") and other.value in ("SKIP", "REJECT"):
                return True
            return self.value == other.value
        if isinstance(other, str):
            if self.value in ("SKIP", "REJECT") and other in ("SKIP", "REJECT"):
                return True
            return self.value == other
        return False

    __hash__ = str.__hash__

class ExtractedRequirements(BaseModel):
    required_skills: List[str] = Field(default_factory=list, description="Explicit mandatory technical skills required")
    preferred_skills: List[str] = Field(default_factory=list, description="Optional, preferred, or nice-to-have skills")
    min_experience_years: float = Field(default=0.0, description="Minimum required experience in years (e.g., 3.0)")
    max_experience_years: Optional[float] = Field(default=None, description="Maximum experience if range given (e.g. 5-8)")
    target_role_level: str = Field(default="Mid", description="Entry, Mid, Senior, Lead, Principal, Manager")
    target_designation: str = Field(default="", description="Normalized target role title")
    required_location: Optional[str] = Field(default=None, description="Location requirement stated in post")
    work_mode: Optional[str] = Field(default=None, description="Remote, Hybrid, On-site")
    required_notice_days: Optional[int] = Field(default=None, description="Notice period requirement in days if stated")
    min_salary_lpa: Optional[float] = Field(default=None, description="Minimum offered salary in LPA if stated")
    max_salary_lpa: Optional[float] = Field(default=None, description="Maximum offered salary in LPA if stated")

class MatchBreakdown(BaseModel):
    # Multi-Factor Sub-Scores
    technical_fit: float = Field(default=0.0, description="Score 0-100 for technical skill overlap (50% weight)")
    experience_fit: float = Field(default=0.0, description="Score 0-100 for experience alignment (20% weight)")
    role_fit: float = Field(default=0.0, description="Score 0-100 for role title & seniority relevance (15% weight)")
    preference_fit: float = Field(default=100.0, description="Score 0-100 for location/mode/salary/notice (10% weight)")
    risk_score: float = Field(default=0.0, description="Risk factor 0-100 where lower is better (5% weight)")
    overall_score: float = Field(..., description="Weighted composite match score 0-100")
    confidence: float = Field(default=85.0, description="Confidence in decision 0-100")

    # Compatibility fields
    skill_score: float = Field(default=0.0, description="Alias for technical_fit")
    experience_score: float = Field(default=0.0, description="Alias for experience_fit")
    role_score: float = Field(default=0.0, description="Alias for role_fit")

    matched_skills: List[str] = Field(default_factory=list, description="Skills possessed by candidate matching requirements")
    missing_skills: List[str] = Field(default_factory=list, description="Skills required by job not found in candidate profile")
    mandatory_missing_skills: List[str] = Field(default_factory=list, description="Mandatory core skills missing")
    incompatible_skills: List[str] = Field(default_factory=list, description="Explicit incompatible stack skills")

    experience_analysis: str = Field(default="", description="Breakdown of candidate exp vs required exp")
    reasoning: str = Field(..., description="Summary explanation of the evaluation decision")

class JobEvaluationResult(BaseModel):
    job_id: str
    job_title: str = Field(default="")
    company: str = Field(default="")
    decision: DecisionType
    match_breakdown: MatchBreakdown
    extracted_requirements: ExtractedRequirements

    def to_decision_dict(self) -> Dict[str, Any]:
        return {
            "job_title": self.job_title,
            "company": self.company,
            "decision": self.decision.value,
            "match_score": self.match_breakdown.overall_score,
            "technical_fit": self.match_breakdown.technical_fit,
            "experience_fit": self.match_breakdown.experience_fit,
            "role_fit": self.match_breakdown.role_fit,
            "preference_fit": self.match_breakdown.preference_fit,
            "risk": self.match_breakdown.risk_score,
            "matched_skills": self.match_breakdown.matched_skills,
            "missing_skills": self.match_breakdown.missing_skills,
            "mandatory_missing_skills": self.match_breakdown.mandatory_missing_skills,
            "incompatible_skills": self.match_breakdown.incompatible_skills,
            "reason": self.match_breakdown.reasoning,
            "confidence": self.match_breakdown.confidence
        }

