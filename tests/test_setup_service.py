import pytest
from pathlib import Path
from services.setup_service import SetupService
from config.settings import BASE_DIR

def test_setup_service_instantiation():
    service = SetupService()
    assert service is not None
    assert service.memory_service is not None

def test_setup_service_parse_resume_to_dict():
    service = SetupService()
    resume_path = BASE_DIR / "config" / "resume.pdf"
    if resume_path.exists():
        parsed = service.parse_resume_to_dict(resume_path)
        assert isinstance(parsed, dict)
        assert "full_name" in parsed
        assert "email" in parsed
        assert "phone" in parsed
        assert parsed["total_experience_years"] >= 1.0

def test_setup_service_save_key_to_env(tmp_path: Path, monkeypatch):
    test_env = tmp_path / ".env"
    monkeypatch.setattr("services.setup_service.ENV_PATH", test_env)
    
    service = SetupService()
    test_key = "AIzaSyTestGeminiKey1234567890"
    service.save_api_key_to_env(test_key, provider="gemini")
    
    assert test_env.exists()
    content = test_env.read_text(encoding="utf-8")
    assert f"GEMINI_API_KEY={test_key}" in content
    
    # Update to another key
    new_key = "AIzaSyNewUpdatedKey9876543210"
    service.save_api_key_to_env(new_key, provider="gemini")
    updated_content = test_env.read_text(encoding="utf-8")
    assert f"GEMINI_API_KEY={new_key}" in updated_content
    assert test_key not in updated_content

def test_synthesize_target_roles():
    from services.setup_service import synthesize_target_roles
    
    # Java Backend engineer
    roles_backend = synthesize_target_roles("Software Engineer", ["Java", "Spring Boot", "MySQL", "REST API"])
    assert any("Backend" in r or "Java" in r for r in roles_backend)
    assert not any("QA" in r or "SDET" in r for r in roles_backend)
    
    # QA Automation engineer
    roles_qa = synthesize_target_roles("QA Engineer", ["Selenium", "Playwright", "TestNG", "Postman"])
    assert any("QA" in r or "SDET" in r or "Test" in r for r in roles_qa)
    
    # Frontend developer
    roles_fe = synthesize_target_roles("Frontend Developer", ["React", "TypeScript", "HTML", "CSS"])
    assert any("Frontend" in r or "UI" in r for r in roles_fe)

def test_parse_resume_empty_file(tmp_path: Path):
    service = SetupService()
    dummy_pdf = tmp_path / "empty.pdf"
    dummy_pdf.write_text("dummy")
    parsed = service.parse_resume_to_dict(dummy_pdf)
    assert parsed["suggested_target_roles"] == []
    assert parsed["current_ctc_inr"] is None
    assert parsed["expected_ctc_inr"] is None
