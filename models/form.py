from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class FormFieldType(str, Enum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    SELECT = "SELECT"
    RADIO = "RADIO"
    CHECKBOX = "CHECKBOX"
    FILE_UPLOAD = "FILE_UPLOAD"
    UNKNOWN = "UNKNOWN"

class FormField(BaseModel):
    field_id: str
    label: str
    field_type: FormFieldType
    options: List[str] = Field(default_factory=list, description="Available options for SELECT or RADIO")
    current_value: Optional[str] = None
    is_required: bool = False
    selector: Optional[str] = None
    is_sensitive: bool = False
    help_text: Optional[str] = None
    validation_error: Optional[str] = None

class FormStep(BaseModel):
    step_index: int = 0
    step_title: str = "Unknown"
    fields: List[FormField] = Field(default_factory=list)
    is_review_step: bool = False

class ApplicationSummary(BaseModel):
    job_id: str
    job_title: str
    company: str
    location: str
    match_score: float
    resume_file: str
    fields_filled: Dict[str, Any] = Field(default_factory=dict)
