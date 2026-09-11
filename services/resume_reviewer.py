import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import pypdf

from config.settings import settings
from models.profile import CandidateProfile
from services.llm_service import LLMService

logger = logging.getLogger(__name__)

class ResumeReviewer:
    """
    AI-powered ATS Resume Reviewer and Gap Analysis Engine.
    Evaluates resume text against candidate's target roles, flags missing information,
    and provides concrete recommendations to increase recruiter shortlists.
    """

    def __init__(self, llm_service: Optional[LLMService] = None):
        self.llm_service = llm_service or LLMService()

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """Extract text from a resume PDF using pypdf."""
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"Resume PDF not found at {pdf_path}")

        try:
            reader = pypdf.PdfReader(str(path))
            pages_text = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
            return "\n\n".join(pages_text)
        except Exception as e:
            logger.error(f"Error reading PDF {pdf_path}: {e}")
            raise e

    async def review_resume(
        self,
        resume_path: str,
        profile: CandidateProfile
    ) -> Dict[str, Any]:
        """
        Performs comprehensive ATS resume review and gap analysis using LLM.
        """
        resume_text = self.extract_text_from_pdf(resume_path)
        target_roles = ", ".join(profile.preferred_roles) if profile.preferred_roles else profile.professional.designation
        known_skills = ", ".join(profile.skills)

        prompt = f"""You are a Principal Technical Recruiter and ATS Optimization Expert.
Analyze the following resume for a candidate targeting roles: [{target_roles}].

Candidate Profile Summary:
- Name: {profile.personal.full_name}
- Current Title: {profile.professional.designation}
- Experience: {profile.professional.total_experience_years} years
- Documented Skills: {known_skills}

Resume Text:
\"\"\"
{resume_text[:6000]}
\"\"\"

Conduct a rigorous review and return a strictly valid JSON object with the following schema:
{{
  "ats_score": integer between 50 and 100,
  "executive_summary": "2-sentence overall evaluation of candidate's market competitiveness",
  "strengths": [
    "string: specific strong point found in resume"
  ],
  "missing_information": [
    "string: critical missing detail (e.g. quantifiable metrics/percentages, portfolio link, specific framework, degree)"
  ],
  "skill_gaps": [
    "string: high-demand skill for target roles currently missing or underrepresented"
  ],
  "recommendations": [
    {{
      "area": "e.g. Quantifiable Impact / Certifications / Tooling",
      "issue": "what is lacking",
      "actionable_fix": "concrete sentence or bullet-point rewrite suggestion"
    }}
  ]
}}

JSON:"""

        if self.llm_service.can_use_llm():
            try:
                if self.llm_service._is_gemini():
                    raw_text = await self.llm_service.call_gemini(prompt, json_output=True, temperature=0.2)
                    return json.loads(raw_text)
                else:
                    import httpx
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        res = await client.post(
                            "https://api.openai.com/v1/chat/completions",
                            headers={"Authorization": f"Bearer {self.llm_service.api_key}", "Content-Type": "application/json"},
                            json={
                                "model": "gpt-4o-mini",
                                "messages": [
                                    {"role": "system", "content": "You are a professional ATS resume auditor. Return valid JSON only."},
                                    {"role": "user", "content": prompt}
                                ],
                                "response_format": {"type": "json_object"},
                                "temperature": 0.2
                            }
                        )
                        res.raise_for_status()
                        return json.loads(res.json()["choices"][0]["message"]["content"])
            except Exception as e:
                logger.warning(f"LLM resume review failed ({e}), using rule-based assessment.")

        # Heuristic fallback if no LLM configured
        return self._heuristic_review(resume_text, profile)

    def _heuristic_review(self, resume_text: str, profile: CandidateProfile) -> Dict[str, Any]:
        """Deterministic fallback review when offline."""
        text_lower = resume_text.lower()
        missing = []
        if "@" not in text_lower:
            missing.append("Email address not prominently detectable.")
        if "github" not in text_lower and "portfolio" not in text_lower:
            missing.append("GitHub / Portfolio link missing.")
        if "%" not in resume_text and not any(w in text_lower for w in ["reduced", "increased", "improved", "saved"]):
            missing.append("Quantifiable business metrics (e.g., % test execution time reduced, defect reduction rate).")

        return {
            "ats_score": 82,
            "executive_summary": f"Solid technical background with {profile.professional.total_experience_years} years in {profile.professional.designation}.",
            "strengths": [
                f"Clear focus on core skills: {', '.join(profile.skills[:4])}",
                "Relevant hands-on company experience listed"
            ],
            "missing_information": missing,
            "skill_gaps": ["Cloud / CI/CD pipeline automation metrics", "Docker containerized test environments"],
            "recommendations": [
                {
                    "area": "Metrics & Impact",
                    "issue": "Bullet points describe duties rather than measurable results.",
                    "actionable_fix": "Rewrite duty bullet points into: 'Accomplished [X] by doing [Y], resulting in [Z% improvement]'."
                }
            ]
        }
