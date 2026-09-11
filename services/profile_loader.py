import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from models.profile import CandidateProfile
from config.settings import settings

class ProfileLoader:
    """
    Single source of truth for the candidate profile.
    Dynamically loads and validates candidate data from config/candidate_profile.json.
    """
    _instance: Optional["ProfileLoader"] = None
    _profile: Optional[CandidateProfile] = None

    def __init__(self, profile_path: Optional[str] = None):
        self.profile_path = Path(profile_path or settings.PROFILE_PATH)
        self.load()

    @classmethod
    def get_instance(cls, profile_path: Optional[str] = None) -> "ProfileLoader":
        if cls._instance is None:
            cls._instance = cls(profile_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance to force fresh reload from disk."""
        cls._instance = None

    def reload(self) -> CandidateProfile:
        """Reload profile from disk."""
        self._profile = None
        return self.load()

    def load(self) -> CandidateProfile:
        if not self.profile_path.exists():
            template_path = Path(__file__).parent.parent / "config" / "candidate_profile.template.json"
            if template_path.exists():
                import shutil
                self.profile_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(template_path, self.profile_path)
            else:
                raise FileNotFoundError(f"Candidate profile not found at {self.profile_path}")

        with open(self.profile_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)

        self._profile = CandidateProfile(**data)
        return self._profile

    @property
    def profile(self) -> CandidateProfile:
        if self._profile is None:
            return self.load()
        return self._profile

    # Dynamic accessors
    @property
    def full_name(self) -> str:
        return self.profile.personal.full_name

    @property
    def email(self) -> str:
        return self.profile.personal.email

    @property
    def phone(self) -> str:
        return self.profile.personal.phone

    @property
    def location(self) -> str:
        return self.profile.personal.location

    @property
    def designation(self) -> str:
        return self.profile.professional.designation

    @property
    def total_experience_years(self) -> float:
        return self.profile.professional.total_experience_years

    @property
    def current_company(self) -> str:
        return self.profile.professional.current_company

    @property
    def current_lpa(self) -> float:
        return self.profile.professional.current_lpa

    @property
    def expected_lpa(self) -> float:
        return self.profile.professional.expected_lpa

    @property
    def notice_period_days(self) -> int:
        return self.profile.professional.notice_period_days

    @property
    def skills(self) -> List[str]:
        return self.profile.skills

    @property
    def excluded_skills(self) -> List[str]:
        return self.profile.excluded_skills

    @property
    def target_roles(self) -> List[str]:
        return self.profile.target_roles or self.profile.preferred_roles

    @property
    def primary_roles(self) -> List[str]:
        return self.profile.primary_roles or self.target_roles[:5]

    @property
    def preferred_roles(self) -> List[str]:
        return self.profile.preferred_roles or self.profile.target_roles

    @property
    def preferred_locations(self) -> List[str]:
        return self.profile.preferred_locations

    @property
    def preferred_work_modes(self) -> List[str]:
        return self.profile.preferred_work_modes or ["On-site", "Hybrid", "Remote"]

    @property
    def minimum_match_score(self) -> float:
        return self.profile.job_preferences.minimum_match_score

    @property
    def require_human_approval(self) -> bool:
        return self.profile.job_preferences.require_human_approval

    def is_excluded_skill(self, skill_query: str) -> bool:
        """Checks if the queried skill is an explicitly excluded skill (candidate does NOT possess)."""
        query = skill_query.lower().strip()
        query_compact = re.sub(r'[\s\-_]+', '', query)
        for ex in self.excluded_skills:
            ex_l = ex.lower().strip()
            if query == ex_l or f" {ex_l} " in f" {query} " or query_compact == re.sub(r'[\s\-_]+', '', ex_l):
                return True
        return False

    def has_skill(self, skill_query: str) -> bool:
        """Checks if candidate possesses the queried skill. Strictly rejects any excluded skill."""
        if self.is_excluded_skill(skill_query):
            return False
        query = skill_query.lower().strip()
        query_compact = re.sub(r'[\s\-_]+', '', query)
        for skill in self.skills:
            s_lower = skill.lower()
            if query == s_lower or query in s_lower or s_lower in query:
                return True
            s_compact = re.sub(r'[\s\-_]+', '', s_lower)
            if query_compact and (query_compact == s_compact or query_compact in s_compact or s_compact in query_compact):
                return True
        return False

    def get_skill_experience_years(self, skill_name: str) -> float:
        """
        Dynamically returns years of experience for a given skill.
        If candidate has the skill, returns their total experience (or prorated).
        If candidate does not have the skill, returns 0.
        """
        if self.has_skill(skill_name):
            return float(self.total_experience_years)
        return 0.0

    def matches_role(self, target_role: str) -> float:
        """Computes a role similarity match ratio from 0.0 to 1.0 against preferred roles."""
        role_lower = target_role.lower()
        best_score = 0.2
        for pref in self.preferred_roles:
            pref_lower = pref.lower()
            if pref_lower in role_lower or role_lower in pref_lower:
                return 1.0
            # Common token overlap check
            pref_tokens = set(pref_lower.split())
            role_tokens = set(role_lower.split())
            overlap = pref_tokens.intersection(role_tokens)
            if overlap:
                score = len(overlap) / max(len(pref_tokens), len(role_tokens))
                if score > best_score:
                    best_score = score
        return best_score

    def matches_location(self, target_location: str) -> bool:
        """Checks if the location matches preferred locations or includes Remote."""
        loc_lower = target_location.lower()
        if any(r in loc_lower for r in ["remote", "work from home", "wfh", "anywhere"]):
            return True
        for pref in self.preferred_locations:
            if pref.lower() in loc_lower or loc_lower in pref.lower():
                return True
        return False
