import pytest
from pathlib import Path
from agents.form_agent import FormAgent, QuestionCategory
from agents.job_evaluator import JobEvaluator
from models.application import ApplicationRecord, ApplicationStatus
from models.evaluation import DecisionType
from models.form import FormField, FormFieldType
from models.job import JobDetails
from models.profile import CandidateProfile
from services.db_service import DatabaseService as DBService
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.profile_loader import ProfileLoader

@pytest.fixture
def profile() -> CandidateProfile:
    return ProfileLoader().profile

@pytest.fixture
def evaluator() -> JobEvaluator:
    llm = LLMService(provider="heuristic")
    return JobEvaluator(llm_service=llm)

@pytest.fixture
def form_agent() -> FormAgent:
    llm = LLMService(provider="heuristic")
    return FormAgent(llm_service=llm, memory_service=MemoryService())


# 1. Strong Java/Selenium QA job -> APPLY
@pytest.mark.asyncio
async def test_decision_strong_java_selenium_qa_applies(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_java_sel_1",
        title="QA Automation Engineer",
        company="TechCorp Solutions",
        location="Hyderabad, Telangana, India",
        job_url="https://www.linkedin.com/jobs/view/job_java_sel_1",
        is_easy_apply=True,
        description_text="""
        We are seeking a QA Automation Engineer with 3-5 years experience.
        Required Skills: Core Java, Selenium WebDriver, TestNG, and REST Assured API Testing.
        SQL queries knowledge and Git version control.
        Location: Hyderabad.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.overall_score >= 75.0
    assert result.match_breakdown.technical_fit >= 80.0
    assert result.match_breakdown.experience_fit == 100.0


# 2. Python/PyTest mandatory job -> SKIP
@pytest.mark.asyncio
async def test_decision_python_pytest_mandatory_skips(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_py_mandatory",
        title="Python Automation Engineer",
        company="PyEnterprise",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_py_mandatory",
        is_easy_apply=True,
        description_text="""
        Mandatory requirement: 4+ years hands-on experience in Python and PyTest automation.
        Must have deep knowledge of Python scripting, PyUnit, and Robot Framework.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.SKIP, DecisionType.REJECT)
    assert "Python" in result.match_breakdown.reasoning or "incompatible" in result.match_breakdown.reasoning.lower()


# 3. C#/.NET mandatory job -> SKIP
@pytest.mark.asyncio
async def test_decision_csharp_dotnet_mandatory_skips(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_csharp_mandatory",
        title="C# SDET Automation Engineer",
        company="DotNet Corp",
        location="Bangalore",
        job_url="https://www.linkedin.com/jobs/view/job_csharp_mandatory",
        is_easy_apply=True,
        description_text="""
        Primary requirement: C# and .NET automation using SpecFlow and NUnit.
        Must have 4 years experience with C# automation frameworks.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.SKIP, DecisionType.REJECT)
    assert "C#" in result.match_breakdown.reasoning or "incompatible" in result.match_breakdown.reasoning.lower()


# 4. QA role with optional Python -> APPLY
@pytest.mark.asyncio
async def test_decision_qa_role_with_optional_python_applies(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_optional_py",
        title="QA Automation Engineer",
        company="GlobalTech",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_optional_py",
        is_easy_apply=True,
        description_text="""
        Seeking QA Engineer with 3 to 4 years experience.
        Requirements: Java, Selenium WebDriver, TestNG, SQL, API Testing.
        Good to have: Python scripting knowledge is a plus.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.overall_score >= 70.0


# 5. Candidate 3.9 yrs vs 4 year requirement -> APPLY
@pytest.mark.asyncio
async def test_decision_candidate_3_9_vs_4_year_req_applies(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_4yr_req",
        title="QA Automation Engineer",
        company="FinTech Services",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_4yr_req",
        is_easy_apply=True,
        description_text="""
        Requires 4 years of hands-on experience in test automation.
        Skills: Selenium, Java, TestNG, Jenkins.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.experience_fit >= 90.0


# 6. Candidate 3.9 yrs vs 7+ year requirement -> SKIP
@pytest.mark.asyncio
async def test_decision_candidate_3_9_vs_7_year_req_skips(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_7yr_req",
        title="Senior QA Lead / Architect",
        company="BigEnterprise",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_7yr_req",
        is_easy_apply=True,
        description_text="""
        Minimum 7+ years of experience leading QA automation frameworks.
        Architectural oversight and team management experience required.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.SKIP, DecisionType.REJECT)
    assert result.match_breakdown.experience_fit <= 30.0


# 7. Hyderabad role -> positive preference score
@pytest.mark.asyncio
async def test_decision_hyderabad_location_positive(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_hyd",
        title="QA Automation Engineer",
        company="CityTech",
        location="Hyderabad, Telangana, India",
        job_url="https://www.linkedin.com/jobs/view/job_hyd",
        is_easy_apply=True,
        description_text="Java, Selenium testing. Hyderabad office."
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.match_breakdown.preference_fit >= 75.0


# 8. Remote role -> positive preference score
@pytest.mark.asyncio
async def test_decision_remote_location_positive(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_remote",
        title="QA Automation Tester",
        company="CloudFirst Inc",
        location="Remote",
        job_url="https://www.linkedin.com/jobs/view/job_remote",
        is_easy_apply=True,
        description_text="100% Remote / Work from home position for QA Automation Engineer with Selenium and Java."
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.match_breakdown.preference_fit >= 80.0


# 9. Unknown relocation requirement -> REVIEW
@pytest.mark.asyncio
async def test_decision_unknown_relocation_reviews(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_reloc",
        title="QA Automation Engineer",
        company="RelocateCorp",
        location="Mumbai, Maharashtra, India",
        job_url="https://www.linkedin.com/jobs/view/job_reloc",
        is_easy_apply=True,
        description_text="""
        Candidate must be willing to relocate to Mumbai office on-site.
        Mandatory relocation required. Core Java and Selenium skills required.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.REVIEW, DecisionType.SKIP)


# 10. 60-day notice requirement -> APPLY
@pytest.mark.asyncio
async def test_decision_60_day_notice_applies(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_60_notice",
        title="QA Automation Engineer",
        company="CalmTech",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_60_notice",
        is_easy_apply=True,
        description_text="""
        Java, Selenium, TestNG. 3-4 years experience.
        Notice period: 60 days accepted.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY


# 11. 30-day joining requirement -> REVIEW
@pytest.mark.asyncio
async def test_decision_30_day_joining_reviews(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_30_notice",
        title="QA Automation Engineer",
        company="FastPaced Corp",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_30_notice",
        is_easy_apply=True,
        description_text="""
        Seeking QA Engineer with Java and Selenium.
        Notice period: 30 days notice required.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.REVIEW


# 12. Immediate joiner requirement -> REVIEW or SKIP
@pytest.mark.asyncio
async def test_decision_immediate_joiner_review_or_skip(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_imm_join",
        title="QA Automation Engineer",
        company="UrgentHire Inc",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_imm_join",
        is_easy_apply=True,
        description_text="""
        Urgent hiring: Immediate joiners only (0-15 days).
        Skills: Selenium, Java, SQL.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.REVIEW, DecisionType.SKIP)


# 13. Unknown mandatory application question -> USER_REQUIRED / REVIEW
@pytest.mark.asyncio
async def test_decision_unknown_mandatory_question_reviews(form_agent: FormAgent, profile: CandidateProfile):
    custom_field = FormField(
        field_id="custom_client_code",
        label="Please enter your internal client referral authorization code*",
        field_type=FormFieldType.TEXT,
        is_required=True
    )
    cat = form_agent.classify_question(custom_field)
    assert cat in (QuestionCategory.USER_REQUIRED, QuestionCategory.UNKNOWN)
    assert form_agent.is_user_required(custom_field) is True

    val, needs_hitl = await form_agent.resolve_field_value(custom_field, profile)
    assert needs_hitl is True


# 14. Race/Ethnicity -> USER_REQUIRED / SENSITIVE
def test_form_agent_race_ethnicity_user_required(form_agent: FormAgent):
    race_field = FormField(
        field_id="eeo_race",
        label="What is your race or ethnic background?",
        field_type=FormFieldType.SELECT,
        options=["Asian", "White", "Hispanic", "Decline to specify"]
    )
    cat = form_agent.classify_question(race_field)
    assert cat in (QuestionCategory.USER_REQUIRED, QuestionCategory.SENSITIVE)
    assert form_agent.is_user_required(race_field) is True


# 15. Gender -> USER_REQUIRED / SENSITIVE
def test_form_agent_gender_user_required(form_agent: FormAgent):
    gender_field = FormField(
        field_id="eeo_gender",
        label="Select your gender",
        field_type=FormFieldType.RADIO,
        options=["Male", "Female", "Prefer not to say"]
    )
    cat = form_agent.classify_question(gender_field)
    assert cat in (QuestionCategory.USER_REQUIRED, QuestionCategory.SENSITIVE)
    assert form_agent.is_user_required(gender_field) is True


# 16. Legal authorization -> USER_REQUIRED / LEGAL
def test_form_agent_legal_authorization_user_required(form_agent: FormAgent):
    legal_field = FormField(
        field_id="legal_work_auth",
        label="Are you legally authorized to work in this country without company sponsorship?",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    cat = form_agent.classify_question(legal_field)
    assert cat in (QuestionCategory.USER_REQUIRED, QuestionCategory.LEGAL)
    assert form_agent.is_user_required(legal_field) is True


# 17. Duplicate job -> ALREADY_PROCESSED
@pytest.mark.asyncio
async def test_decision_duplicate_job_already_processed(tmp_path: Path):
    db_file = tmp_path / "test_dedup.db"
    db = DBService(db_url=f"sqlite+aiosqlite:///{db_file.as_posix()}")
    await db.init_db()
    await db.record_application(ApplicationRecord(
        job_id="dup_12345",
        job_title="QA Automation Engineer",
        company="AlphaCorp",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/dup_12345",
        platform="LinkedIn",
        match_score=90.0,
        status=ApplicationStatus.SUBMITTED
    ))
    is_dup = await db.is_already_applied(job_id="dup_12345", job_url="https://www.linkedin.com/jobs/view/dup_12345", company="AlphaCorp")
    assert is_dup is True
    # Verify decision type enum exists and matches
    assert DecisionType.ALREADY_PROCESSED.value == "ALREADY_PROCESSED"


# 18. High title match but incompatible stack -> SKIP
@pytest.mark.asyncio
async def test_decision_high_title_match_incompatible_stack_skips(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_title_match_incomp",
        title="QA Automation Engineer", # 100% role match
        company="PythonShop",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_title_match_incomp",
        is_easy_apply=True,
        description_text="""
        Expertise in test automation.
        Job explicitly demands Python as primary programming language.
        Must have 4 years in Python, PyTest, and Robot Framework. No Java.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.SKIP, DecisionType.REJECT)
    assert "Python" in result.match_breakdown.reasoning or "incompatible" in result.match_breakdown.reasoning.lower()


# 19. High score but mandatory missing skill -> SKIP or REVIEW
@pytest.mark.asyncio
async def test_decision_high_score_mandatory_missing_skill_skips_or_reviews(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_missing_appium",
        title="Mobile QA Automation Engineer",
        company="MobileFirst App",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_missing_appium",
        is_easy_apply=True,
        description_text="""
        Seeking Mobile QA Automation Engineer.
        Mandatory primary requirement: 4 years experience in Appium mobile automation for iOS and Android.
        Appium is strictly mandatory for this position.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision in (DecisionType.SKIP, DecisionType.REVIEW, DecisionType.REJECT)


# 20. Strong skills + strong role + acceptable experience -> APPLY
@pytest.mark.asyncio
async def test_decision_strong_skills_role_experience_applies(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_perfect_match",
        title="QA Automation Engineer",
        company="Enterprise Tech",
        location="Hyderabad, Telangana, India",
        job_url="https://www.linkedin.com/jobs/view/job_perfect_match",
        is_easy_apply=True,
        description_text="""
        We are hiring a QA Automation Engineer with 3 to 5 years experience.
        Required Skills: Java, Selenium WebDriver, TestNG, REST Assured, API Testing, SQL, Git.
        Location: Hyderabad. Notice period: 60 days accepted. Salary: 12-15 LPA.
        """
    )
    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.overall_score >= 85.0
    assert result.match_breakdown.technical_fit >= 80.0
    assert result.match_breakdown.experience_fit == 100.0
    assert result.match_breakdown.role_fit >= 90.0

    dec_dict = result.to_decision_dict()
    assert dec_dict["decision"] == "APPLY"
    assert dec_dict["match_score"] >= 85.0
    assert len(dec_dict["matched_skills"]) >= 3


# 21. Job Card Prioritization & Sorting Test
def test_card_priority_ranking(evaluator: JobEvaluator, profile: CandidateProfile):
    from apply_jobs import compute_card_priority
    from models.job import JobCardSummary

    card_a = JobCardSummary(
        job_id="card_a",
        title="QA Automation Engineer",
        company="AlphaCorp",
        location="Hyderabad",
        job_url="https://linkedin.com/jobs/view/1"
    )
    card_b = JobCardSummary(
        job_id="card_b",
        title="Senior Software QA Tester",
        company="BetaCorp",
        location="Remote",
        job_url="https://linkedin.com/jobs/view/2"
    )
    card_low = JobCardSummary(
        job_id="card_low",
        title="Retail Sales Supervisor",
        company="GammaCorp",
        location="Delhi",
        job_url="https://linkedin.com/jobs/view/3"
    )

    rank_a, score_a = compute_card_priority(card_a, profile, "QA Automation Engineer", evaluator)
    rank_b, score_b = compute_card_priority(card_b, profile, "QA Automation Engineer", evaluator)
    rank_low, score_low = compute_card_priority(card_low, profile, "QA Automation Engineer", evaluator)

    # Priority A (tier 1) should rank before Low (tier 4)
    assert rank_a == 1
    assert rank_low == 4

    cards = [card_low, card_b, card_a]
    sorted_cards = sorted(cards, key=lambda c: compute_card_priority(c, profile, "QA Automation Engineer", evaluator))
    assert sorted_cards[0].job_id == "card_a"
    assert sorted_cards[-1].job_id == "card_low"

