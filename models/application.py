from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class ApplicationStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    APPLIED = "APPLIED"
    DRY_RUN_PASSED = "DRY_RUN_PASSED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_LOW_SCORE = "SKIPPED_LOW_SCORE"
    SKIPPED_MANUAL_REQUIRED = "SKIPPED_MANUAL_REQUIRED"
    REJECTED_HITL = "REJECTED_HITL"
    FAILED = "FAILED"

class ApplicationRecord(BaseModel):
    id: Optional[int] = None
    job_id: str
    job_title: str
    company: str
    location: str
    job_url: str
    platform: Optional[str] = "LinkedIn"
    match_score: float
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApplicationStatus = ApplicationStatus.SUBMITTED
    notes: Optional[str] = None
