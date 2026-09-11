import os
import pytest
from datetime import datetime, timezone
from models.application import ApplicationRecord, ApplicationStatus
from services.db_service import DatabaseService

async def create_test_db(tmp_path) -> DatabaseService:
    db_file = tmp_path / "test_applications.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    service = DatabaseService(db_url=db_url)
    await service.init_db()
    return service

@pytest.mark.asyncio
async def test_db_init_and_empty_check(tmp_path):
    temp_db = await create_test_db(tmp_path)
    try:
        applied = await temp_db.is_already_applied(job_id="test_101", job_url="http://example.com/1", company="Acme")
        assert applied is False
    finally:
        await temp_db.close()

@pytest.mark.asyncio
async def test_db_record_and_deduplication(tmp_path):
    temp_db = await create_test_db(tmp_path)
    try:
        record = ApplicationRecord(
            job_id="job_linkedin_999",
            job_title="QA Automation Engineer",
            company="GlobalTech",
            location="Hyderabad",
            job_url="https://www.linkedin.com/jobs/view/999",
            match_score=85.5,
            applied_at=datetime.now(timezone.utc),
            status=ApplicationStatus.SUBMITTED,
            notes="Applied via test"
        )

        saved = await temp_db.record_application(record)
        assert saved.id is not None
        assert saved.job_id == "job_linkedin_999"

        # Verify duplicate detection by job_id
        is_dup_id = await temp_db.is_already_applied(job_id="job_linkedin_999")
        assert is_dup_id is True

        # Verify duplicate detection by url + company
        is_dup_url = await temp_db.is_already_applied(
            job_id="random_other_id",
            job_url="https://www.linkedin.com/jobs/view/999?ref=xyz",
            company="GlobalTech"
        )
        assert is_dup_url is True

        # Check history retrieval
        history = await temp_db.get_recent_applications()
        assert len(history) == 1
        assert history[0].job_id == "job_linkedin_999"
        assert history[0].match_score == 85.5
    finally:
        await temp_db.close()

@pytest.mark.asyncio
async def test_db_get_all_applied_jobs_and_table_display(tmp_path):
    temp_db = await create_test_db(tmp_path)
    try:
        # Record 1 LinkedIn job
        await temp_db.record_application(ApplicationRecord(
            job_id="li_101",
            job_title="Senior QA Automation Engineer",
            company="Amazon",
            location="Hyderabad",
            job_url="https://www.linkedin.com/jobs/view/101",
            platform="LinkedIn",
            match_score=92.0,
            applied_at=datetime.now(timezone.utc),
            status=ApplicationStatus.SUBMITTED,
            notes="Applied on LinkedIn"
        ))

        # Record 1 Naukri job
        await temp_db.record_application(ApplicationRecord(
            job_id="nk_202",
            job_title="Lead SDET",
            company="Microsoft",
            location="Bangalore",
            job_url="https://www.naukri.com/job-listings-202",
            platform="Naukri",
            match_score=88.0,
            applied_at=datetime.now(timezone.utc),
            status=ApplicationStatus.SUBMITTED,
            notes="Applied on Naukri"
        ))

        applied_jobs = await temp_db.get_all_applied_jobs()
        assert len(applied_jobs) == 2
        platforms = {j.platform for j in applied_jobs}
        assert "LinkedIn" in platforms
        assert "Naukri" in platforms

        # Test table display without error
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        test_console = Console(file=buf, color_system=None, width=120)
        await temp_db.display_applied_jobs_table(console=test_console)
        output = buf.getvalue()
        assert "Amazon" in output
        assert "Microsoft" in output
        assert "Jobs Applied Till Now" in output
    finally:
        await temp_db.close()

@pytest.mark.asyncio
async def test_db_migration_without_platform_column(tmp_path):
    import sqlite3
    db_file = tmp_path / "legacy_applications.db"
    
    # Create legacy table without platform column
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id VARCHAR(100) NOT NULL,
            job_title VARCHAR(255) NOT NULL,
            company VARCHAR(255) NOT NULL,
            location VARCHAR(255) NOT NULL,
            job_url VARCHAR(500) NOT NULL,
            match_score FLOAT NOT NULL,
            applied_at DATETIME NOT NULL,
            status VARCHAR(50) NOT NULL,
            notes TEXT
        )
    """)
    conn.execute("""
        INSERT INTO applications (job_id, job_title, company, location, job_url, match_score, applied_at, status, notes)
        VALUES ('legacy_1', 'Software Engineer', 'LegacyCorp', 'Remote', 'https://www.linkedin.com/jobs/view/123', 90.0, '2026-09-01 12:00:00', 'SUBMITTED', 'Legacy')
    """)
    conn.commit()
    conn.close()

    db_url = f"sqlite+aiosqlite:///{db_file}"
    service = DatabaseService(db_url=db_url)
    try:
        # Should automatically migrate and fetch without raising OperationalError
        records = await service.get_all_applied_jobs()
        assert len(records) == 1
        assert records[0].platform == "LinkedIn"
        assert records[0].company == "LegacyCorp"
    finally:
        await service.close()

def test_clean_scraped_text():
    from services.db_service import clean_scraped_text

    dirty = "QA Engineering\nQA Engineering with verification"
    assert clean_scraped_text(dirty) == "QA Engineering"

    dirty_dash = "AI \u2013 Junior/Mid QA Manual + Automation Engineer"
    assert clean_scraped_text(dirty_dash) == "AI - Junior/Mid QA Manual + Automation Engineer"

    dirty_badge = "Senior QA Automation Engineer with verification"
    assert clean_scraped_text(dirty_badge) == "Senior QA Automation Engineer"

    empty = ""
    assert clean_scraped_text(empty) == ""

def test_to_local_datetime():
    from services.db_service import to_local_datetime
    from datetime import datetime, timezone

    utc_dt = datetime(2026, 9, 10, 7, 47, 7, tzinfo=timezone.utc)
    local_dt = to_local_datetime(utc_dt)
    assert local_dt is not None
    assert local_dt.tzinfo is not None

    # Test naive datetime (as loaded from SQLite)
    naive_dt = datetime(2026, 9, 10, 7, 47, 7)
    local_from_naive = to_local_datetime(naive_dt)
    assert local_from_naive is not None
    assert local_from_naive.hour != 7 or local_from_naive.utcoffset().total_seconds() == 0

@pytest.mark.asyncio
async def test_applied_jobs_chronological_ordering(tmp_path):
    from datetime import datetime, timezone, timedelta
    from services.db_service import DatabaseService, ApplicationRecord, ApplicationStatus

    temp_db = await create_test_db(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        # Job 1 applied 2 hours ago
        await temp_db.record_application(ApplicationRecord(
            job_id="li_old",
            job_title="QA Tester (Old)",
            company="OldCorp",
            location="Remote",
            job_url="https://www.linkedin.com/jobs/view/old",
            match_score=80.0,
            applied_at=now - timedelta(hours=2),
            status=ApplicationStatus.SUBMITTED
        ))
        # Job 2 applied right now
        await temp_db.record_application(ApplicationRecord(
            job_id="li_new",
            job_title="QA Lead (New)",
            company="NewCorp",
            location="Remote",
            job_url="https://www.linkedin.com/jobs/view/new",
            match_score=95.0,
            applied_at=now,
            status=ApplicationStatus.SUBMITTED
        ))

        records = await temp_db.get_all_applied_jobs()
        assert len(records) == 2
        # Newest should be index 0
        assert records[0].job_id == "li_new"
        assert records[1].job_id == "li_old"
    finally:
        await temp_db.close()

