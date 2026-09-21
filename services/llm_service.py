import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar
import httpx
from pydantic import BaseModel

from config.settings import settings
from models.evaluation import ExtractedRequirements

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

class LLMService:
    """
    Unified LLM Service supporting OpenAI, Gemini, LiteLLM, and a
    deterministic heuristic NLP fallback for offline & zero-credential environments.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY or settings.OPENAI_API_KEY or settings.ANTHROPIC_API_KEY
        self.provider = (provider or settings.LLM_PROVIDER).lower()
        self.model = model or settings.LLM_MODEL

        # Auto-detect provider if 'auto'
        if self.provider == "auto" and self.api_key:
            if self.api_key.startswith("AIzaSy") or self.api_key.startswith("AQ.") or not self.api_key.startswith("sk-"):
                self.provider = "gemini"
            else:
                self.provider = "openai"

    def can_use_llm(self) -> bool:
        """Returns True if an active API key and supported LLM provider are available."""
        return bool(self.api_key and self.provider not in ["heuristic", "none"])

    def _can_use_llm(self) -> bool:
        return self.can_use_llm()

    def _is_gemini(self) -> bool:
        return self.provider == "gemini" or (self.api_key and (self.api_key.startswith("AIzaSy") or self.api_key.startswith("AQ.")))

    _cached_active_gemini_model: Optional[str] = None

    @staticmethod
    def _build_gemini_payload(
        prompt: str,
        json_output: bool,
        temperature: float,
        model_name: str,
        with_thinking: bool = False
    ) -> Dict[str, Any]:
        gen_config: Dict[str, Any] = {"temperature": temperature}
        if json_output:
            gen_config["responseMimeType"] = "application/json"

        # Apply low-latency thinking config (MINIMAL for 3.x, 0 budget for 2.5)
        if with_thinking:
            if "3." in model_name or "3-" in model_name:
                gen_config["thinkingConfig"] = {
                    "thinkingLevel": "MINIMAL"
                }
            elif "2.5" in model_name:
                gen_config["thinkingConfig"] = {
                    "thinkingBudget": 0
                }

        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config
        }

    @staticmethod
    def _extract_gemini_text(res_json: Dict[str, Any]) -> str:
        candidates = res_json.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        # Extract output text parts (ignoring internal thought tokens if present)
        text_parts = [p.get("text", "") for p in parts if not p.get("thought", False) and "text" in p]
        if text_parts:
            return "".join(text_parts).strip()
        if parts and "text" in parts[0]:
            return parts[0]["text"].strip()
        return ""

    def _get_gemini_candidate_models(self) -> List[str]:
        models = []
        # 1. Prioritize previously working model for instant response
        if LLMService._cached_active_gemini_model:
            models.append(LLMService._cached_active_gemini_model)
        if self.model and "gemini" in self.model and self.model not in models:
            models.append(self.model)
        for m in [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3-pro-preview"
        ]:
            if m not in models:
                models.append(m)
        return models

    async def call_gemini(
        self,
        prompt: str,
        json_output: bool = True,
        temperature: float = 0.2,
        timeout: float = 7.0
    ) -> str:
        """
        Calls latest Gemini models with fast response, minimal thinking, and instant fallback.
        """
        candidate_models = self._get_gemini_candidate_models()
        last_error = None
        req_headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        async with httpx.AsyncClient(timeout=timeout) as client:
            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
                
                # Attempt fast non-thinking first for instant interactive response
                for with_thinking in [False, True]:
                    body = self._build_gemini_payload(prompt, json_output, temperature, model_name, with_thinking=with_thinking)
                    try:
                        res = await client.post(url, headers=req_headers, json=body)
                        if res.status_code == 200:
                            text = self._extract_gemini_text(res.json())
                            if text:
                                LLMService._cached_active_gemini_model = model_name
                                return text
                        elif res.status_code in [400, 404] and not with_thinking:
                            continue
                        elif res.status_code == 503:
                            # Model under high demand - immediately pivot to next candidate model
                            logger.debug(f"Gemini model {model_name} 503 high demand, skipping to next model.")
                            last_error = Exception(f"Gemini {model_name} error: 503 high demand")
                            break
                        logger.debug(f"Gemini model {model_name} responded with {res.status_code}, attempting fallback.")
                        last_error = Exception(f"Gemini {model_name} error: {res.status_code} {res.text}")
                        break
                    except Exception as e:
                        last_error = e
                        logger.debug(f"Gemini model {model_name} request failed: {e}")
                        break

        raise last_error or RuntimeError("All Gemini model endpoints failed.")

    def call_gemini_sync(
        self,
        prompt: str,
        json_output: bool = True,
        temperature: float = 0.2,
        timeout: float = 7.0
    ) -> str:
        """
        Synchronous call to latest Gemini models with fast response and instant fallback.
        """
        candidate_models = self._get_gemini_candidate_models()
        last_error = None
        req_headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        with httpx.Client(timeout=timeout) as client:
            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
                
                for with_thinking in [False, True]:
                    body = self._build_gemini_payload(prompt, json_output, temperature, model_name, with_thinking=with_thinking)
                    try:
                        res = client.post(url, headers=req_headers, json=body)
                        if res.status_code == 200:
                            text = self._extract_gemini_text(res.json())
                            if text:
                                LLMService._cached_active_gemini_model = model_name
                                return text
                        elif res.status_code in [400, 404] and not with_thinking:
                            continue
                        elif res.status_code == 503:
                            logger.debug(f"Gemini model {model_name} 503 high demand, skipping to next model.")
                            last_error = Exception(f"Gemini {model_name} error: 503 high demand")
                            break
                        logger.debug(f"Gemini model {model_name} responded with {res.status_code}, attempting fallback.")
                        last_error = Exception(f"Gemini {model_name} error: {res.status_code}")
                        break
                    except Exception as e:
                        last_error = e
                        logger.debug(f"Gemini model {model_name} request failed: {e}")
                        break

        raise last_error or RuntimeError("All Gemini model endpoints failed.")

    async def analyze_job_fit_with_llm(
        self,
        job_title: str,
        company: str,
        job_description: str,
        candidate_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calls the LLM to interact with the raw job description, assessing fit,
        identifying key technical requirements, and crafting an executive summary.
        """
        if not self.can_use_llm():
            return self._analyze_job_fit_heuristic(job_title, job_description, candidate_profile)

        prof = candidate_profile.get("professional", {})
        personal = candidate_profile.get("personal", {})
        cand_name = personal.get("full_name") or "Candidate"
        cand_designation = prof.get("designation") or (candidate_profile.get("preferred_roles", ["Engineer"])[0] if candidate_profile.get("preferred_roles") else "Engineer")
        cand_exp = prof.get("total_experience_years", 0)
        cand_skills = ", ".join(candidate_profile.get("skills", []))
        cand_company = prof.get("current_company") or "N/A"
        target_roles = candidate_profile.get("preferred_roles", [])
        cand_roles_str = ", ".join(target_roles) if target_roles else cand_designation

        prompt = f"""You are an elite technical hiring advisor.
Analyze this job posting description against the candidate's profile:

Job Title: {job_title}
Company: {company}
Job Description:
{job_description[:4500]}

Candidate Profile:
- Name: {cand_name}
- Current Designation: {cand_designation}
- Target Job Roles: {cand_roles_str}
- Experience: {cand_exp} years
- Skills: {cand_skills}
- Current Company: {cand_company}

Evaluation Rules:
1. Determine if this job's engineering discipline (e.g. QA/SDET, Fullstack, Backend, Frontend, DevOps, SRE, Cloud, Data, AI/ML) genuinely aligns with the candidate's target roles and skillset.
2. If there is a domain or discipline mismatch (e.g. Java Fullstack / SRE / Cloud Engineer for a QA/SDET profile, or vice versa), assign a low fit_score below 40.
3. If the role aligns well with the candidate's target roles and skillset, assign a realistic fit_score between 60 and 99 based on matched requirements.

Respond ONLY with a valid JSON object formatted as:
{{
  "fit_score": integer between 0 and 100 representing fit percentage,
  "top_requirements": list of top 4 technical skills extracted directly from the description,
  "matched_skills": list of skills from the candidate profile that match the job description,
  "match_summary": "1-2 sentences summarizing fit or explaining why there is a discipline mismatch."
}}"""

        try:
            if self._is_gemini():
                raw_text = await self.call_gemini(prompt, json_output=True, temperature=0.2)
                return json.loads(raw_text)

            else:
                model_name = self.model if "gpt" in self.model else "gpt-4o-mini"
                async with httpx.AsyncClient(timeout=25.0) as client:
                    res = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json={
                            "model": model_name,
                            "messages": [
                                {"role": "system", "content": "You are a precise technical hiring evaluator. Return valid JSON only."},
                                {"role": "user", "content": prompt}
                            ],
                            "response_format": {"type": "json_object"},
                            "temperature": 0.2,
                        }
                    )
                    res.raise_for_status()
                    content = res.json()["choices"][0]["message"]["content"]
                    return json.loads(content)

        except Exception as e:
            logger.warning(f"LLM Job description analysis failed ({e}), evaluating with dynamic heuristics.")
            return self._analyze_job_fit_heuristic(job_title, job_description, candidate_profile)

    def _analyze_job_fit_heuristic(
        self,
        job_title: str,
        job_description: str,
        candidate_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculates job fit score deterministically using profile skills and title alignment.
        Properly penalizes role discipline mismatch and missing required skills (< 45%).
        """
        prof = candidate_profile.get("professional", {})
        cand_designation = prof.get("designation") or (candidate_profile.get("preferred_roles", ["Engineer"])[0] if candidate_profile.get("preferred_roles") else "Engineer")
        target_roles = candidate_profile.get("preferred_roles", [])
        cand_roles_str = ", ".join(target_roles) if target_roles else cand_designation
        skills_list = candidate_profile.get("skills", [])
        
        jd_lower = job_description.lower()
        title_lower = job_title.lower()
        target_roles_lower = [r.lower() for r in (target_roles or [cand_designation])]

        # 1. Check title discipline match
        is_title_match = any(r in title_lower or title_lower in r for r in target_roles_lower)
        
        qa_keywords = {"qa", "sdet", "test", "testing", "quality", "automation"}
        is_cand_qa = any(any(q in r for q in qa_keywords) for r in target_roles_lower)
        is_job_qa = any(q in title_lower for q in qa_keywords)
        
        non_qa_dev_terms = ["fullstack", "full stack", "backend", "site reliability", "sre", "cloud & infrastructure", "devops", "frontend", "infrastructure"]
        is_job_non_qa_dev = any(d in title_lower for d in non_qa_dev_terms)

        # 2. Extract technical requirements
        reqs = self._extract_with_heuristic(job_title, job_description)
        required_skills = reqs.required_skills
        cand_skills_lower = [s.lower().strip() for s in skills_list]

        # 3. Check hard language / tech stack incompatibility
        is_cand_python = any("python" in s for s in cand_skills_lower)
        if not is_cand_python and (re.search(r'\bpython\b', title_lower) or (re.search(r'\bpython\b', jd_lower) and not any(k in jd_lower for k in ["java", "selenium", "playwright"]))):
            return {
                "fit_score": 25.0,
                "top_requirements": required_skills[:4] if required_skills else ["Python"],
                "matched_skills": [],
                "missing_skills": ["Python"],
                "match_summary": f"Incompatible stack: '{job_title}' requires Python automation."
            }

        is_cand_csharp = any(any(c in s for c in ["c#", ".net", "csharp", "dotnet"]) for s in cand_skills_lower)
        if not is_cand_csharp and (re.search(r'(?<!\w)(c#|\.net|csharp|dotnet|specflow)(?!\w)', title_lower) or (re.search(r'(?<!\w)(c#|\.net|csharp)(?!\w)', jd_lower) and not any(k in jd_lower for k in ["java", "selenium", "playwright"]))):
            return {
                "fit_score": 25.0,
                "top_requirements": required_skills[:4] if required_skills else ["C# / .NET"],
                "matched_skills": [],
                "missing_skills": ["C# / .NET"],
                "match_summary": f"Incompatible stack: '{job_title}' requires C# / .NET automation."
            }

        matched_reqs = []
        missing_reqs = []
        for req in required_skills:
            req_clean = req.lower().strip()
            if any(req_clean == c or req_clean in c or c in req_clean for c in cand_skills_lower):
                matched_reqs.append(req)
            else:
                missing_reqs.append(req)

        if is_cand_qa and is_job_non_qa_dev and not is_job_qa:
            score = 25.0
            summary = f"Role mismatch: '{job_title}' does not align with QA/SDET target profile."
        elif not is_title_match:
            score = 35.0
            summary = f"Title '{job_title}' has low alignment with target roles ({cand_roles_str})."
        elif required_skills:
            coverage = len(matched_reqs) / len(required_skills)
            if len(matched_reqs) == 0 or (len(required_skills) >= 2 and coverage < 0.5):
                score = min(45.0, round(coverage * 80.0, 1))
                summary = f"Low skill match: Only {len(matched_reqs)}/{len(required_skills)} required skills matched. Missing: {', '.join(missing_reqs[:4])}."
            else:
                score = round(40.0 + (coverage * 50.0), 1)
                summary = f"Candidate profile matches {len(matched_reqs)}/{len(required_skills)} requirements for {job_title}."
        else:
            # Fallback when no catalog skills were matched in JD
            matched = [s for s in skills_list if s.lower() in jd_lower]
            ratio = len(matched) / max(len(skills_list), 1)
            score = round(40.0 + (ratio * 50.0), 1)
            summary = f"Candidate profile matches the role and requirements for {job_title}."

        return {
            "fit_score": score,
            "top_requirements": required_skills[:4] if required_skills else (matched[:4] if 'matched' in locals() and matched else ["Technical skills alignment"]),
            "matched_skills": matched_reqs if required_skills else (matched[:5] if 'matched' in locals() else []),
            "missing_skills": missing_reqs,
            "match_summary": summary
        }

    async def extract_job_requirements(
        self,
        job_title: str,
        job_description: str
    ) -> ExtractedRequirements:
        """
        Extract required skills, experience bounds, and target designation
        from a raw job description using LLM or deterministic NLP fallback.
        """
        if self.can_use_llm():
            try:
                return await self._extract_with_llm(job_title, job_description)
            except Exception as e:
                logger.warning(f"LLM extraction failed ({e}), falling back to deterministic NLP engine.")

        return self._extract_with_heuristic(job_title, job_description)

    async def answer_form_question(
        self,
        question_text: str,
        options: List[str],
        candidate_context: Dict[str, Any],
        job_description: Optional[str] = None
    ) -> str:
        """
        Given a form question, candidate profile, and optional job description,
        select or generate the best response.
        """
        if self.can_use_llm():
            try:
                return await self._answer_with_llm(question_text, options, candidate_context, job_description)
            except Exception as e:
                logger.warning(f"LLM question answering failed ({e}), using heuristic mapper.")

        return self._answer_with_heuristic(question_text, options, candidate_context)

    async def evaluate_skill_experience(
        self,
        skill: str,
        candidate_context: Dict[str, Any],
        total_experience_years: float,
        job_description: Optional[str] = None
    ) -> float:
        """
        Evaluates candidate's realistic years of experience with a specific skill using LLM.
        Returns a float between 0.0 and total_experience_years, or -1.0 if evaluation cannot be performed.
        """
        if not self.can_use_llm():
            return -1.0

        jd_excerpt = f"\nTarget Job Context:\n{job_description[:1000]}\n" if job_description else ""
        prompt = f"""You are an expert talent acquisition and recruiting AI evaluating a candidate's experience for a job application question.

Question: "How many years of experience do you have with {skill}?"
Target Skill: "{skill}"
Candidate Total Overall Professional Experience: {total_experience_years} years

Candidate Profile & Resume Context:
{json.dumps(candidate_context, indent=2)}
{jd_excerpt}

Evaluation Rules:
1. If "{skill}" is part of the candidate's primary core discipline and experience throughout their career, return {total_experience_years} or close to it.
2. If "{skill}" is a secondary tool, methodology, or used in a subset of projects, return a realistic value (typically 1.0 to 2.5 years), never exceeding {total_experience_years}.
3. If "{skill}" is absent from the candidate's resume, skills list, and project descriptions, return 0.0.
4. Return strictly a JSON object with one key "years" as a float or int:
{{"years": 2.0}}
"""
        try:
            if self._is_gemini():
                raw_text = await self.call_gemini(prompt, json_output=True, temperature=0.0)
                data = json.loads(raw_text)
                if isinstance(data, dict) and "years" in data:
                    years = float(data["years"])
                    return max(0.0, min(years, total_experience_years))
                m = re.search(r'"years"\s*:\s*(\d+(?:\.\d+)?)', raw_text)
                if m:
                    return max(0.0, min(float(m.group(1)), total_experience_years))
            elif self.api_key:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                        json={
                            "model": self.model if "gpt" in self.model else "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": "You are a precise technical skill evaluation engine. Respond only with valid JSON."},
                                {"role": "user", "content": prompt}
                            ],
                            "response_format": {"type": "json_object"},
                            "temperature": 0.0,
                        },
                    )
                    res.raise_for_status()
                    content = res.json()["choices"][0]["message"]["content"]
                    data = json.loads(content)
                    if isinstance(data, dict) and "years" in data:
                        years = float(data["years"])
                        return max(0.0, min(years, total_experience_years))
        except Exception as e:
            logger.warning(f"LLM skill experience evaluation failed for '{skill}': {e}")

        return -1.0


    async def _extract_with_llm(self, job_title: str, job_description: str) -> ExtractedRequirements:
        prompt = f"""You are an expert talent acquisition and recruiting AI. Analyze the following job posting across any professional discipline (Engineering, QA, Finance, Marketing, HR, Operations, Healthcare, Legal, etc.):
Job Title: {job_title}
Job Description:
{job_description[:4500]}

Extract the requirements as a strictly formatted JSON object with these keys:
- "required_skills": list of required skills, tools, or qualifications
- "preferred_skills": list of preferred, bonus, or nice-to-have skills
- "min_experience_years": float representing minimum years of experience required (e.g. 4.0). If no experience is specified, use 0.0.
- "max_experience_years": float or null if an experience range is given (e.g. 5-8 -> 8.0).
- "target_role_level": string ("Entry", "Mid", "Senior", "Lead", "Architect", "Manager", "Director")
- "target_designation": normalized job title (e.g. "{job_title}")
- "job_department": inferred department (e.g. "Software Engineering", "Quality Assurance", "Finance", "Marketing", "Human Resources", "Operations", "Healthcare", "Legal")

JSON:"""

        if self._is_gemini():
            raw_text = await self.call_gemini(prompt, json_output=True, temperature=settings.LLM_TEMPERATURE)
            data = json.loads(raw_text)
            return ExtractedRequirements(**data)

        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model if "gpt" in self.model else "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": "You are a precise job requirement extraction engine. Respond only with valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": settings.LLM_TEMPERATURE,
                    },
                )
                res.raise_for_status()
                content = res.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                return ExtractedRequirements(**data)

    async def _answer_with_llm(
        self,
        question_text: str,
        options: List[str],
        candidate_context: Dict[str, Any],
        job_description: Optional[str] = None
    ) -> str:
        jd_context = f"\nRelevant Job Description excerpt:\n{job_description[:1500]}\n" if job_description else ""
        prompt = f"""You are filling an employment application on behalf of the candidate.
Candidate Profile Context:
{json.dumps(candidate_context, indent=2)}
{jd_context}
Question: "{question_text}"
Available Options: {options if options else "Free text entry"}

Select or formulate the most accurate answer based strictly on the candidate's profile and relevance to the job.
If options are provided, output EXACTLY one matching option string from the options list.
Answer:"""

        raw_answer = ""
        if self._is_gemini():
            raw_text = await self.call_gemini(prompt, json_output=False, temperature=0.0)
            raw_answer = raw_text.strip().strip('"')

        elif self.api_key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    },
                )
                res.raise_for_status()
                raw_answer = res.json()["choices"][0]["message"]["content"].strip().strip('"')
        else:
            return self._answer_with_heuristic(question_text, options, candidate_context)

        # Clean answer and remove conversational LLM prefixes
        clean_ans = re.sub(r'^(?:answer|response|selected option):\s*', '', raw_answer, flags=re.IGNORECASE).strip()
        if options:
            for opt in options:
                if opt.strip().lower() == clean_ans.lower():
                    return opt
            for opt in options:
                if opt.strip().lower() in clean_ans.lower() or clean_ans.lower() in opt.strip().lower():
                    return opt
        return clean_ans

    def _extract_with_heuristic(self, job_title: str, job_description: str) -> ExtractedRequirements:
        """
        Comprehensive rule-based NLP extraction for offline/zero-API usage.
        Extracts years of experience, technical skills, and seniority levels.
        """
        combined = f"{job_title}\n{job_description}".lower()

        # 1. Parse Years of Experience
        # Patterns like: "3-5 years", "3 to 5 years", "4+ years", "minimum 3 years", "at least 4 years"
        min_exp = 0.0
        max_exp = None

        range_match = re.search(r'(\d+)\s*(?:-|to)\s*(\d+)\s*(?:\+?\s*)?(?:years|year|yrs|yr)', combined)
        plus_match = re.search(r'(\d+)\s*\+\s*(?:years|year|yrs|yr)', combined)
        min_match = re.search(r'(?:minimum|at least|min\.?)\s*(\d+)\s*(?:years|year|yrs|yr)', combined)
        simple_match = re.search(r'(\d+)\s*(?:years|year|yrs|yr)(?:\s+of)?\s+(?:experience|exp)', combined)

        if range_match:
            min_exp = float(range_match.group(1))
            max_exp = float(range_match.group(2))
        elif plus_match:
            min_exp = float(plus_match.group(1))
        elif min_match:
            min_exp = float(min_match.group(1))
        elif simple_match:
            min_exp = float(simple_match.group(1))

        # 2. Extract Technical & Domain Skills (Separating Required vs Preferred)
        skill_catalog = [
            # Tech & Development
            "selenium", "playwright", "cypress", "appium", "java", "python", "typescript",
            "javascript", "c#", ".net", "dotnet", "csharp", "specflow", "pytest", "pyunit",
            "robot framework", "ruby", "golang", "php", "testng", "cucumber", "junit",
            "restassured", "postman", "sql", "mysql", "postgresql", "jenkins", "git",
            "github actions", "docker", "kubernetes", "jmeter", "api testing", "automation testing",
            "bdd", "tdd", "ci/cd", "rest", "soap", "soapui", "linux", "aws", "azure", "jira",
            "agile", "scrum", "performance testing", "loadrunner", "gatling", "react", "node", "angular", "vue",
            # Finance & Accounting
            "financial modeling", "dcf", "valuation", "excel", "gaap", "ifrs", "budgeting",
            "forecasting", "fp&a", "quickbooks", "auditing", "tax", "variance analysis", "cash flow",
            # Marketing & Sales
            "seo", "sem", "google ads", "ppc", "google analytics", "content marketing",
            "social media marketing", "email marketing", "hubspot", "copywriting", "lead generation",
            # Human Resources
            "talent acquisition", "recruiting", "sourcing", "onboarding", "hris", "workday",
            "employee relations", "payroll", "compensation",
            # Operations & Engineering
            "supply chain", "logistics", "procurement", "lean", "six sigma", "inventory management",
            "cad", "solidworks", "autocad", "matlab",
            # Healthcare & Legal
            "patient care", "clinical operations", "emr", "ehr", "hipaa",
            "contract management", "compliance", "regulatory compliance", "due diligence"
        ]

        # Identify preferred / nice-to-have section if present
        pref_section_match = re.search(
            r'(?:good to have|nice to have|preferred skills|optional|bonus points?|plus|desired skills?)\s*[:\n](.+?)(?=\n\s*(?:requirements|responsibilities|qualifications|about you|benefits|\Z))',
            combined,
            re.DOTALL
        )
        pref_text = pref_section_match.group(1).lower() if pref_section_match else ""

        def normalize_skill_name(s: str) -> str:
            upper_skills = {"sql", "bdd", "tdd", "ci/cd", "api testing", "jira", "aws", "c#", ".net", "dcf", "gaap", "ifrs", "fp&a", "seo", "sem", "ppc", "hris", "cad", "emr", "ehr", "hipaa"}
            if s in upper_skills:
                return "C#" if s in ["c#", "csharp"] else (".NET" if s in [".net", "dotnet"] else s.upper())
            elif s in ["playwright", "selenium", "postman", "docker", "jenkins", "jmeter", "restassured", "pytest", "specflow", "soapui", "quickbooks", "hubspot", "workday", "solidworks", "autocad", "matlab"]:
                return "RestAssured" if s == "restassured" else ("PyTest" if s == "pytest" else ("SpecFlow" if s == "specflow" else ("SoapUI" if s == "soapui" else ("QuickBooks" if s == "quickbooks" else ("HubSpot" if s == "hubspot" else ("Workday" if s == "workday" else ("SolidWorks" if s == "solidworks" else s.capitalize())))))))
            return s.title()

        found_required = []
        found_preferred = []

        for skill in skill_catalog:
            pattern = rf'(?:\b|\.){re.escape(skill.lstrip("."))}\b' if skill.startswith(".") else rf'\b{re.escape(skill)}\b'
            norm_s = normalize_skill_name(skill)
            if pref_text and re.search(pattern, pref_text):
                found_preferred.append(norm_s)
            elif re.search(pattern, combined):
                found_required.append(norm_s)

        # 3. Target Role Level
        level = "Mid"
        title_lower = job_title.lower()
        if any(w in title_lower for w in ["lead", "principal", "manager", "architect", "director", "head"]):
            level = "Lead"
        elif any(w in title_lower for w in ["senior", "sr.", "sr ", "expert"]):
            level = "Senior"
        elif any(w in title_lower for w in ["junior", "jr.", "entry", "intern", "associate"]):
            level = "Entry"

        # 4. Infer Job Department
        job_dept = None
        if any(w in title_lower for w in ["qa", "test", "quality", "sdet"]):
            job_dept = "Quality Assurance"
        elif any(w in title_lower for w in ["finance", "financial", "accounting", "accountant", "audit", "tax", "fp&a"]):
            job_dept = "Finance & Accounting"
        elif any(w in title_lower for w in ["marketing", "seo", "sem", "content", "growth", "brand"]):
            job_dept = "Marketing"
        elif any(w in title_lower for w in ["sales", "bdr", "sdr", "account executive"]):
            job_dept = "Sales"
        elif any(w in title_lower for w in ["hr", "human resources", "recruiter", "talent"]):
            job_dept = "Human Resources"
        elif any(w in title_lower for w in ["operations", "supply chain", "logistics"]):
            job_dept = "Operations"
        elif any(w in title_lower for w in ["nurse", "clinical", "medical", "patient"]):
            job_dept = "Healthcare"
        elif any(w in title_lower for w in ["legal", "counsel", "compliance"]):
            job_dept = "Legal"
        elif any(w in title_lower for w in ["engineer", "developer", "architect", "devops", "cloud"]):
            job_dept = "Software Engineering"

        # 5. Location & Work Mode
        req_loc = None
        for loc in ["hyderabad", "bangalore", "bengaluru", "pune", "chennai", "mumbai", "delhi", "noida", "gurgaon", "gurugram", "kolkata"]:
            if loc in combined:
                req_loc = loc.title()
                break

        work_mode = None
        if "remote" in combined or "work from home" in combined or "wfh" in combined:
            work_mode = "Remote"
        elif "hybrid" in combined:
            work_mode = "Hybrid"
        elif "on-site" in combined or "onsite" in combined or "work from office" in combined:
            work_mode = "On-site"

        # 6. Notice Period
        notice_days = None
        if re.search(r'\b(immediate\s*joiners?|join\s*immediately|immediate\s*joining|0[- ]15\s*days?)\b', combined):
            notice_days = 0
        elif re.search(r'\b15\s*days?\b', combined):
            notice_days = 15
        elif re.search(r'\b(30\s*days?|1\s*month)\b', combined):
            notice_days = 30
        elif re.search(r'\b(60\s*days?|2\s*months?)\b', combined):
            notice_days = 60
        elif re.search(r'\b(90\s*days?|3\s*months?)\b', combined):
            notice_days = 90

        # 7. Salary
        min_sal = None
        max_sal = None
        sal_range = re.search(r'(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs|lakh|lac|lacs)', combined)
        if sal_range:
            min_sal = float(sal_range.group(1))
            max_sal = float(sal_range.group(2))
        else:
            single_sal = re.search(r'(\d+(?:\.\d+)?)\s*(?:lpa|lakhs|lakh|lac|lacs)', combined)
            if single_sal:
                min_sal = float(single_sal.group(1))

        return ExtractedRequirements(
            required_skills=list(dict.fromkeys(found_required)),
            preferred_skills=list(dict.fromkeys(found_preferred)),
            min_experience_years=min_exp,
            max_experience_years=max_exp,
            target_role_level=level,
            target_designation=job_title.strip(),
            job_department=job_dept,
            required_location=req_loc,
            work_mode=work_mode,
            required_notice_days=notice_days,
            min_salary_lpa=min_sal,
            max_salary_lpa=max_sal
        )

    def _answer_with_heuristic(
        self,
        question_text: str,
        options: List[str],
        candidate_context: Dict[str, Any]
    ) -> str:
        """
        Deterministic rule-based mapper for common LinkedIn application questions.
        """
        q = question_text.lower()
        options_lower = [opt.lower() for opt in options] if options else []

        # 1. Experience with specific skill: "How many years of experience do you have with [Skill]?"
        exp_match = re.search(r'years of (?:work )?experience (?:do you have )?with (.+?)(?:\?|$)', q)
        if exp_match:
            skill = exp_match.group(1).strip()
            # check candidate skills
            skills = [s.lower() for s in candidate_context.get("skills", [])]
            for s in skills:
                if s in skill or skill in s:
                    return str(int(candidate_context.get("total_experience_years", 4)))
            return "0"

        # 2. Hybrid / Remote / On-site comfort
        if "hybrid" in q or "remote" in q or "commute" in q or "relocate" in q:
            if options:
                for idx, opt in enumerate(options_lower):
                    if "yes" in opt or "willing" in opt or "agree" in opt:
                        return options[idx]
            return "Yes"

        # 0. Stakeholder / External Client Communication: return 0 value
        if any(phrase in q for phrase in [
            "communicated testing progress",
            "communicating testing progress",
            "release readiness to stakeholders",
            "testing progress, risks, defects",
            "stakeholders or external clients"
        ]):
            if options:
                for idx, opt in enumerate(options_lower):
                    if any(z in opt for z in ["0", "none", "no experience", "no"]):
                        return options[idx]
                return options[0]
            return "0"

        # 3. Notice period
        if "notice period" in q or "how soon can you join" in q:
            days = candidate_context.get("notice_period_days", 30)
            if options:
                for idx, opt in enumerate(options_lower):
                    if str(days) in opt:
                        return options[idx]
                    if "immediate" in opt and days <= 15:
                        return options[idx]
                return options[0]
            if "day" in q:
                return str(days)
            return f"{days} days"

        # 4. Current / Expected CTC
        if any(w in q for w in ["current ctc", "current salary", "current compensation", "current annual compensation", "current lpa"]):
            cur_lpa = float(candidate_context.get("current_lpa", 0.0) or 0.0)
            if "month" in q:
                return str(int((cur_lpa * 100000) / 12)) if cur_lpa > 0 else "0"
            is_lpa = any(w in q for w in ["lakh", "lakhs", "lpa", "lac"]) and not any(w in q for w in ["in inr", "inr", "larger than 100", "200000"])
            if is_lpa:
                return str(int(cur_lpa)) if cur_lpa.is_integer() else str(cur_lpa)
            if any(w in q for w in ["inr", "annual", "larger than 100", "200000", "rupee", "whole number"]):
                return str(int(cur_lpa * 100000))
            return str(int(cur_lpa * 100000))

        if any(w in q for w in ["expected ctc", "expected salary", "expected compensation", "expected annual compensation", "salary expectation", "expected lpa"]):
            exp_lpa = float(candidate_context.get("expected_lpa", 0.0) or 0.0)
            if "month" in q:
                return str(int((exp_lpa * 100000) / 12)) if exp_lpa > 0 else "0"
            is_lpa = any(w in q for w in ["lakh", "lakhs", "lpa", "lac"]) and not any(w in q for w in ["in inr", "inr", "larger than 100", "350000"])
            if is_lpa:
                return str(int(exp_lpa)) if exp_lpa.is_integer() else str(exp_lpa)
            if any(w in q for w in ["inr", "annual", "larger than 100", "350000", "rupee", "whole number"]):
                return str(int(exp_lpa * 100000))
            return str(int(exp_lpa * 100000))

        # 5. Work authorization / Citizenship
        if "legally authorized" in q or "authorized to work" in q:
            if options:
                for idx, opt in enumerate(options_lower):
                    if "yes" in opt:
                        return options[idx]
            return "Yes"

        if "require sponsorship" in q or "require visa" in q:
            if options:
                for idx, opt in enumerate(options_lower):
                    if "no" in opt:
                        return options[idx]
            return "No"

        # Fallback for binary yes/no questions
        if options:
            for idx, opt in enumerate(options_lower):
                if opt in ["yes", "true", "i agree"]:
                    return options[idx]
            return options[0]

        return ""
