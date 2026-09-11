from typing import Optional
from pydantic import BaseModel, Field

class JobCardSummary(BaseModel):
    job_id: str = Field(..., description="LinkedIn Job ID")
    title: str = Field(..., description="Job Title from card")
    company: str = Field(..., description="Company Name from card")
    location: str = Field(..., description="Location string from card")
    is_easy_apply: bool = Field(default=False, description="Whether posting has Easy Apply badge")
    job_url: str = Field(..., description="Canonical or direct URL to the job posting")
    card_index: int = Field(default=0, description="Position index of the card on the current page")

class JobDetails(BaseModel):
    job_id: str
    title: str
    company: str
    location: str
    job_url: str
    is_easy_apply: bool = True
    description_text: str = Field(..., description="Full raw job description text")
    workplace_type: Optional[str] = Field(default=None, description="On-site, Hybrid, or Remote")
    seniority_level: Optional[str] = Field(default=None, description="Seniority level if available")
    employment_type: Optional[str] = Field(default=None, description="Full-time, Contract, etc.")
