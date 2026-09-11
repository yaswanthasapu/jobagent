import json
import pytest
from pathlib import Path
from services.profile_loader import ProfileLoader
from models.profile import CandidateProfile

@pytest.fixture
def sample_profile_json(tmp_path: Path) -> Path:
    sample = {
        "name": "Jane Doe",
        "current_role": "QA Automation Engineer",
        "experience_years": 4.0,
        "current_ctc_lpa": 10.0,
        "expected_ctc_lpa": 15.0,
        "notice_period_days": 30,
        "target_roles": ["QA Automation Engineer", "Software Test Engineer", "SDET"],
        "skills": ["Selenium", "Playwright", "RestAssured", "Java"],
        "preferred_locations": ["Hyderabad", "Bangalore", "Remote"],
        "personal": {
            "full_name": "Jane Doe",
            "email": "jane.doe@example.com",
            "phone": "9876543210",
            "location": "Hyderabad, Telangana, India"
        },
        "professional": {
            "designation": "QA Automation Engineer",
            "total_experience_years": 4.0,
            "current_company": "Tech Corp",
            "current_lpa": 10.0,
            "expected_lpa": 15.0,
            "notice_period_days": 30
        }
    }
    p = tmp_path / "test_candidate_profile.json"
    p.write_text(json.dumps(sample), encoding="utf-8")
    return p

def test_profile_loader_loads_successfully(sample_profile_json: Path):
    loader = ProfileLoader(str(sample_profile_json))
    profile = loader.profile

    assert profile.personal.full_name == "Jane Doe"
    assert profile.personal.location == "Hyderabad, Telangana, India"
    assert profile.professional.designation == "QA Automation Engineer"
    assert profile.professional.total_experience_years == 4.0
    assert profile.professional.current_company == "Tech Corp"
    assert profile.professional.current_lpa == 10.0
    assert profile.professional.expected_lpa == 15.0
    assert profile.professional.notice_period_days == 30

def test_profile_skill_lookups(sample_profile_json: Path):
    loader = ProfileLoader(str(sample_profile_json))

    # Known skills
    assert loader.has_skill("Selenium") is True
    assert loader.has_skill("playwright") is True
    assert loader.has_skill("RestAssured") is True
    assert loader.has_skill("Java") is True

    # Experience years
    assert loader.get_skill_experience_years("Selenium") == 4.0
    assert loader.get_skill_experience_years("Playwright") == 4.0

    # Unknown skills
    assert loader.has_skill("Cobol") is False
    assert loader.get_skill_experience_years("Cobol") == 0.0

def test_profile_role_and_location_matching(sample_profile_json: Path):
    loader = ProfileLoader(str(sample_profile_json))

    assert loader.matches_role("QA Automation Engineer") == 1.0
    assert loader.matches_role("Software Test Engineer") == 1.0
    assert loader.matches_role("Chief Marketing Officer") < 0.5

    assert loader.matches_location("Hyderabad, India") is True
    assert loader.matches_location("Bangalore Urban") is True
    assert loader.matches_location("Remote") is True
