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

class WorkExperience(BaseModel):
    title: str = Field(default="", description="Job title held")
    company: str = Field(default="", description="Company or organization name")
    start_date: Optional[str] = Field(default=None, description="Start date (YYYY-MM or YYYY)")
    end_date: Optional[str] = Field(default=None, description="End date (YYYY-MM, YYYY, or 'Present')")
    years: Optional[float] = Field(default=None, description="Duration in years")
    skills_used: List[str] = Field(default_factory=list, description="Skills, tools, or methodologies used in this role")
    description: Optional[str] = Field(default="", description="Summary of responsibilities and achievements")

class EducationInfo(BaseModel):
    degree: str = Field(default="Bachelor's Degree", description="Highest degree obtained")
    field_of_study: str = Field(default="Electronics and Communication Engineering", description="Major / Field of study")
    major: str = Field(default="Electronics and Communication Engineering", description="Major specialization")
    graduation_department: str = Field(default="Electronics and Communication Engineering", description="Graduation department")
    institution: Optional[str] = Field(default="", description="School or College name")
    from_month: Optional[str] = Field(default="January", description="Start month")
    from_year: Optional[int] = Field(default=2015, description="Start year")
    to_month: Optional[str] = Field(default="February", description="End/Graduation month")
    to_year: Optional[int] = Field(default=2022, description="Graduation year")

class CandidateProfile(BaseModel):
    # Single Source of Truth root attributes
    name: Optional[str] = None
    current_role: Optional[str] = None
    experience_years: Optional[float] = None
    current_ctc_lpa: Optional[float] = None
    expected_ctc_lpa: Optional[float] = None
    notice_period_days: Optional[int] = None
    graduation_department: Optional[str] = Field(default="Electronics and Communication Engineering", description="Graduation department")
    field_of_study: Optional[str] = Field(default="Electronics and Communication Engineering", description="Major / Field of study")
    major: Optional[str] = Field(default="Electronics and Communication Engineering", description="Major specialization")
    education: EducationInfo = Field(default_factory=EducationInfo)

    # Professional domain & dynamic taxonomy
    detected_department: Optional[str] = Field(default=None, description="Inferred department from resume or skills")
    user_confirmed_department: Optional[str] = Field(default=None, description="User confirmed or manually selected department")
    industry: Optional[str] = Field(default=None, description="Industry sector (e.g. Fintech, Healthcare, Tech)")
    certifications: List[str] = Field(default_factory=list, description="Professional certifications (e.g. CPA, PMP, AWS)")
    licenses: List[str] = Field(default_factory=list, description="Professional regulatory licenses (e.g. Bar, Medical, PE)")
    experience_history: List[WorkExperience] = Field(default_factory=list, description="Structured work experience history")

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
            raw_personal = data.get("personal")
            if hasattr(raw_personal, "model_dump"):
                personal_dict = raw_personal.model_dump()
            elif isinstance(raw_personal, dict):
                personal_dict = dict(raw_personal)
            else:
                personal_dict = {}

            raw_prof = data.get("professional")
            if hasattr(raw_prof, "model_dump"):
                prof_dict = raw_prof.model_dump()
            elif isinstance(raw_prof, dict):
                prof_dict = dict(raw_prof)
            else:
                prof_dict = {}

            name = personal_dict.get("full_name") or data.get("name")
            current_role = prof_dict.get("designation") or data.get("current_role")
            
            exp = prof_dict.get("total_experience_years")
            if exp is None or (isinstance(exp, (int, float)) and exp == 0.0 and data.get("experience_years") is not None and data.get("experience_years") > 0):
                exp = data.get("experience_years")
            
            c_lpa = prof_dict.get("current_lpa")
            if c_lpa is None or (isinstance(c_lpa, (int, float)) and c_lpa == 0.0 and data.get("current_ctc_lpa") is not None and data.get("current_ctc_lpa") > 0):
                c_lpa = data.get("current_ctc_lpa")
                
            e_lpa = prof_dict.get("expected_lpa")
            if e_lpa is None or (isinstance(e_lpa, (int, float)) and e_lpa == 0.0 and data.get("expected_ctc_lpa") is not None and data.get("expected_ctc_lpa") > 0):
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

