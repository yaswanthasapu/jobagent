import pytest
from pathlib import Path
from agents.form_agent import FormAgent, QuestionCategory
from agents.job_evaluator import JobEvaluator
from models.evaluation import DecisionType
from models.form import FormField, FormFieldType
from models.job import JobDetails
from models.profile import CandidateProfile, PersonalInfo, ProfessionalInfo, JobPreferences, WorkExperience
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.setup_service import synthesize_target_roles

@pytest.fixture
def evaluator() -> JobEvaluator:
    llm = LLMService(provider="heuristic")
    return JobEvaluator(llm_service=llm)

@pytest.fixture
def form_agent() -> FormAgent:
    llm = LLMService(provider="heuristic")
    return FormAgent(llm_service=llm, memory_service=MemoryService())

@pytest.fixture
def qa_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Alex QA",
        current_role="QA Automation Engineer",
        experience_years=4.0,
        skills=["Selenium", "Java", "TestNG", "REST Assured", "Git", "SQL"],
        target_roles=["QA Automation Engineer", "SDET", "Test Automation Engineer"],
        preferred_roles=["QA Automation Engineer", "SDET"],
        preferred_locations=["Remote", "Hyderabad"],
        detected_department="Quality Assurance",
        personal=PersonalInfo(full_name="Alex QA", email="alex@example.com", phone="9876543210", location="Hyderabad, India"),
        professional=ProfessionalInfo(designation="QA Automation Engineer", total_experience_years=4.0, current_company="TechCorp"),
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True)
    )

@pytest.fixture
def finance_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Sarah Finance",
        current_role="Financial Analyst",
        experience_years=5.0,
        skills=["Financial Modeling", "Excel", "FP&A", "QuickBooks", "Budgeting", "Forecasting", "SAP"],
        target_roles=["Financial Analyst", "Senior Financial Analyst", "FP&A Analyst"],
        preferred_roles=["Financial Analyst", "FP&A Analyst"],
        preferred_locations=["Remote", "Mumbai"],
        detected_department="Finance",
        certifications=["CPA"],
        licenses=["Certified Public Accountant (CPA)"],
        personal=PersonalInfo(full_name="Sarah Finance", email="sarah@example.com", phone="9876543211", location="Mumbai, India"),
        professional=ProfessionalInfo(designation="Financial Analyst", total_experience_years=5.0, current_company="FinCorp"),
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True)
    )

@pytest.fixture
def marketing_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Jordan Marketer",
        current_role="Digital Marketing Specialist",
        experience_years=3.5,
        skills=["SEO", "SEM", "Google Ads", "Content Marketing", "Social Media Marketing", "Google Analytics", "Copywriting"],
        target_roles=["Digital Marketing Specialist", "Marketing Specialist", "Growth Marketer"],
        preferred_roles=["Digital Marketing Specialist", "Growth Marketer"],
        preferred_locations=["Remote", "Bangalore"],
        detected_department="Marketing",
        personal=PersonalInfo(full_name="Jordan Marketer", email="jordan@example.com", phone="9876543212", location="Bangalore, India"),
        professional=ProfessionalInfo(designation="Digital Marketing Specialist", total_experience_years=3.5, current_company="GrowthMedia"),
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True)
    )

@pytest.fixture
def ops_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Taylor Ops",
        current_role="Operations Manager",
        experience_years=6.0,
        skills=["Supply Chain", "Logistics", "Procurement", "Inventory Management", "Six Sigma", "Vendor Management"],
        target_roles=["Operations Manager", "Supply Chain Analyst", "Operations Specialist"],
        preferred_roles=["Operations Manager"],
        preferred_locations=["Remote", "Delhi"],
        detected_department="Operations",
        personal=PersonalInfo(full_name="Taylor Ops", email="taylor@example.com", phone="9876543213", location="Delhi, India"),
        professional=ProfessionalInfo(designation="Operations Manager", total_experience_years=6.0, current_company="Logix Global"),
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True)
    )

@pytest.fixture
def hr_profile() -> CandidateProfile:
    return CandidateProfile(
        name="Morgan HR",
        current_role="HR Generalist",
        experience_years=4.0,
        skills=["Talent Acquisition", "Recruiter", "Workday", "Onboarding", "Employee Relations", "Payroll", "HRIS"],
        target_roles=["HR Generalist", "Talent Acquisition Specialist", "Human Resources Specialist"],
        preferred_roles=["HR Generalist"],
        preferred_locations=["Remote", "Pune"],
        detected_department="Human Resources",
        personal=PersonalInfo(full_name="Morgan HR", email="morgan@example.com", phone="9876543214", location="Pune, India"),
        professional=ProfessionalInfo(designation="HR Generalist", total_experience_years=4.0, current_company="PeopleFirst"),
        job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True)
    )


# -------------------------------------------------------------
# 1. Multi-Department Autonomous Matching (APPLY Decisions)
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_qa_candidate_evaluates_qa_job(evaluator: JobEvaluator, qa_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_qa_101",
        title="QA Automation Engineer",
        company="GlobalTech",
        location="Hyderabad, India",
        job_url="https://www.linkedin.com/jobs/view/job_qa_101",
        is_easy_apply=True,
        description_text="""
        Looking for a QA Automation Engineer with 3-5 years of experience.
        Required: Core Java, Selenium WebDriver, TestNG, REST API testing, and Git.
        """
    )
    res = await evaluator.evaluate_fit(job, qa_profile)
    assert res.decision == DecisionType.APPLY
    assert res.match_breakdown.overall_score >= 75.0
    assert res.match_breakdown.role_fit >= 90.0


@pytest.mark.asyncio
async def test_finance_candidate_evaluates_finance_job(evaluator: JobEvaluator, finance_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_fin_101",
        title="Financial Analyst",
        company="Morgan & Partners",
        location="Mumbai, India",
        job_url="https://www.linkedin.com/jobs/view/job_fin_101",
        is_easy_apply=True,
        description_text="""
        We are seeking a Financial Analyst to join our team.
        Required: 3-5 years experience in Financial Modeling, FP&A, Budgeting, and Forecasting.
        Proficiency in Excel and QuickBooks or SAP.
        """
    )
    res = await evaluator.evaluate_fit(job, finance_profile)
    assert res.decision == DecisionType.APPLY
    assert res.match_breakdown.overall_score >= 70.0
    assert res.match_breakdown.role_fit >= 90.0


@pytest.mark.asyncio
async def test_marketing_candidate_evaluates_marketing_job(evaluator: JobEvaluator, marketing_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_mkt_101",
        title="Digital Marketing Specialist",
        company="GrowthWave",
        location="Bangalore, India",
        job_url="https://www.linkedin.com/jobs/view/job_mkt_101",
        is_easy_apply=True,
        description_text="""
        Hiring a Digital Marketing Specialist with 2-4 years experience.
        Required: SEO, SEM, Google Ads, Content Marketing, and Google Analytics.
        Must have strong copywriting skills.
        """
    )
    res = await evaluator.evaluate_fit(job, marketing_profile)
    assert res.decision == DecisionType.APPLY
    assert res.match_breakdown.overall_score >= 70.0
    assert res.match_breakdown.role_fit >= 90.0


@pytest.mark.asyncio
async def test_operations_candidate_evaluates_ops_job(evaluator: JobEvaluator, ops_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_ops_101",
        title="Operations Manager",
        company="Apex Supply Chain",
        location="Delhi, India",
        job_url="https://www.linkedin.com/jobs/view/job_ops_101",
        is_easy_apply=True,
        description_text="""
        Seeking an experienced Operations Manager (4-7 years).
        Required: Supply Chain management, Logistics, Procurement, and Vendor Management.
        Knowledge of Six Sigma processes and Inventory Management.
        """
    )
    res = await evaluator.evaluate_fit(job, ops_profile)
    assert res.decision == DecisionType.APPLY
    assert res.match_breakdown.overall_score >= 70.0
    assert res.match_breakdown.role_fit >= 90.0


@pytest.mark.asyncio
async def test_hr_candidate_evaluates_hr_job(evaluator: JobEvaluator, hr_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_hr_101",
        title="HR Generalist",
        company="InnoCorp",
        location="Pune, India",
        job_url="https://www.linkedin.com/jobs/view/job_hr_101",
        is_easy_apply=True,
        description_text="""
        Hiring an HR Generalist with 3-5 years experience.
        Required: Talent Acquisition, Recruiter experience, Onboarding, and Employee Relations.
        Experience with Workday or HRIS software is required.
        """
    )
    res = await evaluator.evaluate_fit(job, hr_profile)
    assert res.decision == DecisionType.APPLY
    assert res.match_breakdown.overall_score >= 70.0
    assert res.match_breakdown.role_fit >= 90.0


# -------------------------------------------------------------
# 2. Cross-Discipline Filtering & Precision Rejection (SKIP)
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_finance_candidate_rejects_qa_job(evaluator: JobEvaluator, finance_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_cross_qa",
        title="Senior QA Automation Engineer",
        company="TechCorp",
        location="Mumbai, India",
        job_url="https://www.linkedin.com/jobs/view/job_cross_qa",
        is_easy_apply=True,
        description_text="""
        Looking for a Senior QA Automation Engineer with Selenium, Java, and TestNG.
        """
    )
    res = await evaluator.evaluate_fit(job, finance_profile)
    assert res.decision == DecisionType.SKIP
    assert res.match_breakdown.role_fit <= 25.0
    assert "discipline mismatch" in res.match_breakdown.reasoning.lower() or "mismatch" in res.match_breakdown.reasoning.lower()


@pytest.mark.asyncio
async def test_qa_candidate_rejects_finance_job(evaluator: JobEvaluator, qa_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_cross_fin",
        title="Senior Financial Analyst",
        company="FinBank",
        location="Hyderabad, India",
        job_url="https://www.linkedin.com/jobs/view/job_cross_fin",
        is_easy_apply=True,
        description_text="""
        Looking for a Senior Financial Analyst with Financial Modeling, FP&A, and QuickBooks.
        """
    )
    res = await evaluator.evaluate_fit(job, qa_profile)
    assert res.decision == DecisionType.SKIP
    assert res.match_breakdown.role_fit <= 25.0


@pytest.mark.asyncio
async def test_marketing_candidate_rejects_engineering_job(evaluator: JobEvaluator, marketing_profile: CandidateProfile):
    job = JobDetails(
        job_id="job_cross_mech",
        title="Mechanical Design Engineer",
        company="AeroDynamic Ltd",
        location="Bangalore, India",
        job_url="https://www.linkedin.com/jobs/view/job_cross_mech",
        is_easy_apply=True,
        description_text="""
        Seeking a Mechanical Design Engineer with SolidWorks, AutoCAD, and FEA experience.
        """
    )
    res = await evaluator.evaluate_fit(job, marketing_profile)
    assert res.decision == DecisionType.SKIP
    assert res.match_breakdown.role_fit <= 25.0


# -------------------------------------------------------------
# 3. Requirement Hierarchy (Mandatory vs Optional)
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_mandatory_requirement_skips(evaluator: JobEvaluator, finance_profile: CandidateProfile):
    # Candidate lacks mandatory "Tableau" and it's flagged as required skill not in profile
    job = JobDetails(
        job_id="job_fin_mandatory",
        title="Financial Analyst",
        company="FinCorp",
        location="Mumbai, India",
        job_url="https://www.linkedin.com/jobs/view/job_fin_mandatory",
        is_easy_apply=True,
        description_text="""
        Looking for a Financial Analyst with 10-15 years experience in Investment Banking.
        Mandatory requirements: 10+ years of Investment Banking experience.
        """
    )
    res = await evaluator.evaluate_fit(job, finance_profile)
    # Experience mismatch (candidate has 5 years, job requires 10-15 years)
    assert res.decision == DecisionType.SKIP
    assert res.match_breakdown.experience_fit < 50.0


# -------------------------------------------------------------
# 4. FormAgent Regulatory & Licensing Verification
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_regulatory_license_verified_answers_yes(form_agent: FormAgent, finance_profile: CandidateProfile):
    field = FormField(
        field_id="cpa_license",
        label="Do you hold an active Certified Public Accountant (CPA) license?",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    # Finance candidate has CPA in licenses
    val, needs_hitl = await form_agent.resolve_field_value(field, finance_profile)
    assert val.lower() == "yes"
    assert not needs_hitl
    assert not form_agent.is_user_required(field, finance_profile)


@pytest.mark.asyncio
async def test_regulatory_license_unverified_requires_user(form_agent: FormAgent, finance_profile: CandidateProfile):
    field = FormField(
        field_id="bar_license",
        label="Are you actively admitted to the California State Bar or licensed attorney?",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    # Finance candidate does not have State Bar license
    val, needs_hitl = await form_agent.resolve_field_value(field, finance_profile)
    assert needs_hitl is True
    assert form_agent.is_user_required(field, finance_profile) is True


# -------------------------------------------------------------
# 5. FormAgent Cross-Domain Experience Matching
# -------------------------------------------------------------

def test_form_agent_cross_domain_experience_calculation(form_agent: FormAgent, finance_profile: CandidateProfile):
    # Testing experience resolution for finance skills
    years = form_agent._match_skill_experience("financial modeling", finance_profile)
    assert years == 5.0

    # Secondary general tools (Excel, Jira, Office) are calibrated to min(3.0, total_exp)
    years_excel = form_agent._match_skill_experience("excel", finance_profile)
    assert years_excel == 3.0

    # Skill not in candidate profile
    years_unknown = form_agent._match_skill_experience("kubernetes", finance_profile)
    assert years_unknown == 0.0


# -------------------------------------------------------------
# 6. Universal Target Roles Synthesis
# -------------------------------------------------------------

def test_synthesize_target_roles_across_all_departments():
    # Finance
    fin_roles = synthesize_target_roles("Financial Analyst", ["Financial Modeling", "FP&A", "Accounting", "QuickBooks"])
    assert any("Financial Analyst" in r or "FP&A" in r or "Finance" in r for r in fin_roles)

    # Marketing
    mkt_roles = synthesize_target_roles("Digital Marketer", ["SEO", "Google Ads", "Content Marketing", "Copywriting"])
    assert any("Marketing" in r or "Growth" in r for r in mkt_roles)

    # HR
    hr_roles = synthesize_target_roles("HR Recruiter", ["Talent Acquisition", "Workday", "Recruiting", "Payroll"])
    assert any("HR" in r or "Recruiter" in r or "Human Resources" in r for r in hr_roles)

    # Operations
    ops_roles = synthesize_target_roles("Operations Lead", ["Supply Chain", "Logistics", "Procurement", "Inventory"])
    assert any("Operations" in r or "Supply Chain" in r for r in ops_roles)

    # Engineering (Mechanical)
    eng_roles = synthesize_target_roles("Mechanical Engineer", ["SolidWorks", "AutoCAD", "Thermodynamics"])
    assert any("Mechanical Engineer" in r or "Design Engineer" in r for r in eng_roles)

    # Product
    prod_roles = synthesize_target_roles("Product Owner", ["Product Management", "Scrum Master", "Agile", "Roadmapping"])
    assert any("Product Manager" in r or "Scrum Master" in r for r in prod_roles)

    # UI/UX
    ux_roles = synthesize_target_roles("UX Designer", ["Figma", "Wireframing", "User Research", "Prototyping"])
    assert any("UX" in r or "Designer" in r for r in ux_roles)
