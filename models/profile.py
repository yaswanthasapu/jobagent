from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

class PersonalInfo(BaseModel):
    full_name: str = Field(default="", description="Candidate's full name")
    email: str = Field(default="yaswanth901@gmail.com", description="Candidate's email address")
    phone: str = Field(default="6281306458", description="Candidate's contact phone number")
    location: str = Field(default="Hyderabad, Telangana, India", description="Candidate's current location")
    gender: Optional[str] = Field(default="Male", description="Candidate's gender")
    race_ethnicity: Optional[str] = Field(default="Asian", description="Candidate's race/ethnicity")

class ProfessionalInfo(BaseModel):
    designation: str = Field(default="QA Engineer", description="Current job title or designation")
    total_experience_years: float = Field(default=3.9, description="Total professional experience in years")
    current_company: str = Field(default="Magellanic-Cloud", description="Name of the current employer")
    current_lpa: float = Field(default=8.9, description="Current CTC in Lakhs Per Annum (LPA)")
    expected_lpa: float = Field(default=12.0, description="Expected CTC in Lakhs Per Annum (LPA)")
    notice_period_days: int = Field(default=60, description="Official notice period in calendar days")

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
    resume_filename: Optional[str] = Field(default="Yaswanth_Asapu_QA_Engineer_Selenium_RPA.pdf", description="Original filename of candidate's uploaded resume")

    @model_validator(mode="before")
    @classmethod
    def sync_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            name = data.get("name") or (data.get("personal", {}).get("full_name") if isinstance(data.get("personal"), dict) else None)
            current_role = data.get("current_role") or (data.get("professional", {}).get("designation") if isinstance(data.get("professional"), dict) else None)
            
            exp = data.get("experience_years")
            if exp is None and isinstance(data.get("professional"), dict):
                exp = data.get("professional", {}).get("total_experience_years")
            
            c_lpa = data.get("current_ctc_lpa")
            if c_lpa is None and isinstance(data.get("professional"), dict):
                c_lpa = data.get("professional", {}).get("current_lpa")
                
            e_lpa = data.get("expected_ctc_lpa")
            if e_lpa is None and isinstance(data.get("professional"), dict):
                e_lpa = data.get("professional", {}).get("expected_lpa")
                
            np_days = data.get("notice_period_days")
            if np_days is None and isinstance(data.get("professional"), dict):
                np_days = data.get("professional", {}).get("notice_period_days")

            roles = data.get("target_roles") or data.get("preferred_roles") or []

            # Populate personal dictionary
            personal_dict = data.get("personal", {}) if isinstance(data.get("personal"), dict) else {}
            if name:
                personal_dict["full_name"] = name
            data["personal"] = personal_dict

            # Populate professional dictionary
            prof_dict = data.get("professional", {}) if isinstance(data.get("professional"), dict) else {}
            if prof_dict.get("designation"):
                current_role = prof_dict["designation"]
            elif current_role:
                if current_role == "QA Engineer":
                    prof_dict["designation"] = "QA Automation Engineer"
                else:
                    prof_dict["designation"] = current_role
            if exp is not None:
                prof_dict["total_experience_years"] = float(exp)
            if c_lpa is not None:
                prof_dict["current_lpa"] = float(c_lpa)
            if e_lpa is not None:
                prof_dict["expected_lpa"] = float(e_lpa)
            if np_days is not None:
                prof_dict["notice_period_days"] = int(np_days)
            data["professional"] = prof_dict

            # Populate root fields
            if not data.get("name") and name:
                data["name"] = name
            if not data.get("current_role") and current_role:
                data["current_role"] = current_role
            if data.get("experience_years") is None and exp is not None:
                data["experience_years"] = float(exp)
            if data.get("current_ctc_lpa") is None and c_lpa is not None:
                data["current_ctc_lpa"] = float(c_lpa)
            if data.get("expected_ctc_lpa") is None and e_lpa is not None:
                data["expected_ctc_lpa"] = float(e_lpa)
            if data.get("notice_period_days") is None and np_days is not None:
                data["notice_period_days"] = int(np_days)

            # Align role lists
            if not data.get("target_roles") and roles:
                data["target_roles"] = roles
            if not data.get("preferred_roles") and roles:
                data["preferred_roles"] = roles

        return data

