import pytest
from pathlib import Path
from services.profile_loader import ProfileLoader

def test_profile_loader_loads_successfully():
    loader = ProfileLoader()
    profile = loader.profile

    assert profile.personal.full_name == "Yaswanth Asapu"
    assert profile.personal.location == "Hyderabad, Telangana, India"
    assert profile.professional.designation == "QA Automation Engineer"
    assert profile.professional.total_experience_years in [3.9, 4.0]
    assert profile.professional.current_company == "Magellanic-Cloud"
    assert profile.professional.current_lpa > 0
    assert profile.professional.expected_lpa > 0
    assert profile.professional.notice_period_days == 60

def test_profile_skill_lookups():
    loader = ProfileLoader()

    # Known skills
    assert loader.has_skill("Selenium") is True
    assert loader.has_skill("playwright") is True
    assert loader.has_skill("RestAssured") is True
    assert loader.has_skill("Java") is True

    # Experience years
    assert loader.get_skill_experience_years("Selenium") in [3.9, 4.0]
    assert loader.get_skill_experience_years("Playwright") in [3.9, 4.0]

    # Unknown skills
    assert loader.has_skill("Cobol") is False
    assert loader.get_skill_experience_years("Cobol") == 0.0

def test_profile_role_and_location_matching():
    loader = ProfileLoader()

    assert loader.matches_role("QA Automation Engineer") == 1.0
    assert loader.matches_role("Software Test Engineer") == 1.0
    assert loader.matches_role("Chief Marketing Officer") < 0.5

    assert loader.matches_location("Hyderabad, India") is True
    assert loader.matches_location("Bangalore Urban") is True
    assert loader.matches_location("Remote") is True
