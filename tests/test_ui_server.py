import pytest
from fastapi.testclient import TestClient
from ui.server import app

@pytest.fixture
def client():
    return TestClient(app)

def test_ui_index_html(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    assert "JobAgent" in response.text
    assert "Candidate Profile" in response.text

def test_ui_status_api(client: TestClient):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "ready"
    assert "candidate_name" in data
    assert "total_applied_jobs" in data

def test_ui_profile_api(client: TestClient):
    # GET
    res = client.get("/api/profile")
    assert res.status_code == 200
    prof = res.json()
    assert "personal" in prof
    assert "professional" in prof
    assert "skills" in prof
    assert isinstance(prof["skills"], list)

    # POST Update
    orig_designation = prof["professional"]["designation"]
    prof["professional"]["designation"] = "Lead QA Automation Engineer"
    
    post_res = client.post("/api/profile", json=prof)
    assert post_res.status_code == 200
    updated = post_res.json()
    assert updated["status"] == "success"
    assert updated["profile"]["professional"]["designation"] == "Lead QA Automation Engineer"

    # Revert back
    prof["professional"]["designation"] = orig_designation
    client.post("/api/profile", json=prof)

def test_ui_memory_api(client: TestClient):
    # GET
    res = client.get("/api/memory")
    assert res.status_code == 200
    mem = res.json()
    assert "saved_form_answers" in mem
    assert "user_preferences" in mem
    assert "conversation_notes" in mem

    # POST answer
    post_res = client.post("/api/memory/answer", json={
        "label": "Test Framework Tool Experience",
        "answer": "3.5",
        "field_type": "number"
    })
    assert post_res.status_code == 200
    assert post_res.json()["status"] == "success"

    # Verify answer exists in memory
    res2 = client.get("/api/memory")
    saved = res2.json()["saved_form_answers"]
    assert any("test framework tool" in k.lower() for k in saved.keys())

    # DELETE answer
    del_res = client.delete("/api/memory/answer/test framework tool experience")
    assert del_res.status_code == 200

def test_ui_rules_api(client: TestClient):
    # Add Rule
    add_res = client.post("/api/memory/rule", json={"rule": "Test UI Dashboard Policy Rule"})
    assert add_res.status_code == 200

    # Verify
    res = client.get("/api/memory")
    notes = res.json()["conversation_notes"]
    assert "Test UI Dashboard Policy Rule" in notes

    # Delete Rule
    del_res = client.delete("/api/memory/rule?rule_text=Test%20UI%20Dashboard%20Policy%20Rule")
    assert del_res.status_code == 200

def test_ui_applications_api(client: TestClient):
    res = client.get("/api/applications?limit=20")
    assert res.status_code == 200
    data = res.json()
    assert "counts" in data
    assert "applications" in data
    assert isinstance(data["applications"], list)

def test_ui_resume_review_api(client: TestClient):
    res = client.get("/api/resume/review")
    assert res.status_code == 200
    data = res.json()
    assert "score" in data
    assert data["score"] >= 0

def test_stdout_log_tee_and_run_agent_status(client: TestClient):
    import io
    from ui.server import StdoutLogTee, AGENT_RUN_STATE
    
    mock_stdout = io.StringIO()
    tee = StdoutLogTee(mock_stdout)
    tee.write("\x1b[32m[SUCCESS]\x1b[0m Job application test log\n")
    
    assert any("Job application test log" in log["message"] for log in AGENT_RUN_STATE["logs"])

    res = client.get("/api/run-agent/status")
    assert res.status_code == 200
    status_data = res.json()
    assert "logs" in status_data
    assert "is_running" in status_data

def test_ui_stop_run_agent_when_idle(client: TestClient):
    res = client.post("/api/run-agent/stop")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ["not_running", "stopped"]

def test_ui_clear_logs(client: TestClient):
    res = client.post("/api/run-agent/clear-logs")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "cleared"
    
    status_res = client.get("/api/run-agent/status")
    assert status_res.status_code == 200
    assert len(status_res.json()["logs"]) == 0

def test_ui_stop_run_agent_when_running(client: TestClient):
    import ui.server
    ui.server.AGENT_RUN_STATE["is_running"] = True
    try:
        res = client.post("/api/run-agent/stop")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "stopped"
        assert ui.server.AGENT_RUN_STATE["is_running"] is False
    finally:
        ui.server.AGENT_RUN_STATE["is_running"] = False

def test_ui_resume_info_and_download(client: TestClient):
    # GET /api/resume/info
    res = client.get("/api/resume/info")
    assert res.status_code == 200
    data = res.json()
    assert "exists" in data
    assert "filename" in data

    # GET /api/resume/download
    dl_res = client.get("/api/resume/download")
    assert dl_res.status_code in [200, 404]
    if dl_res.status_code == 200:
        assert dl_res.headers["content-type"] == "application/pdf"

def test_ui_resume_upload(client: TestClient, tmp_path):
    from pathlib import Path
    from config.settings import settings
    orig_path = Path(settings.RESUME_PATH)
    meta_path = orig_path.parent / "resume_meta.json"
    profile_path = Path(settings.PROFILE_PATH)

    orig_bytes = orig_path.read_bytes() if orig_path.exists() else None
    orig_meta_bytes = meta_path.read_bytes() if meta_path.exists() else None
    orig_profile_bytes = profile_path.read_bytes() if profile_path.exists() else None

    try:
        # Upload test PDF
        dummy_pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \ntrailer<</Size 3/Root 1 0 R>>\nstartxref\n101\n%%EOF"
        
        files = {
            "file": ("test_candidate_resume.pdf", dummy_pdf_content, "application/pdf")
        }
        res = client.post("/api/resume/upload", files=files)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "metadata" in data
        assert data["metadata"]["filename"] == "test_candidate_resume.pdf"
        assert "review" in data

        # Check GET /api/resume/info reflects uploaded filename
        info_res = client.get("/api/resume/info")
        assert info_res.status_code == 200
        assert info_res.json()["filename"] == "test_candidate_resume.pdf"
    finally:
        if orig_bytes is not None:
            orig_path.write_bytes(orig_bytes)
        if orig_meta_bytes is not None:
            meta_path.write_bytes(orig_meta_bytes)
        elif meta_path.exists():
            meta_path.unlink()
        if orig_profile_bytes is not None:
            profile_path.write_bytes(orig_profile_bytes)

def test_ui_hitl_status_and_respond(client: TestClient):
    import asyncio
    from services.hitl_service import HITLManager

    HITLManager.clear()
    res = client.get("/api/run-agent/status")
    assert res.status_code == 200
    data = res.json()
    assert data["hitl_active"] is False

    # Simulate an active HITL request
    fut = HITLManager.request_human_input(
        hitl_type="field_input",
        title="🔔 Human Input Required: Education Degree",
        message="Please select your degree",
        options=["Bachelor's Degree", "Master's Degree"],
        suggested_value="Bachelor's Degree",
        timeout_sec=90
    )

    # Status API should now report hitl_active == True
    res2 = client.get("/api/run-agent/status")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["hitl_active"] is True
    assert data2["hitl_request"]["title"] == "🔔 Human Input Required: Education Degree"
    assert data2["hitl_request"]["options"] == ["Bachelor's Degree", "Master's Degree"]

    # Submit response from Web UI
    respond_res = client.post("/api/run-agent/hitl-respond", json={
        "action": "answer",
        "value": "Bachelor's Degree"
    })
    assert respond_res.status_code == 200
    assert respond_res.json()["success"] is True

    # Verify future resolved
    assert fut.done()
    assert fut.result() == {"action": "answer", "value": "Bachelor's Degree"}

    # Status should now report hitl_active == False
    res3 = client.get("/api/run-agent/status")
    assert res3.status_code == 200
    assert res3.json()["hitl_active"] is False
    HITLManager.clear()

def test_ui_evaluation_latest_api(client: TestClient):
    from ui.server import record_evaluation_decision, AGENT_RUN_STATE
    sample_decision = {
        "job_title": "Senior QA Automation Engineer",
        "company": "Tech Corp",
        "decision": "APPLY",
        "match_score": 96.0,
        "technical_fit": 98.0,
        "experience_fit": 95.0,
        "role_fit": 96.0,
        "preference_fit": 100.0,
        "risk": 2.0,
        "matched_skills": ["Java", "Selenium", "TestNG"],
        "missing_skills": [],
        "mandatory_missing_skills": [],
        "incompatible_skills": [],
        "reason": "Strong match on all criteria.",
        "confidence": 95.0
    }
    record_evaluation_decision(sample_decision)

    res = client.get("/api/evaluation/latest")
    assert res.status_code == 200
    data = res.json()
    assert data["job_title"] == "Senior QA Automation Engineer"
    assert data["decision"] == "APPLY"
    assert data["match_score"] == 96.0

    # Also check run-agent/status
    res_status = client.get("/api/run-agent/status")
    assert res_status.status_code == 200
    assert res_status.json()["latest_decision"]["job_title"] == "Senior QA Automation Engineer"
    AGENT_RUN_STATE["latest_decision"] = None




