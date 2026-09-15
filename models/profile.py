from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

class PersonalInfo(BaseModel):
    full_name: str = Field(default="", description="Candidate's full name")
    email: str = Field(default="", description="Candidate's email address")
    phone: str = Field(default="", description="Candidate's contact phone number")
    location: str = Field(default="", description="Candidate's current location")
    gender: Optional[str] = Field(default=None, description="Candidate's gender")
    race_ethnicity: Optional[str] = Field(default=None, description="Candidate's race/ethnicity")

class ProfessionalInfo(BaseModel):
    designation: str = Field(default="", description="Current job title or designation")
    total_experience_years: float = Field(default=0.0, description="Total professional experience in years")
    current_company: str = Field(default="", description="Name of the current employer")
    current_lpa: float = Field(default=0.0, description="Current CTC in Lakhs Per Annum (LPA)")
    expected_lpa: float = Field(default=0.0, description="Expected CTC in Lakhs Per Annum (LPA)")
    notice_period_days: int = Field(default=30, description="Official notice period in calendar days")

class JobPreferences(BaseModel):
    minimum_match_score: float = Field(default=60.0, description="Minimum fit score (0-100) to proceed with applying")
    easy_apply_only: bool = Field(default=True, description="Target only Easy Apply postings")
    require_human_approval: bool = Field(default=True, description="Pause before final submission for human approval")

class CandidateProfile(BaseModel):
    # Single Source of Truth root attributes
    name: Optional[str] = None
    current_role: Optional[str] = None
    experience_years: Optional[float] = None
    current_ctc_lpa: Optional[float] = None
    expected_ctc_lpa: Optional[float] = None
    notice_period_days: Optional[int] = None

    target_roles: List[str] = Field(default_factory=list, description="Target job titles")
    primary_roles: List[str] = Field(default_factory=list, description="Primary focus roles")
    skills: List[str] = Field(default_factory=list, description="Candidate skill inventory")
    excluded_skills: List[str] = Field(default_factory=list, description="Skills candidate does NOT possess (never claim)")
    preferred_locations: List[str] = Field(default_factory=list, description="Preferred employment locations")
    preferred_work_modes: List[str] = Field(default_factory=list, description="Preferred work modes (On-site, Hybrid, Remote)")

    # Nested models for backwards compatibility
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    professional: ProfessionalInfo = Field(default_factory=ProfessionalInfo)
    preferred_roles: List[str] = Field(default_factory=list, description="Alias for target_roles")
    job_preferences: JobPreferences = Field(default_factory=JobPreferences)
    resume_filename: Optional[str] = Field(default=None, description="Original filename of candidate's uploaded resume")

    @model_validator(mode="before")
    @classmethod
    def sync_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            personal_dict = data.get("personal", {}) if isinstance(data.get("personal"), dict) else {}
            prof_dict = data.get("professional", {}) if isinstance(data.get("professional"), dict) else {}

            name = personal_dict.get("full_name") or data.get("name")
            current_role = prof_dict.get("designation") or data.get("current_role")
            
            exp = prof_dict.get("total_experience_years")
            if exp is None:
                exp = data.get("experience_years")
            
            c_lpa = prof_dict.get("current_lpa")
            if c_lpa is None:
                c_lpa = data.get("current_ctc_lpa")
                
            e_lpa = prof_dict.get("expected_lpa")
            if e_lpa is None:
                e_lpa = data.get("expected_ctc_lpa")
                
            np_days = prof_dict.get("notice_period_days")
            if np_days is None:
                np_days = data.get("notice_period_days")

            roles = data.get("target_roles") or data.get("preferred_roles") or []

            # Populate personal dictionary
            if name:
                personal_dict["full_name"] = str(name).strip()
            data["personal"] = personal_dict

            # Populate professional dictionary
            if current_role:
                prof_dict["designation"] = str(current_role).strip()
            if exp is not None:
                prof_dict["total_experience_years"] = float(exp)
            if c_lpa is not None:
                prof_dict["current_lpa"] = float(c_lpa)
            if e_lpa is not None:
                prof_dict["expected_lpa"] = float(e_lpa)
            if np_days is not None:
                prof_dict["notice_period_days"] = int(np_days)
            data["professional"] = prof_dict

            # Populate root fields to keep in exact sync
            if name:
                data["name"] = str(name).strip()
            if current_role:
                data["current_role"] = str(current_role).strip()
            if exp is not None:
                data["experience_years"] = float(exp)
            if c_lpa is not None:
                data["current_ctc_lpa"] = float(c_lpa)
            if e_lpa is not None:
                data["expected_ctc_lpa"] = float(e_lpa)
            if np_days is not None:
                data["notice_period_days"] = int(np_days)

            # Align role lists
            if roles:
                data["target_roles"] = roles
                data["preferred_roles"] = roles

        return data

