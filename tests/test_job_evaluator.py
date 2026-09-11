import pytest
from models.evaluation import DecisionType
from models.job import JobDetails
from models.profile import CandidateProfile
from services.llm_service import LLMService
from services.profile_loader import ProfileLoader
from agents.job_evaluator import JobEvaluator

@pytest.fixture
def profile() -> CandidateProfile:
    loader = ProfileLoader()
    p = loader.profile.model_copy(deep=True)
    p.preferred_roles = ["QA Automation Engineer", "SDET", "Software Engineer"]
    return p

@pytest.fixture
def evaluator() -> JobEvaluator:
    # Heuristic/offline LLM service for fast, deterministic unit testing
    llm_service = LLMService(provider="heuristic")
    return JobEvaluator(llm_service)

@pytest.mark.asyncio
async def test_high_fit_job_evaluation(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_high_fit_1",
        title="QA Automation Engineer",
        company="TechCorp Solutions",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_high_fit_1",
        is_easy_apply=True,
        description_text="""
        We are seeking a QA Automation Engineer with 3-5 years of experience.
        Required Skills:
        - Hands-on experience with Playwright or Selenium in Java.
        - API testing using Postman and RestAssured.
        - Experience with CI/CD tools such as Jenkins and Git.
        - Solid SQL and database querying skills.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.overall_score >= 70.0
    assert result.match_breakdown.experience_score >= 80.0
    assert "Playwright" in result.match_breakdown.matched_skills or "Selenium" in result.match_breakdown.matched_skills

@pytest.mark.asyncio
async def test_overqualified_experience_rejection(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_director_exp",
        title="Director of Quality Engineering",
        company="Global Enterprises",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_director_exp",
        is_easy_apply=True,
        description_text="""
        Seeking a QA Leader with minimum 12+ years of experience in test engineering leadership.
        Experience scaling automation orgs, managing 20+ SDETs.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision == DecisionType.REJECT
    assert result.match_breakdown.experience_score <= 20.0
    assert "exceeds" in result.match_breakdown.experience_analysis.lower()

@pytest.mark.asyncio
async def test_unrelated_role_low_score(evaluator: JobEvaluator, profile: CandidateProfile):
    job = JobDetails(
        job_id="job_unrelated_1",
        title="Senior Frontend React Developer",
        company="DesignPixel",
        location="Remote",
        job_url="https://www.linkedin.com/jobs/view/job_unrelated_1",
        is_easy_apply=True,
        description_text="""
        Looking for a Senior Frontend Developer with 5+ years of React, Next.js, Redux, Tailwind CSS, GraphQL.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision in [DecisionType.SKIP, DecisionType.REJECT]
    assert result.match_breakdown.overall_score < profile.job_preferences.minimum_match_score

@pytest.mark.asyncio
async def test_semantic_skill_equivalence(evaluator: JobEvaluator, profile: CandidateProfile):
    # Job requires "REST API Testing" and "Webdriver", which should semantically match
    # RestAssured/Postman and Selenium/Playwright in the candidate profile
    job = JobDetails(
        job_id="job_semantic_1",
        title="SDET - Test Automation",
        company="Fintech Systems",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_semantic_1",
        is_easy_apply=True,
        description_text="""
        Looking for an SDET with 4 years of experience.
        Must have strong skills in API testing and web automation.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)
    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.skill_score >= 60.0

def test_role_score_mismatch_filtering(evaluator: JobEvaluator):
    target_roles = ["QA Automation Engineer", "SDET", "Software Test Engineer"]
    
    # Irrelevant disciplines should receive a score <= 25.0 to trigger card pre-filtering
    assert evaluator._compute_role_score("Java Fullstack Developer", target_roles) <= 25.0
    assert evaluator._compute_role_score("Site Reliability Engineer", target_roles) <= 25.0
    assert evaluator._compute_role_score("Senior Cloud & Infrastructure Engineer", target_roles) <= 25.0
    assert evaluator._compute_role_score("Backend Engineer (Go/Java)", target_roles) <= 25.0

    # Relevant roles should receive high scores >= 90.0
    assert evaluator._compute_role_score("QA Automation Engineer", target_roles) == 100.0
    assert evaluator._compute_role_score("Senior SDET Automation", target_roles) >= 90.0

@pytest.mark.asyncio
async def test_llm_job_fit_fallback_heuristics(profile: CandidateProfile):
    llm = LLMService(provider="heuristic")
    
    # Analyze a mismatched job (Java Fullstack) for QA candidate
    res = await llm.analyze_job_fit_with_llm(
        job_title="Java Fullstack Developer",
        company="Enterprise Corp",
        job_description="Seeking a Fullstack Java developer with Spring Boot and React.",
        candidate_profile=profile.model_dump() if hasattr(profile, "model_dump") else profile.dict()
    )
    
    # Must NOT return fake 88! Must be penalised
    assert res["fit_score"] <= 40
    assert "mismatch" in res["match_summary"].lower() or "role" in res["match_summary"].lower()

@pytest.mark.asyncio
async def test_skip_when_skills_do_not_match_even_with_matching_title(evaluator: JobEvaluator, profile: CandidateProfile):
    """
    Candidate is Java/Selenium/Playwright.
    Job title is 'QA Automation Engineer' but JD specifically requires Python & PyTest.
    Agent must SKIP the job and identify missing skills.
    """
    job = JobDetails(
        job_id="job_python_mismatch",
        title="QA Automation Engineer",
        company="PyCloud Systems",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_python_mismatch",
        is_easy_apply=True,
        description_text="""
        We are hiring a QA Automation Engineer with 3-5 years experience.
        Required Skills:
        - Strong proficiency in Python automation.
        - Experience building frameworks using PyTest and Robot Framework.
        - Docker and AWS deployment automation.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision == DecisionType.SKIP
    assert result.match_breakdown.overall_score < 60.0
    assert "Python" in result.match_breakdown.missing_skills or "python" in result.match_breakdown.reasoning.lower()

@pytest.mark.asyncio
async def test_skip_csharp_dotnet_automation_stack(evaluator: JobEvaluator, profile: CandidateProfile):
    """
    Job requires C# / .NET / SpecFlow. Candidate is Java-based.
    Agent must detect hard stack incompatibility and SKIP.
    """
    job = JobDetails(
        job_id="job_csharp_mismatch",
        title="QA Automation Engineer - C# .NET",
        company="DotNet Corp",
        location="Bangalore",
        job_url="https://www.linkedin.com/jobs/view/job_csharp_mismatch",
        is_easy_apply=True,
        description_text="""
        Seeking QA Automation Engineer with 4 years experience.
        Requirements:
        - Deep expertise in C# and .NET framework.
        - BDD automation using SpecFlow and NUnit.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision == DecisionType.SKIP
    assert result.match_breakdown.overall_score <= 45.0
    assert "c#" in result.match_breakdown.reasoning.lower() or "incompatible" in result.match_breakdown.reasoning.lower()

@pytest.mark.asyncio
async def test_apply_when_skills_match_candidate_profile(evaluator: JobEvaluator, profile: CandidateProfile):
    """
    Job requires Java, Selenium, TestNG, RestAssured, SQL.
    Candidate profile matches. Agent must decide to APPLY with high score.
    """
    job = JobDetails(
        job_id="job_java_matched",
        title="QA Automation Engineer",
        company="AgileTech India",
        location="Hyderabad",
        job_url="https://www.linkedin.com/jobs/view/job_java_matched",
        is_easy_apply=True,
        description_text="""
        Looking for a QA Automation Engineer with 3-5 years experience.
        Requirements:
        - Core Java programming.
        - Selenium WebDriver and TestNG.
        - API testing using RestAssured.
        - Database validation using SQL.
        """
    )

    result = await evaluator.evaluate_fit(job, profile)

    assert result.decision == DecisionType.APPLY
    assert result.match_breakdown.overall_score >= 60.0
    assert len(result.match_breakdown.matched_skills) >= 2
    assert "Java" in result.match_breakdown.matched_skills or "Selenium" in result.match_breakdown.matched_skills

def test_role_score_pre_filtering(evaluator: JobEvaluator):
    target_roles = ["QA Automation Engineer", "SDET"]

    # Target matches should score >= 85
    assert evaluator._compute_role_score("Senior QA Automation Engineer", target_roles) >= 90.0
    assert evaluator._compute_role_score("SDET II", target_roles) >= 85.0
    assert evaluator._compute_role_score("Software Quality Assurance Tester", target_roles) >= 85.0

    # Completely unrelated non-QA jobs must score <= 20.0 so they are immediately pre-filtered out
    assert evaluator._compute_role_score("Customer Service Assistant", target_roles) <= 20.0
    assert evaluator._compute_role_score("Salesforce Developer", target_roles) <= 20.0
    assert evaluator._compute_role_score("Principal GenAI Engineer", target_roles) <= 20.0
    assert evaluator._compute_role_score("Sports Data Collector (Freelance position)", target_roles) <= 20.0
    assert evaluator._compute_role_score("Senior Java Software Engineer", target_roles) <= 20.0

