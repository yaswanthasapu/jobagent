import pytest
from models.form import FormField, FormFieldType
from models.profile import CandidateProfile
from services.llm_service import LLMService
from services.profile_loader import ProfileLoader
from services.memory_service import MemoryService
from agents.form_agent import FormAgent

@pytest.fixture
def profile() -> CandidateProfile:
    return ProfileLoader().profile

@pytest.fixture
def form_agent(tmp_path, profile: CandidateProfile) -> FormAgent:
    mem_path = tmp_path / "test_agent_memory.json"
    mem_service = MemoryService(memory_path=mem_path)
    mem_service.seed_from_profile(profile)
    return FormAgent(LLMService(provider="heuristic"), memory_service=mem_service)

@pytest.mark.asyncio
async def test_form_agent_skill_experience_resolution(form_agent: FormAgent, profile: CandidateProfile):
    field = FormField(
        field_id="exp_playwright",
        label="How many years of work experience do you have with Playwright?",
        field_type=FormFieldType.TEXT
    )
    val, needs_hitl = await form_agent.resolve_field_value(field, profile)
    assert val in ["1.5", "1", "2"]
    assert needs_hitl is False

    # Core skill gets 4
    field_sel = FormField(
        field_id="exp_sel",
        label="How many years of work experience do you have with Selenium?",
        field_type=FormFieldType.TEXT
    )
    val_sel, _ = await form_agent.resolve_field_value(field_sel, profile)
    assert val_sel in ["4", "4.0"]

    # Unpossessed skill
    field_unknown = FormField(
        field_id="exp_ruby",
        label="How many years of work experience do you have with Ruby on Rails?",
        field_type=FormFieldType.TEXT
    )
    val2, needs_hitl2 = await form_agent.resolve_field_value(field_unknown, profile)
    assert val2 == "0"
    assert needs_hitl2 is False

@pytest.mark.asyncio
async def test_form_agent_compensation_and_notice(form_agent: FormAgent, profile: CandidateProfile):
    current_ctc_field = FormField(
        field_id="current_ctc",
        label="What is your current CTC (in LPA)?",
        field_type=FormFieldType.TEXT
    )
    val, _ = await form_agent.resolve_field_value(current_ctc_field, profile)
    cur_str = str(int(profile.professional.current_lpa)) if profile.professional.current_lpa.is_integer() else str(profile.professional.current_lpa)
    assert cur_str in val

    expected_ctc_field = FormField(
        field_id="expected_ctc",
        label="Expected CTC (LPA)",
        field_type=FormFieldType.TEXT
    )
    val2, _ = await form_agent.resolve_field_value(expected_ctc_field, profile)
    exp_str = str(int(profile.professional.expected_lpa)) if profile.professional.expected_lpa.is_integer() else str(profile.professional.expected_lpa)
    assert exp_str in val2

    notice_field = FormField(
        field_id="notice",
        label="Notice Period (Days)",
        field_type=FormFieldType.TEXT
    )
    val3, _ = await form_agent.resolve_field_value(notice_field, profile)
    assert "60" in val3

@pytest.mark.asyncio
async def test_form_agent_notice_dropdown_selection(form_agent: FormAgent, profile: CandidateProfile):
    dropdown_field = FormField(
        field_id="notice_dropdown",
        label="Select your notice period",
        field_type=FormFieldType.SELECT,
        options=["Immediate", "15 Days", "30 Days", "60 Days / 2 Months", "90 Days"]
    )
    val, needs_hitl = await form_agent.resolve_field_value(dropdown_field, profile)
    assert "60" in val or "2 Months" in val
    assert needs_hitl is False

@pytest.mark.asyncio
async def test_form_agent_sensitive_ambiguous_flags_hitl(form_agent: FormAgent, profile: CandidateProfile):
    sensitive_field = FormField(
        field_id="clearance",
        label="Do you possess an active DoD Top Secret Security Clearance?",
        field_type=FormFieldType.TEXT
    )
    val, needs_hitl = await form_agent.resolve_field_value(sensitive_field, profile)
    assert needs_hitl is True

@pytest.mark.asyncio
async def test_form_agent_additional_questions_radio_resolution(form_agent: FormAgent, profile: CandidateProfile):
    # 1. Age requirement: "Are you at least 18 years of age?*"
    age_field = FormField(
        field_id="age_check",
        label="Are you at least 18 years of age?*",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_age, _ = await form_agent.resolve_field_value(age_field, profile)
    assert val_age == "Yes"

    # 2. Requisite education/certification: "Do you have the requisite education and/or certification for this position as outlined in the job description?*"
    edu_field = FormField(
        field_id="edu_check",
        label="Do you have the requisite education and/or certification for this position as outlined in the job description?*",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_edu, _ = await form_agent.resolve_field_value(edu_field, profile)
    assert val_edu == "Yes"

    # 3. Eligibility to work in India without sponsorship: "If hired, can you provide proof of identity and eligibility to work in India without sponsorship?*"
    sponsor_field = FormField(
        field_id="work_auth_india",
        label="If hired, can you provide proof of identity and eligibility to work in India without sponsorship?*",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_sponsor, _ = await form_agent.resolve_field_value(sponsor_field, profile)
    assert val_sponsor == "Yes"

    # 4. Visa sponsorship required -> No
    visa_field = FormField(
        field_id="visa_req",
        label="Will you now or in the future require visa sponsorship for employment?",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_visa, _ = await form_agent.resolve_field_value(visa_field, profile)
    assert val_visa == "No"

    # 5. Phone Country Code dropdown
    cc_field = FormField(
        field_id="phone_cc",
        label="Phone country code*",
        field_type=FormFieldType.SELECT,
        options=["United States (+1)", "India (+91)", "United Kingdom (+44)"]
    )
    val_cc, _ = await form_agent.resolve_field_value(cc_field, profile)
    assert "+91" in val_cc

@pytest.mark.asyncio
async def test_form_agent_linkedin_exact_questions(form_agent: FormAgent, profile: CandidateProfile):
    # 1. Selenium Experience -> 4
    field_sel = FormField(
        field_id="exp_selenium",
        label="How many years of work experience do you have with Selenium?*",
        field_type=FormFieldType.TEXT
    )
    val_sel, needs_hitl_sel = await form_agent.resolve_field_value(field_sel, profile)
    assert val_sel in ["4", "4.0"]
    assert needs_hitl_sel is False

    # 2. Core Java Experience -> 4
    field_java = FormField(
        field_id="exp_core_java",
        label="How many years of work experience do you have with Core Java?*",
        field_type=FormFieldType.TEXT
    )
    val_java, needs_hitl_java = await form_agent.resolve_field_value(field_java, profile)
    assert val_java in ["4", "4.0"]
    assert needs_hitl_java is False

    # 3. Total IT Experience -> 4
    field_total_it = FormField(
        field_id="total_it_exp",
        label="Total IT Experience ?*",
        field_type=FormFieldType.TEXT
    )
    val_total, needs_hitl_total = await form_agent.resolve_field_value(field_total_it, profile)
    assert val_total in ["4", "4.0"]
    assert needs_hitl_total is False

    # 4. Rest Assured Experience -> 4
    field_rest = FormField(
        field_id="exp_rest_assured",
        label="How many years of work experience do you have with Rest Assured ?*",
        field_type=FormFieldType.TEXT
    )
    val_rest, needs_hitl_rest = await form_agent.resolve_field_value(field_rest, profile)
    assert val_rest in ["4", "4.0"]
    assert needs_hitl_rest is False

@pytest.mark.asyncio
async def test_form_agent_eeo_demographics_resolution(form_agent: FormAgent, profile: CandidateProfile):
    # Race/Ethnicity radio selection -> Asian
    race_field = FormField(
        field_id="fieldset_race",
        label="Race/Ethnicity*",
        field_type=FormFieldType.RADIO,
        options=[
            "Hispanic or Latino",
            "American Indian or Alaska Native (Not Hispanic or Latino)",
            "Asian (Not Hispanic or Latino)",
            "Black or African American (Not Hispanic or Latino)",
            "Native Hawaiian or Other Pacific Islander (Not Hispanic or Latino)",
            "Two or More Races (Not Hispanic or Latino)",
            "White (Not Hispanic or Latino)",
            "I prefer not to specify"
        ]
    )
    val_race, needs_hitl_race = await form_agent.resolve_field_value(race_field, profile)
    assert "Asian" in val_race
    assert needs_hitl_race is False

    # Gender radio selection -> Male
    gender_field = FormField(
        field_id="fieldset_gender",
        label="Gender*",
        field_type=FormFieldType.RADIO,
        options=[
            "Male",
            "Female",
            "I prefer not to specify"
        ]
    )
    val_gender, needs_hitl_gender = await form_agent.resolve_field_value(gender_field, profile)
    assert val_gender == "Male"
    assert needs_hitl_gender is False

@pytest.mark.asyncio
async def test_form_agent_user_additional_questions(form_agent: FormAgent, profile: CandidateProfile):
    # 1. Product-based companies worked for
    field_prod = FormField(
        field_id="prod_companies",
        label="Product-based companies worked for",
        field_type=FormFieldType.TEXT,
        options=["Service-Based Company", "Enterprise Software / SaaS Product Company", "Startup", "Consultancy"]
    )
    val_prod, _ = await form_agent.resolve_field_value(field_prod, profile)
    assert "Enterprise Software" in val_prod

    # 2. Automation testing for iPaaS, SaaS, or cloud-based platforms?
    field_ipaas = FormField(
        field_id="ipaas_testing",
        label="Automation testing for iPaaS, SaaS, or cloud-based platforms?",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_ipaas, _ = await form_agent.resolve_field_value(field_ipaas, profile)
    assert val_ipaas == "Yes"

    # 3. API Testing – Postman/Swagger/etc.
    field_api = FormField(
        field_id="api_testing_level",
        label="API Testing – Postman/Swagger/etc.",
        field_type=FormFieldType.SELECT,
        options=["Beginner", "Intermediate", "Advanced", "Expert"]
    )
    val_api, _ = await form_agent.resolve_field_value(field_api, profile)
    assert val_api == "Advanced"

    # 4. Frameworks worked with
    field_fw = FormField(
        field_id="frameworks_worked",
        label="Frameworks worked with",
        field_type=FormFieldType.TEXT,
        options=["Selenium", "Playwright", "Cypress", "Appium"]
    )
    val_fw, _ = await form_agent.resolve_field_value(field_fw, profile)
    assert "Selenium" in val_fw

    # 5. Python Experience
    field_py = FormField(
        field_id="python_exp",
        label="Python Experience",
        field_type=FormFieldType.SELECT,
        options=["None", "Beginner", "Intermediate", "Advanced"]
    )
    val_py, _ = await form_agent.resolve_field_value(field_py, profile)
    assert val_py == "Beginner"

    # 6. PyScript experience
    field_pyscript = FormField(
        field_id="pyscript_exp",
        label="PyScript experience",
        field_type=FormFieldType.SELECT,
        options=["No experience", "Basic knowledge", "Extensive experience"]
    )
    val_pyscript, _ = await form_agent.resolve_field_value(field_pyscript, profile)
    assert "No experience" in val_pyscript

    # 7. Backend applications/services hosted on AWS
    field_aws = FormField(
        field_id="aws_backend",
        label="Backend applications/services hosted on AWS",
        field_type=FormFieldType.SELECT,
        options=["No experience", "1-2 years", "3+ years"]
    )
    val_aws, _ = await form_agent.resolve_field_value(field_aws, profile)
    assert "No experience" in val_aws

    # 8. Legally authorized to work in country
    field_legal = FormField(
        field_id="legal_auth",
        label="Legally authorized to work in country",
        field_type=FormFieldType.RADIO,
        options=["Yes", "No"]
    )
    val_legal, _ = await form_agent.resolve_field_value(field_legal, profile)
    assert val_legal == "Yes"

    # Disability radio selection
    disability_field = FormField(
        field_id="fieldset_disability",
        label="Disability status*",
        field_type=FormFieldType.RADIO,
        options=[
            "Yes, I have a disability",
            "No, I don't have a disability",
            "I don't wish to answer"
        ]
    )
    val_disability, needs_hitl_disability = await form_agent.resolve_field_value(disability_field, profile)
    assert "No" in val_disability
    assert needs_hitl_disability is False

    # Veteran radio selection
    vet_field = FormField(
        field_id="fieldset_veteran",
        label="Veteran status*",
        field_type=FormFieldType.RADIO,
        options=[
            "I am a veteran",
            "I am not a protected veteran",
            "I decline to self-identify"
        ]
    )
    val_vet, needs_hitl_vet = await form_agent.resolve_field_value(vet_field, profile)
    assert "not a protected veteran" in val_vet.lower()
    assert needs_hitl_vet is False

@pytest.mark.asyncio
async def test_form_agent_uses_resume_grounding(form_agent: FormAgent, profile: CandidateProfile):
    # Test resume text property is available
    assert isinstance(form_agent.resume_text, str)

    # Test common experience question phrasing: "Years of experience"
    exp_field = FormField(
        field_id="exp_years",
        label="Years of Experience",
        field_type=FormFieldType.NUMBER
    )
    val_exp, _ = await form_agent.resolve_field_value(exp_field, profile)
    assert val_exp == "4"

    # Test "Experience (Years)"
    exp_field2 = FormField(
        field_id="exp_years_paren",
        label="Experience (Years)",
        field_type=FormFieldType.TEXT
    )
    val_exp2, _ = await form_agent.resolve_field_value(exp_field2, profile)
    assert val_exp2 == "4"

    # Test education with options
    edu_field = FormField(
        field_id="highest_edu",
        label="Highest level of education",
        field_type=FormFieldType.SELECT,
        options=["High School", "Associate Degree", "Bachelor's Degree", "Master's Degree"]
    )
    val_edu, _ = await form_agent.resolve_field_value(edu_field, profile)
    assert "Bachelor" in val_edu

@pytest.mark.asyncio
async def test_form_agent_new_field_triggers_hitl_and_saves_response(form_agent: FormAgent, profile: CandidateProfile):
    # 1. A completely new question appears that is NOT in memory or profile
    new_question_label = "Are you comfortable working in rotating day/night shifts?"
    new_field = FormField(
        field_id="rotational_shifts",
        label=new_question_label,
        field_type=FormFieldType.RADIO,
        options=["Yes", "No", "Flexible"]
    )

    # Initial resolution: Must flag needs_hitl=True
    suggested_val, needs_hitl = await form_agent.resolve_field_value(new_field, profile)
    assert needs_hitl is True

    # 2. Human interaction occurs: user provides "Flexible"
    human_response = "Flexible"
    form_agent.memory_service.save_form_answer(new_question_label, human_response, "radio")

    # 3. Subsequent job applications encounter the exact same or slightly rephrased question:
    saved_val, needs_hitl_after = await form_agent.resolve_field_value(new_field, profile)
    assert needs_hitl_after is False
    assert saved_val == "Flexible"

@pytest.mark.asyncio
async def test_form_agent_new_freetext_field_triggers_hitl(form_agent: FormAgent, profile: CandidateProfile):
    # A completely new custom free-text field
    custom_field = FormField(
        field_id="custom_referral",
        label="Who referred you to this position?",
        field_type=FormFieldType.TEXT
    )
    suggested_val, needs_hitl = await form_agent.resolve_field_value(custom_field, profile)
    assert needs_hitl is True

    # Save human answer
    form_agent.memory_service.save_form_answer("Who referred you to this position?", "Employee Referral - Yaswanth", "text")

    # Verify remembered
    resolved_val, needs_hitl2 = await form_agent.resolve_field_value(custom_field, profile)
    assert needs_hitl2 is False
    assert "Yaswanth" in resolved_val


@pytest.mark.asyncio
async def test_form_agent_skill_experience_with_ai(tmp_path, profile: CandidateProfile, mocker):
    mem_path = tmp_path / "test_ai_agent_memory.json"
    mem_service = MemoryService(memory_path=mem_path)
    llm_service = LLMService(provider="gemini", api_key="test-api-key")

    # Mock can_use_llm and evaluate_skill_experience
    mocker.patch.object(llm_service, "can_use_llm", return_value=True)
    mocker.patch.object(llm_service, "evaluate_skill_experience", side_effect=lambda skill, **kwargs: (
        4.0 if "selenium" in skill.lower() else (
            1.5 if "python" in skill.lower() else (
                0.0 if "c#" in skill.lower() else 2.0
            )
        )
    ))

    agent = FormAgent(llm_service=llm_service, memory_service=mem_service)

    # 1. Core skill
    f1 = FormField(field_id="f1", label="How many years of work experience do you have with Selenium?", field_type=FormFieldType.TEXT)
    v1, _ = await agent.resolve_field_value(f1, profile)
    assert v1 == "4"

    # 2. Secondary skill evaluated by AI as 1.5 -> "1.5"
    f2 = FormField(field_id="f2", label="How many years of work experience do you have with Python?", field_type=FormFieldType.TEXT)
    v2, _ = await agent.resolve_field_value(f2, profile)
    assert v2 == "1.5"

    # 3. Unpossessed skill evaluated by AI as 0.0 -> "0"
    f3 = FormField(field_id="f3", label="How many years of work experience do you have with C#?", field_type=FormFieldType.TEXT)
    v3, _ = await agent.resolve_field_value(f3, profile)
    assert v3 == "0"


@pytest.mark.asyncio
async def test_form_agent_skill_experience_range_options(tmp_path, profile: CandidateProfile, mocker):
    mem_path = tmp_path / "test_options_agent_memory.json"
    mem_service = MemoryService(memory_path=mem_path)
    llm_service = LLMService(provider="gemini", api_key="test-api-key")

    mocker.patch.object(llm_service, "can_use_llm", return_value=True)
    mocker.patch.object(llm_service, "evaluate_skill_experience", return_value=2.0)

    agent = FormAgent(llm_service=llm_service, memory_service=mem_service)

    # Dropdown with ranges
    field_opts = FormField(
        field_id="f_opts",
        label="How many years of work experience do you have with Postman?",
        field_type=FormFieldType.SELECT,
        options=["None / 0 years", "1-2 years", "3-5 years", "5+ years"]
    )
    v_opt, needs_hitl = await agent.resolve_field_value(field_opts, profile)
    assert v_opt == "1-2 years"
    assert needs_hitl is False


