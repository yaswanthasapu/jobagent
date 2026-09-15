import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from services.chat_agent import ChatAgent
from models.profile import CandidateProfile, PersonalInfo, ProfessionalInfo, JobPreferences

@pytest.fixture
def dummy_profile():
    return CandidateProfile(
        personal=PersonalInfo(
            full_name="Test Candidate",
            email="candidate@test.com",
            phone="9876543210",
            location="Hyderabad, India"
        ),
        professional=ProfessionalInfo(
            designation="QA Automation Engineer",
            total_experience_years=4.0,
            current_company="TechCorp",
            current_lpa=8.0,
            expected_lpa=12.0,
            notice_period_days=60
        ),
        skills=["Selenium", "Java", "TestNG", "Postman"],
        preferred_roles=["QA Automation Engineer", "SDET"],
        preferred_locations=["Hyderabad", "Remote"],
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True, require_human_approval=True)
    )

def test_chat_agent_initialization():
    agent = ChatAgent()
    assert agent.console is not None
    assert agent.memory_service is not None
    assert agent.profile_loader is not None

def test_heuristic_parse_search_linkedin():
    agent = ChatAgent()
    parsed = agent._heuristic_parse("Search for remote SDET jobs on LinkedIn posted in the last 24 hours")
    assert parsed["intent"] == "search_and_apply"
    params = parsed["search_params"]
    assert params["platform"] == "linkedin"
    assert params["keyword"] == "Sdet"
    assert params["date_posted"] == "24h"
    assert params["remote_only"] is True

def test_heuristic_parse_search_naukri_dry_run():
    agent = ChatAgent()
    parsed = agent._heuristic_parse("Find QA Lead jobs in Bangalore on naukri in dry run mode applying to 3 jobs")
    assert parsed["intent"] == "search_and_apply"
    params = parsed["search_params"]
    assert params["platform"] == "naukri"
    assert params["keyword"] == "Qa Lead"
    assert params["location"] == "Bangalore"
    assert params["dry_run"] is True
    assert params["max_jobs"] == 3

def test_heuristic_parse_profile_update():
    agent = ChatAgent()
    parsed = agent._heuristic_parse("Update my expected CTC to 15 LPA and current CTC to 9.5 LPA and notice period to 30 days")
    assert parsed["intent"] == "update_profile"
    updates = parsed["profile_updates"]
    assert updates["expected_lpa"] == 15.0
    assert updates["expected_ctc_inr"] == 1500000
    assert updates["current_lpa"] == 9.5
    assert updates["current_ctc_inr"] == 950000
    assert updates["notice_period_days"] == 30

def test_heuristic_parse_add_skills():
    agent = ChatAgent()
    parsed = agent._heuristic_parse("Add Cypress, Playwright, and Docker to my skills")
    assert parsed["intent"] == "update_profile"
    updates = parsed["profile_updates"]
    assert "add_skills" in updates
    assert "Cypress" in updates["add_skills"]
    assert "Docker" in updates["add_skills"]

def test_heuristic_parse_memory_rule():
    agent = ChatAgent()
    parsed = agent._heuristic_parse("Remember: always select Yes for experience and profile questions")
    assert parsed["intent"] == "add_rule"
    assert "always select Yes for experience" in parsed["memory_rule"]

def test_heuristic_parse_status_and_exit():
    agent = ChatAgent()
    assert agent._heuristic_parse("show profile")["intent"] == "show_profile"
    assert agent._heuristic_parse("review resume")["intent"] == "review_resume"
    assert agent._heuristic_parse("history")["intent"] == "view_history"
    assert agent._heuristic_parse("exit")["intent"] == "exit"

def test_heuristic_parse_applied_jobs():
    agent = ChatAgent()
    assert agent._heuristic_parse("show applied jobs")["intent"] == "view_applied_jobs"
    assert agent._heuristic_parse("which jobs were applied till now?")["intent"] == "view_applied_jobs"
    assert agent._heuristic_parse("/applied")["intent"] == "view_applied_jobs"

@pytest.mark.asyncio
async def test_interpret_message_with_mocked_llm():
    mock_llm = MagicMock()
    mock_llm.can_use_llm.return_value = True
    mock_llm.call_gemini = AsyncMock(return_value=json.dumps({
        "intent": "search_and_apply",
        "search_params": {
            "keyword": "Senior SDET",
            "location": "Hyderabad",
            "platform": "all",
            "date_posted": "24h",
            "remote_only": False,
            "max_jobs": 5,
            "dry_run": False
        },
        "profile_updates": {},
        "memory_rule": None,
        "response_message": "Searching for Senior SDET in Hyderabad across all platforms."
    }))

    agent = ChatAgent(llm_service=mock_llm)
    result = await agent.interpret_message("Apply to senior sdet jobs in hyderabad on both platforms")
    assert result["intent"] == "search_and_apply"
    assert result["search_params"]["keyword"] == "Senior SDET"
    assert result["search_params"]["platform"] == "all"

def test_apply_profile_updates(tmp_path, monkeypatch):
    test_profile_file = tmp_path / "candidate_profile.json"
    initial_profile = {
        "personal": {
            "full_name": "John Doe",
            "email": "john@example.com",
            "phone": "9999999999",
            "location": "Hyderabad"
        },
        "professional": {
            "designation": "QA Engineer",
            "total_experience_years": 3.0,
            "current_company": "OldCorp",
            "current_lpa": 6.0,
            "expected_lpa": 10.0,
            "notice_period_days": 60
        },
        "skills": ["Selenium", "Java"],
        "preferred_roles": ["QA Engineer"],
        "preferred_locations": ["Hyderabad"],
        "job_preferences": {
            "minimum_match_score": 60.0,
            "easy_apply_only": True,
            "require_human_approval": True
        }
    }
    with open(test_profile_file, "w", encoding="utf-8") as f:
        json.dump(initial_profile, f)

    import services.chat_agent as ca_mod
    monkeypatch.setattr(ca_mod, "PROFILE_PATH", test_profile_file)

    from services.memory_service import MemoryService
    test_memory_file = tmp_path / "agent_memory.json"
    mem_service = MemoryService(memory_path=test_memory_file)

    agent = ChatAgent(memory_service=mem_service)
    changes = agent.apply_profile_updates({
        "expected_lpa": 14.0,
        "notice_period_days": 30,
        "add_skills": ["Playwright", "Python"]
    })

    assert len(changes) == 3
    with open(test_profile_file, "r", encoding="utf-8") as f:
        updated = json.load(f)

    assert updated["professional"]["expected_lpa"] == 14.0
    assert updated["professional"]["notice_period_days"] == 30
    assert "Playwright" in updated["skills"]
    assert "Python" in updated["skills"]

@pytest.mark.asyncio
async def test_execute_search_and_apply_does_not_raise_name_error(tmp_path, monkeypatch):
    import rich.prompt
    from unittest.mock import AsyncMock

    test_profile_file = tmp_path / "candidate_profile.json"
    test_profile = {
        "name": "Satya",
        "personal": {"full_name": "Satya", "email": "satya@example.com"},
        "professional": {"designation": "Software Engineer", "total_experience_years": 4.0},
        "skills": ["Java", "Spring"],
        "preferred_roles": ["Software Engineer"],
        "preferred_locations": ["Hyderabad"]
    }
    test_profile_file.write_text(json.dumps(test_profile), encoding="utf-8")

    import services.chat_agent as ca_mod
    monkeypatch.setattr(ca_mod, "PROFILE_PATH", test_profile_file)
    monkeypatch.setattr(rich.prompt.Prompt, "ask", lambda *args, **kwargs: "2")
    monkeypatch.setattr(rich.prompt.Confirm, "ask", lambda *args, **kwargs: False)

    agent = ChatAgent()
    # Should complete confirmation table display without NameError on target_dry_run
    await agent.execute_search_and_apply({"keyword": "Fullstack", "platform": "linkedin"})
