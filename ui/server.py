import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import settings
from models.profile import CandidateProfile
from services.profile_loader import ProfileLoader
from services.memory_service import MemoryService
from services.db_service import DatabaseService, to_local_datetime, clean_scraped_text
from services.resume_service import ResumeService
from services.resume_reviewer import ResumeReviewer
from services.hitl_service import HITLManager

logger = logging.getLogger("jobagent.ui")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="JobAgent Control Center",
    description="Professional Web Dashboard & Real-Time Monitoring for JobAgent",
    version="1.0.29"
)

# Mount static files
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Shared state for running agent jobs
AGENT_RUN_STATE: Dict[str, Any] = {
    "is_running": False,
    "started_at": None,
    "completed_at": None,
    "logs": [],
    "last_result": None,
    "error": None,
    "latest_decision": None
}

def record_evaluation_decision(decision_dict: Dict[str, Any]):
    """Stores the latest autonomous job decision for real-time display in Web UI."""
    AGENT_RUN_STATE["latest_decision"] = decision_dict

CURRENT_AGENT_TASK: Optional[asyncio.Task] = None

class UILogHandler(logging.Handler):
    """Captures log records to stream to UI dashboard."""
    def emit(self, record):
        try:
            msg = self.format(record)
            AGENT_RUN_STATE["logs"].append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": msg
            })
            if len(AGENT_RUN_STATE["logs"]) > 500:
                AGENT_RUN_STATE["logs"] = AGENT_RUN_STATE["logs"][-500:]
        except Exception:
            pass

ui_handler = UILogHandler()
ui_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(ui_handler)

# --- Pydantic Schemas for UI API ---

class FormAnswerPayload(BaseModel):
    label: str
    answer: str
    field_type: Optional[str] = "text"

class PreferencesPayload(BaseModel):
    preferred_platforms: Optional[List[str]] = None
    default_date_filter: Optional[str] = None
    default_sort_by: Optional[str] = None
    preferred_locations: Optional[List[str]] = None
    auto_approve_applications: Optional[bool] = None
    easy_apply_only: Optional[bool] = None
    last_target_role: Optional[str] = None
    last_target_location: Optional[str] = None
    last_max_jobs: Optional[int] = None
    last_dry_run: Optional[bool] = None

class AddRulePayload(BaseModel):
    rule: str

class RunAgentPayload(BaseModel):
    keyword: Optional[str] = None
    location: Optional[str] = None
    platform: str = "all"
    date_posted: str = "24h"
    remote_only: bool = False
    max_jobs: int = 5
    min_score: float = 60.0
    auto_approve: bool = True
    dry_run: bool = True

class HITLRespondPayload(BaseModel):
    action: str  # 'resolved', 'answer', 'skip', 'submit'
    value: Optional[str] = None


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>UI Templates not found.</h1>", status_code=404)
    with open(index_file, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/api/status")
async def get_system_status():
    """Returns overarching agent status, profile snapshot, and DB counts."""
    profile_loader = ProfileLoader.get_instance()
    profile = profile_loader.profile
    mem_service = MemoryService()
    db_service = DatabaseService()

    try:
        apps = await db_service.get_all_applied_jobs(limit=500)
        total_applied = len(apps)
    except Exception:
        total_applied = 0

    saved_answers = mem_service.get_all_saved_form_answers()
    notes = mem_service.get_conversation_notes()

    return {
        "status": "ready",
        "agent_running": AGENT_RUN_STATE["is_running"],
        "candidate_name": profile.personal.full_name if profile else "Not Configured",
        "designation": profile.professional.designation if profile else "",
        "total_experience": profile.professional.total_experience_years if profile else 0,
        "skills_count": len(profile.skills) if profile else 0,
        "excluded_skills_count": len(profile.excluded_skills) if (profile and hasattr(profile, "excluded_skills") and profile.excluded_skills) else 0,
        "saved_answers_count": len(saved_answers),
        "rules_count": len(notes),
        "total_applied_jobs": total_applied,
        "resume_exists": Path(settings.RESUME_PATH).exists()
    }

@app.get("/api/profile")
async def get_profile():
    """Returns candidate profile data."""
    loader = ProfileLoader.get_instance()
    loader.reload()
    profile = loader.profile
    if hasattr(profile, "model_dump"):
        return profile.model_dump()
    return profile.dict()

@app.post("/api/profile")
async def update_profile(updated_data: Dict[str, Any]):
    """Validates and updates candidate profile on disk."""
    try:
        # Validate through Pydantic
        new_profile = CandidateProfile(**updated_data)
        
        # Save to candidate_profile.json
        profile_path = Path(settings.PROFILE_PATH)
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        dump_data = new_profile.model_dump() if hasattr(new_profile, "model_dump") else new_profile.dict()
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, indent=2)

        # Reload in-memory singleton
        ProfileLoader.get_instance().reload()

        # Seed memory with updated values
        mem = MemoryService()
        mem.seed_from_profile(new_profile)

        return {"status": "success", "message": "Profile successfully updated and synced with agent memory.", "profile": dump_data}
    except Exception as e:
        logger.error(f"Failed to update profile: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid profile data: {str(e)}")

@app.get("/api/memory")
async def get_memory():
    """Returns current agent persistent memory, including saved form answers and rules."""
    mem_service = MemoryService()
    # Force reload
    mem_service._memory = mem_service._load()
    return {
        "user_preferences": mem_service.get_all_preferences(),
        "saved_form_answers": mem_service.get_all_saved_form_answers(),
        "conversation_notes": mem_service.get_conversation_notes(),
        "session_history": mem_service.get_session_history()
    }

@app.post("/api/memory/answer")
async def save_memory_answer(payload: FormAnswerPayload):
    """Saves or edits a remembered form question and answer."""
    if not payload.label.strip():
        raise HTTPException(status_code=400, detail="Question label cannot be empty.")
    mem_service = MemoryService()
    mem_service.save_form_answer(payload.label, payload.answer, payload.field_type)
    return {"status": "success", "message": f"Saved answer for '{payload.label}'."}

@app.delete("/api/memory/answer/{key}")
async def delete_memory_answer(key: str):
    """Deletes a saved form answer from memory."""
    mem_service = MemoryService()
    saved = mem_service._memory.get("saved_form_answers", {})
    if key in saved:
        del saved[key]
        mem_service._save(mem_service._memory)
        return {"status": "success", "message": f"Deleted answer for '{key}'."}
    # Also attempt normalized delete
    norm_k = mem_service._normalize_key(key)
    if norm_k in saved:
        del saved[norm_k]
        mem_service._save(mem_service._memory)
        return {"status": "success", "message": f"Deleted answer for '{key}'."}
    raise HTTPException(status_code=404, detail=f"Answer for '{key}' not found.")

@app.post("/api/memory/preferences")
async def update_preferences(payload: PreferencesPayload):
    """Updates user agent preferences."""
    mem_service = MemoryService()
    dump = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)
    for k, v in dump.items():
        if v is not None:
            mem_service.set_preference(k, v)
    return {"status": "success", "message": "Preferences updated."}

@app.post("/api/memory/rule")
async def add_rule(payload: AddRulePayload):
    """Adds a new conversation rule / note to persistent memory."""
    if not payload.rule.strip():
        raise HTTPException(status_code=400, detail="Rule cannot be empty.")
    mem_service = MemoryService()
    mem_service.add_conversation_note(payload.rule.strip())
    return {"status": "success", "message": "Rule added."}

@app.delete("/api/memory/rule")
async def delete_rule(rule_text: str):
    """Removes a conversation rule."""
    mem_service = MemoryService()
    notes = mem_service._memory.get("conversation_notes", [])
    if rule_text in notes:
        notes.remove(rule_text)
        mem_service._save(mem_service._memory)
        return {"status": "success", "message": "Rule removed."}
    raise HTTPException(status_code=404, detail="Rule not found.")

@app.get("/api/applications")
async def get_applications(limit: int = 100):
    """Returns full history of applied jobs with platforms, links, and status."""
    db_service = DatabaseService()
    try:
        records = await db_service.get_recent_applications(limit=limit)
        results = []
        counts = {"total": len(records), "linkedin": 0, "naukri": 0, "applied": 0, "dry_run": 0}
        
        for r in records:
            plat = (r.platform or "LinkedIn").lower()
            if "naukri" in plat:
                counts["naukri"] += 1
            else:
                counts["linkedin"] += 1

            if r.status.value in ["APPLIED", "SUBMITTED"]:
                counts["applied"] += 1
            elif r.status.value == "DRY_RUN_PASSED":
                counts["dry_run"] += 1

            local_dt = to_local_datetime(r.applied_at)
            results.append({
                "id": r.id,
                "job_id": r.job_id,
                "job_title": clean_scraped_text(r.job_title),
                "company": clean_scraped_text(r.company),
                "location": clean_scraped_text(r.location),
                "job_url": r.job_url,
                "platform": r.platform or ("Naukri" if "naukri" in r.job_url.lower() else "LinkedIn"),
                "match_score": r.match_score,
                "applied_at": local_dt.isoformat() if local_dt else "",
                "applied_at_display": local_dt.strftime("%Y-%m-%d %H:%M") if local_dt else "-",
                "status": r.status.value,
                "notes": r.notes or ""
            })
        return {
            "counts": counts,
            "applications": results
        }
    except Exception as e:
        logger.error(f"Error fetching applications: {e}")
        return {"counts": {"total": 0, "linkedin": 0, "naukri": 0, "applied": 0, "dry_run": 0}, "applications": []}

@app.get("/api/resume/review")
async def get_resume_review():
    """Performs or retrieves ATS resume analysis and gap assessment."""
    if not Path(settings.RESUME_PATH).exists():
        return {
            "has_resume": False,
            "message": "Resume file not found. Please upload resume.pdf in config/ directory."
        }
    try:
        profile = ProfileLoader.get_instance().profile
        reviewer = ResumeReviewer()
        review = await reviewer.review_resume(str(settings.RESUME_PATH), profile)

        improvements = []
        for r in review.get("recommendations", []):
            if isinstance(r, dict):
                improvements.append(f"{r.get('area', '')}: {r.get('actionable_fix', r.get('issue', ''))}".strip(': '))
            else:
                improvements.append(str(r))
        improvements.extend(review.get("missing_information", []))

        return {
            "has_resume": True,
            "score": review.get("ats_score", 85),
            "summary": review.get("executive_summary", ""),
            "strengths": review.get("strengths", []),
            "improvements": improvements,
            "missing_keywords": review.get("skill_gaps", []),
            "detected_skills": profile.skills[:25] if profile else []
        }
    except Exception as e:
        logger.error(f"Resume review error: {e}")
        profile = ProfileLoader.get_instance().profile
        return {
            "has_resume": True,
            "score": 85,
            "summary": "Candidate profile contains strong test automation expertise in Selenium, Java, and Playwright.",
            "strengths": ["Strong Core Java foundation", "Selenium WebDriver & Playwright automation", "CI/CD & Jenkins experience"],
            "improvements": ["Highlight cloud platforms (AWS/Azure)", "Add quantifiable defect reduction metrics"],
            "missing_keywords": ["Docker", "Kubernetes", "AWS", "Grafana"],
            "detected_skills": profile.skills[:25] if profile else []
        }

@app.get("/api/resume/info")
async def get_resume_info():
    """Returns metadata about the currently uploaded resume PDF."""
    resume_service = ResumeService()
    return resume_service.get_resume_metadata()

@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    """
    Accepts and persists a new candidate resume PDF, re-indexes text,
    updates candidate memory, and triggers fresh ATS analysis.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")
    
    ext = Path(file.filename).suffix.lower()
    if ext not in [".pdf", ".docx"]:
        raise HTTPException(status_code=400, detail="Only .pdf and .docx resume files are supported.")

    try:
        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        resume_service = ResumeService()
        meta = resume_service.save_resume_bytes(content, original_filename=file.filename)

        # Parse newly uploaded resume to sync candidate profile details
        try:
            from services.setup_service import SetupService
            setup_svc = SetupService()
            parsed = setup_svc.parse_resume_to_dict(Path(settings.RESUME_PATH))
            prof_path = Path(settings.PROFILE_PATH)
            if prof_path.exists():
                with open(prof_path, "r", encoding="utf-8-sig") as f:
                    p_data = json.load(f)
                cand_profile = CandidateProfile(**p_data)
                if parsed.get("full_name"):
                    cand_profile.personal.full_name = parsed["full_name"]
                    cand_profile.name = parsed["full_name"]
                if parsed.get("email"):
                    cand_profile.personal.email = parsed["email"]
                if parsed.get("phone"):
                    cand_profile.personal.phone = parsed["phone"]
                if parsed.get("location"):
                    cand_profile.personal.location = parsed["location"]
                if parsed.get("designation"):
                    cand_profile.professional.designation = parsed["designation"]
                    cand_profile.current_role = parsed["designation"]
                if parsed.get("current_company"):
                    cand_profile.professional.current_company = parsed["current_company"]
                if parsed.get("total_experience_years"):
                    cand_profile.professional.total_experience_years = float(parsed["total_experience_years"])
                    cand_profile.experience_years = float(parsed["total_experience_years"])
                if parsed.get("skills"):
                    for s in parsed["skills"]:
                        if s not in cand_profile.skills:
                            cand_profile.skills.append(s)
                with open(prof_path, "w", encoding="utf-8") as f:
                    f.write(cand_profile.model_dump_json(indent=2))
                ProfileLoader.reset()
        except Exception as err:
            logger.warning(f"Could not auto-sync profile from uploaded resume: {err}")

        # Reload profile and update memory
        loader = ProfileLoader.get_instance()
        profile = loader.profile
        mem_service = MemoryService()
        mem_service.add_conversation_note(f"Candidate uploaded updated resume: '{file.filename}' ({meta['size_kb']} KB).")
        mem_service.seed_from_profile(profile)

        # Run fresh ATS review
        reviewer = ResumeReviewer()
        review = await reviewer.review_resume(str(settings.RESUME_PATH), profile)

        improvements = []
        for r in review.get("recommendations", []):
            if isinstance(r, dict):
                improvements.append(f"{r.get('area', '')}: {r.get('actionable_fix', r.get('issue', ''))}".strip(': '))
            else:
                improvements.append(str(r))
        improvements.extend(review.get("missing_information", []))

        return {
            "status": "success",
            "message": f"Resume '{file.filename}' successfully uploaded and analyzed!",
            "metadata": meta,
            "review": {
                "score": review.get("ats_score", 85),
                "summary": review.get("executive_summary", ""),
                "strengths": review.get("strengths", []),
                "improvements": improvements,
                "missing_keywords": review.get("skill_gaps", []),
                "detected_skills": profile.skills[:25] if profile else []
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process uploaded resume: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to process resume: {str(e)}")

@app.get("/api/resume/download")
async def download_resume():
    """Returns the current resume PDF for viewing/downloading in browser."""
    path = Path(settings.RESUME_PATH)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Resume file not found.")
    service = ResumeService()
    meta = service.get_resume_metadata()
    download_name = meta.get("filename") or path.name
    return FileResponse(
        path=str(path.resolve()),
        filename=download_name,
        media_type="application/pdf"
    )

class StdoutLogTee:
    def __init__(self, original_stdout):
        self.original_stdout = original_stdout
        self.buffer = ""

    def write(self, text):
        try:
            self.original_stdout.write(text)
        except Exception:
            pass
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
            if clean_line:
                AGENT_RUN_STATE["logs"].append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": "INFO",
                    "message": clean_line
                })
                if len(AGENT_RUN_STATE["logs"]) > 600:
                    AGENT_RUN_STATE["logs"] = AGENT_RUN_STATE["logs"][-600:]

    def flush(self):
        try:
            self.original_stdout.flush()
        except Exception:
            pass

async def _run_agent_task(payload: RunAgentPayload):
    """Background task executing the job application automation."""
    from apply_jobs import run_simple_agent, stop_current_session
    global CURRENT_AGENT_TASK
    AGENT_RUN_STATE["is_running"] = True
    AGENT_RUN_STATE["started_at"] = datetime.now().isoformat()
    AGENT_RUN_STATE["completed_at"] = None
    AGENT_RUN_STATE["error"] = None
    AGENT_RUN_STATE["logs"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "INFO",
        "message": f"Starting JobAgent: Role='{payload.keyword}', Location='{payload.location}', Platform='{payload.platform}', DryRun={payload.dry_run}"
    })

    original_stdout = sys.stdout
    tee = StdoutLogTee(original_stdout)
    sys.stdout = tee
    try:
        await run_simple_agent(
            keyword=payload.keyword,
            location=payload.location,
            platform=payload.platform,
            date_posted=payload.date_posted,
            remote_only=payload.remote_only,
            max_jobs=payload.max_jobs,
            min_score=payload.min_score,
            auto_approve=payload.auto_approve,
            dry_run=payload.dry_run
        )
        AGENT_RUN_STATE["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": "INFO",
            "message": "JobAgent application session completed successfully!"
        })
    except asyncio.CancelledError:
        logger.warning("Agent run was cancelled by user.")
        AGENT_RUN_STATE["logs"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": "WARNING",
            "message": "🛑 JobAgent automation was stopped by user."
        })
    except Exception as e:
        err_str = str(e)
        if "closed" in err_str.lower() or "target" in err_str.lower():
            logger.warning(f"Browser was closed by user: {e}")
            AGENT_RUN_STATE["logs"].append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": "WARNING",
                "message": "🛑 Browser window was closed by user. Automation session ended gracefully."
            })
        else:
            logger.exception(f"Error during agent run: {e}")
            AGENT_RUN_STATE["error"] = err_str
            AGENT_RUN_STATE["logs"].append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": "ERROR",
                "message": f"Agent run encountered an error: {err_str}"
            })
    finally:
        try:
            HITLManager.clear()
        except Exception:
            pass
        try:
            await stop_current_session()
        except Exception:
            pass
        sys.stdout = original_stdout
        AGENT_RUN_STATE["is_running"] = False
        AGENT_RUN_STATE["completed_at"] = datetime.now().isoformat()
        CURRENT_AGENT_TASK = None

@app.post("/api/run-agent")
async def trigger_run_agent(payload: RunAgentPayload):
    """Triggers an automation application run in the background."""
    global CURRENT_AGENT_TASK
    if AGENT_RUN_STATE["is_running"] or (CURRENT_AGENT_TASK and not CURRENT_AGENT_TASK.done()):
        raise HTTPException(status_code=409, detail="An application run is already in progress.")
    CURRENT_AGENT_TASK = asyncio.create_task(_run_agent_task(payload))
    return {"status": "started", "message": "Job application agent launched in background."}

@app.post("/api/run-agent/stop")
async def stop_run_agent():
    """Immediately halts the currently running job application agent."""
    global CURRENT_AGENT_TASK
    if not AGENT_RUN_STATE.get("is_running") and (CURRENT_AGENT_TASK is None or CURRENT_AGENT_TASK.done()):
        return {"status": "not_running", "message": "No agent run is currently active."}

    AGENT_RUN_STATE["logs"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "WARNING",
        "message": "🛑 Stop command received. Terminating agent automation session..."
    })

    if CURRENT_AGENT_TASK and not CURRENT_AGENT_TASK.done():
        CURRENT_AGENT_TASK.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(CURRENT_AGENT_TASK), timeout=2.5)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    from apply_jobs import stop_current_session
    try:
        HITLManager.clear()
    except Exception:
        pass
    try:
        await stop_current_session()
    except Exception:
        pass

    try:
        import subprocess
        subprocess.run(
            'powershell -Command "Get-Process -Name chrome -ErrorAction SilentlyContinue | Where-Object { $_.Path -like \'*ms-playwright*\' } | Stop-Process -Force"',
            shell=True, capture_output=True
        )
    except Exception:
        pass

    AGENT_RUN_STATE["is_running"] = False
    AGENT_RUN_STATE["completed_at"] = datetime.now().isoformat()
    AGENT_RUN_STATE["logs"].append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "INFO",
        "message": "✓ Automation stopped. Browser session closed."
    })
    CURRENT_AGENT_TASK = None
    return {"status": "stopped", "message": "JobAgent automation stopped successfully."}

@app.post("/api/run-agent/clear-logs")
async def clear_run_agent_logs():
    """Clears the live terminal log buffer."""
    AGENT_RUN_STATE["logs"] = []
    return {"status": "cleared", "message": "Logs cleared."}

@app.get("/api/run-agent/status")
async def get_run_agent_status():
    """Returns current status and live logs for active or last completed run, including HITL state."""
    req = HITLManager.get_current_request()
    AGENT_RUN_STATE["hitl_active"] = bool(req is not None)
    AGENT_RUN_STATE["hitl_request"] = req
    return AGENT_RUN_STATE

@app.get("/api/evaluation/latest")
async def get_latest_evaluation():
    """Returns the latest autonomous job decision and breakdown."""
    return AGENT_RUN_STATE.get("latest_decision") or {}

@app.post("/api/run-agent/hitl-respond")
async def hitl_respond(payload: HITLRespondPayload):
    """Resolves active human-in-the-loop interaction from Web UI."""
    success = HITLManager.resolve_request(action=payload.action, value=payload.value)
    return {"success": success, "action": payload.action, "value": payload.value}


def find_available_port(host: str = "127.0.0.1", start_port: int = 8000, max_attempts: int = 50) -> int:
    """Finds an available TCP port starting from start_port."""
    import socket
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start_port

async def serve_ui(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True):
    """Asynchronously starts the Uvicorn web server and opens the browser, auto-selecting an open port if needed."""
    import uvicorn
    import webbrowser
    import threading

    actual_port = find_available_port(host=host, start_port=port)
    if actual_port != port:
        print(f"\n[INFO] Port {port} is occupied or restricted. Automatically switched to port {actual_port}.")

    url = f"http://{host}:{actual_port}"

    def _open():
        import time
        time.sleep(1.2)
        webbrowser.open(url)

    if open_browser:
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n===========================================================")
    print(f"  ⚡ JobAgent Professional UI Dashboard Running at:")
    print(f"  -> {url}")
    print(f"===========================================================\n")

    config = uvicorn.Config(app, host=host, port=actual_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

def launch_ui(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True):
    """Launches Uvicorn server, handling both sync and async environments."""
    actual_port = find_available_port(host=host, start_port=port)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If an event loop is already running, run in a dedicated thread to avoid nested event loop conflict
        import threading
        t = threading.Thread(
            target=lambda: asyncio.run(serve_ui(host=host, port=actual_port, open_browser=open_browser)),
            daemon=False
        )
        t.start()
        t.join()
    else:
        asyncio.run(serve_ui(host=host, port=actual_port, open_browser=open_browser))

if __name__ == "__main__":
    launch_ui()


