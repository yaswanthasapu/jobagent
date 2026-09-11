import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from agents.fsm_runner import FSMRunner, WorkflowState

def test_fsm_runner_init_state():
    runner = FSMRunner(
        keyword="SDET",
        location="Hyderabad",
        max_jobs=3,
        min_score=75.0,
        headless=True,
        dry_run=True,
        auto_approve=True
    )
    assert runner.state == WorkflowState.INIT
    assert runner.keyword == "SDET"
    assert runner.min_score == 75.0
    assert runner.dry_run is True

@pytest.mark.asyncio
async def test_fsm_init_step():
    runner = FSMRunner(
        headless=True,
        dry_run=True,
        auto_approve=True
    )
    await runner._handle_init()
    assert runner.state == WorkflowState.LAUNCH_BROWSER
    assert runner.profile_loader is not None
    assert runner.db_service is not None
    assert runner.job_evaluator is not None
    assert runner.form_agent is not None

    # Cleanup DB connection created during init
    if runner.db_service:
        await runner.db_service.close()
