import json
import pytest
from pathlib import Path
from services.memory_service import MemoryService, DEFAULT_MEMORY

def test_memory_service_initialization(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    assert mem_file.exists()
    assert service.get_preference("preferred_platforms") == ["linkedin", "naukri"]
    assert service.get_preference("auto_approve_applications") is False

def test_memory_service_update_preferences(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    service.set_preference("current_ctc_inr", 950000)
    service.set_preference("preferred_platforms", ["naukri"])
    
    # Verify in-memory and on-disk persistence
    assert service.get_preference("current_ctc_inr") == 950000
    assert service.get_preference("preferred_platforms") == ["naukri"]
    
    new_service = MemoryService(memory_path=mem_file)
    assert new_service.get_preference("current_ctc_inr") == 950000
    assert new_service.get_preference("preferred_platforms") == ["naukri"]

def test_memory_service_conversation_notes(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    test_note = "User prefers remote roles in Hyderabad."
    service.add_conversation_note(test_note)
    
    notes = service.get_conversation_notes()
    assert test_note in notes
    
    # Verify duplicate notes are ignored
    service.add_conversation_note(test_note)
    assert notes.count(test_note) == 1

def test_memory_service_session_history(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    session = {
        "applied_count": 3,
        "platform": "linkedin",
        "jobs": ["Job 1", "Job 2", "Job 3"]
    }
    service.record_session(session)
    
    history = service.get_session_history()
    assert len(history) == 1
    assert history[0]["applied_count"] == 3
    assert "timestamp" in history[0]

def test_memory_service_llm_context_prompt(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    service.set_preference("current_ctc_inr", 890000)
    service.set_preference("expected_ctc_inr", 1200000)
    service.set_preference("notice_period_days", 60)
    
    context = service.get_context_prompt_for_llm()
    assert "Current INR 890,000" in context
    assert "Expected INR 1,200,000" in context
    assert "60 days" in context

def test_memory_service_save_and_get_form_answer(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    # Save answer for a question
    service.save_form_answer("How many years of work experience do you have with Selenium?*", "3.9", "text")
    
    # 1. Exact match retrieval
    assert service.get_form_answer("How many years of work experience do you have with Selenium?*") == "3.9"
    # 2. Normalized match (different case / punctuation)
    assert service.get_form_answer("how many years of work experience do you have with selenium") == "3.9"
    # 3. Partial keyword boundary match
    assert service.get_form_answer("Selenium") == "3.9"

    # Save another field and check retrieval
    service.save_form_answer("Total IT Experience ?*", "3.9", "number")
    assert service.get_form_answer("Total IT Experience") == "3.9"
    assert service.get_form_answer("Total IT Experience ?*") == "3.9"

    # Check all saved answers
    all_answers = service.get_all_saved_form_answers()
    assert len(all_answers) >= 2

def test_memory_service_seed_from_profile(tmp_path: Path):
    from services.profile_loader import ProfileLoader
    profile = ProfileLoader().profile

    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    # Ensure saved_form_answers starts empty
    assert len(service.get_all_saved_form_answers()) == 0
    
    # Seed from candidate profile
    service.seed_from_profile(profile)
    
    # Verify core fields are seeded
    saved = service.get_all_saved_form_answers()
    assert len(saved) > 10
    
    # Check specific fields
    assert service.get_form_answer("Total IT Experience") == "4"
    assert service.get_form_answer("Selenium") == "4"
    assert service.get_form_answer("Core Java") == "4"
    assert service.get_form_answer("Rest Assured") == "4"
    assert service.get_form_answer("Notice Period") == "60"
    assert service.get_form_answer("race/ethnicity") == "Asian"
    assert service.get_form_answer("gender") == "Male"
    assert service.get_form_answer("legally authorized to work in country") == "Yes"
    assert service.get_form_answer("Product-based companies worked for") == "Enterprise Software / SaaS Product Company"
    assert service.get_form_answer("API Testing – Postman/Swagger/etc.") == "Advanced"
    assert service.get_form_answer("Frameworks worked with") == "Selenium"
    assert service.get_form_answer("Python Experience") == "Beginner"
    assert service.get_form_answer("PyScript experience") == "No experience"
    assert service.get_form_answer("Backend applications/services hosted on AWS") == "No experience"
    assert service.get_form_answer("require sponsorship") == "No"

@pytest.mark.asyncio
async def test_form_agent_uses_and_updates_memory_service(tmp_path: Path):
    from agents.form_agent import FormAgent
    from services.llm_service import LLMService
    from services.profile_loader import ProfileLoader
    from models.form import FormField, FormFieldType

    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    profile = ProfileLoader().profile
    
    # Pre-seed custom answer into memory
    service.save_form_answer("Custom Proprietary Tool Years", "5.5", "text")
    
    agent = FormAgent(llm_service=LLMService(provider="heuristic"), memory_service=service)
    
    field = FormField(
        field_id="custom_tool",
        label="How many years with Custom Proprietary Tool Years?",
        field_type=FormFieldType.TEXT
    )
    val, needs_hitl = await agent.resolve_field_value(field, profile)
    # Value should come directly from memory!
    assert val == "5.5"
    assert needs_hitl is False
    
    # Now resolve a new field and verify it gets saved to memory
    new_field = FormField(
        field_id="docker_exp",
        label="How many years of work experience do you have with Docker?*",
        field_type=FormFieldType.TEXT
    )
    val2, _ = await agent.resolve_field_value(new_field, profile)
    # Memory should now have docker saved
    saved_answer = service.get_form_answer("Docker")
    assert saved_answer is not None

def test_memory_service_multiline_label_matching(tmp_path: Path):
    mem_file = tmp_path / "test_memory.json"
    service = MemoryService(memory_path=mem_file)
    
    # Save a clean form answer
    service.save_form_answer("What level of professional experience do you have developing, executing and maintaining automated test suites", "Extensive use", "radio")
    
    # Test retrieval when DOM label has newlines, "Required", or screen-reader text
    multiline_label = "What level of professional experience do you have developing, executing and maintaining automated test suites\nRequired\nPlease respond truthfully."
    assert service.get_form_answer(multiline_label) == "Extensive use"
    
    # Another test with asterisk and extra whitespace
    assert service.get_form_answer("What level of professional experience do you have developing, executing and maintaining automated test suites?* \n Required") == "Extensive use"

