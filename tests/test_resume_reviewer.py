import pytest
from pathlib import Path
from models.profile import CandidateProfile
from services.profile_loader import ProfileLoader
from services.resume_reviewer import ResumeReviewer

@pytest.fixture
def profile() -> CandidateProfile:
    return ProfileLoader().profile

@pytest.fixture
def reviewer() -> ResumeReviewer:
    return ResumeReviewer()

def test_resume_reviewer_extract_text():
    reviewer = ResumeReviewer()
    resume_path = Path("config/resume.pdf")
    if resume_path.exists():
        text = reviewer.extract_text_from_pdf(str(resume_path))
        assert len(text) > 50
        assert "Yaswanth" in text or "Automation" in text or "@" in text

def test_resume_reviewer_heuristic_review(reviewer: ResumeReviewer, profile: CandidateProfile):
    sample_text = """
    Alex Taylor
    QA Automation Engineer
    Phone: 9876543210
    Skills: Selenium, Playwright, Java, TestNG
    Experience: 4 years working at Tech Innovations.
    """
    review = reviewer._heuristic_review(sample_text, profile)
    
    assert "ats_score" in review
    assert review["ats_score"] >= 70
    assert "executive_summary" in review
    assert "strengths" in review
    assert "missing_information" in review
    assert "recommendations" in review
    assert len(review["recommendations"]) > 0

@pytest.mark.asyncio
async def test_resume_reviewer_full_review(reviewer: ResumeReviewer, profile: CandidateProfile):
    resume_path = Path("config/resume.pdf")
    if not resume_path.exists():
        pytest.skip("resume.pdf not found in config/")

    review = await reviewer.review_resume(str(resume_path), profile)
    assert review["ats_score"] >= 50
    assert len(review["executive_summary"]) > 10
    assert isinstance(review["recommendations"], list)
