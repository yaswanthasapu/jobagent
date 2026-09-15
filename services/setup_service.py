import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
import pypdf
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from config.settings import BASE_DIR, settings
from models.profile import CandidateProfile, JobPreferences, PersonalInfo, ProfessionalInfo
from services.llm_service import LLMService
from services.memory_service import MemoryService
from services.resume_reviewer import ResumeReviewer
from services.profile_loader import ProfileLoader

logger = logging.getLogger(__name__)

ENV_PATH = Path(settings.DATA_DIR / ".env") if (settings.DATA_DIR / ".env").exists() else Path(BASE_DIR / ".env")
PROFILE_PATH = Path(settings.PROFILE_PATH)
RESUME_PATH = Path(settings.RESUME_PATH)

TECH_TAXONOMY: List[str] = [
    # Programming Languages
    "Java", "Python", "JavaScript", "TypeScript", "C++", "C#", "C", "Go", "Golang", "Rust", "Kotlin", "Swift", "PHP", "Ruby", "Scala", "R", "Dart", "Shell", "Bash", "PowerShell", "SQL",
    # Java Ecosystem
    "Spring", "Spring Boot", "Spring Cloud", "Hibernate", "JPA", "Maven", "Gradle", "JVM", "JDBC", "Struts", "Servlets", "JSP",
    # Testing & Automation
    "Selenium", "Playwright", "Cypress", "TestNG", "JUnit", "Cucumber", "BDD", "TDD", "Postman", "RestAssured", "REST Assured", "JMeter", "Appium", "PyTest", "Mocha", "Jest", "Karma", "Jasmine", "Robot Framework", "Manual Testing", "Automation Testing", "API Testing", "Mobile Testing", "Performance Testing", "Regression Testing", "Smoke Testing", "Load Testing", "Security Testing", "UFT", "QTP", "UiPath", "SoapUI",
    # Frontend
    "React", "React.js", "Next.js", "Angular", "Vue", "Vue.js", "Nuxt.js", "Redux", "HTML", "HTML5", "CSS", "CSS3", "Sass", "SCSS", "Tailwind CSS", "Bootstrap", "Webpack", "Vite", "jQuery",
    # Backend & Frameworks
    "Node.js", "Express", "Express.js", "Django", "FastAPI", "Flask", ".NET", ".NET Core", "ASP.NET", "Ruby on Rails", "NestJS", "Koa", "Gin", "Microservices", "REST API", "GraphQL", "gRPC", "SOAP", "WebSockets",
    # Databases & Caching
    "MySQL", "PostgreSQL", "Oracle", "MongoDB", "Redis", "Elasticsearch", "Cassandra", "DynamoDB", "SQLite", "MariaDB", "MSSQL", "SQL Server", "CouchDB", "Neo4j", "Firebase", "Kafka", "RabbitMQ", "ActiveMQ",
    # Cloud & DevOps
    "AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "K8s", "Jenkins", "GitLab CI", "GitHub Actions", "Terraform", "Ansible", "Helm", "Prometheus", "Grafana", "Linux", "Unix", "Nginx", "Apache", "CI/CD",
    # Data & AI/ML
    "Apache Spark", "Hadoop", "Pandas", "NumPy", "Scikit-Learn", "TensorFlow", "PyTorch", "Keras", "Airflow", "Snowflake", "Databricks", "BigQuery", "Tableau", "Power BI", "Machine Learning", "Deep Learning", "NLP",
    # Tools & Collaboration
    "Git", "GitHub", "GitLab", "Bitbucket", "Jira", "Confluence", "Swagger", "Eclipse", "IntelliJ", "VS Code", "Agile", "Scrum", "Kanban"
]

def synthesize_target_roles(designation: str, skills: List[str]) -> List[str]:
    """Dynamically derives target job roles strictly from the candidate's actual skills and title."""
    skills_lower = {s.lower() for s in skills}
    title_lower = designation.lower() if designation else ""
    roles: List[str] = []

    is_qa = (
        any(s in skills_lower for s in ['selenium', 'playwright', 'cypress', 'testng', 'cucumber', 'appium', 'jmeter', 'manual testing', 'automation testing', 'test automation', 'restassured', 'rest assured'])
        or any(w in title_lower for w in ['qa', 'sdet', 'test', 'quality'])
    )
    is_backend = (
        any(s in skills_lower for s in ['java', 'python', 'node', 'nodejs', 'express', 'django', 'fastapi', 'spring', 'spring boot', 'c#', '.net', 'golang', 'go', 'sql', 'postgresql', 'mysql', 'microservices', 'hibernate'])
        or 'backend' in title_lower
    )
    is_frontend = (
        any(s in skills_lower for s in ['react', 'angular', 'vue', 'javascript', 'typescript', 'html', 'css', 'nextjs', 'tailwind', 'redux'])
        or 'frontend' in title_lower or 'ui' in title_lower
    )
    is_devops = (
        any(s in skills_lower for s in ['docker', 'kubernetes', 'jenkins', 'terraform', 'ansible', 'ci/cd', 'aws', 'azure', 'gcp'])
        or any(w in title_lower for w in ['devops', 'cloud', 'sre'])
    )
    is_data = (
        any(s in skills_lower for s in ['pandas', 'numpy', 'spark', 'hadoop', 'machine learning', 'deep learning', 'pytorch', 'tensorflow', 'scikit-learn'])
        or 'data' in title_lower or 'ml' in title_lower
    )

    if is_qa and not is_backend:
        roles.extend(['QA Automation Engineer', 'SDET', 'Test Automation Engineer'])
    elif is_backend and is_frontend:
        roles.extend(['Full Stack Engineer', 'Full Stack Developer', 'Software Engineer', 'Backend Engineer'])
    elif is_backend:
        if 'java' in skills_lower:
            roles.extend(['Backend Engineer', 'Full Stack Engineer', 'Java Engineer', 'Java Developer'])
        elif 'python' in skills_lower:
            roles.extend(['Python Developer', 'Backend Engineer', 'Software Engineer'])
        else:
            roles.extend(['Backend Engineer', 'Software Engineer'])
    elif is_frontend:
        roles.extend(['Frontend Developer', 'UI Engineer', 'Full Stack Developer'])
    elif is_devops:
        roles.extend(['DevOps Engineer', 'Cloud Engineer', 'Site Reliability Engineer'])
    elif is_data:
        roles.extend(['Data Engineer', 'Machine Learning Engineer'])

    if not roles:
        if designation and designation not in ['Software Professional', 'Candidate']:
            roles = [designation, f'Senior {designation}', 'Software Engineer']
        else:
            roles = ['Software Engineer', 'Full Stack Engineer', 'Backend Engineer']

    seen = set()
    deduped = []
    for r in roles:
        if r.lower() not in seen:
            seen.add(r.lower())
            deduped.append(r)
    return deduped[:5]

class SetupService:
    """
    Manages one-time user onboarding, profile configuration, resume extraction,
    and persistent environment settings.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.memory_service = MemoryService()

    def has_configured_profile(self) -> bool:
        """Checks if the user has an existing, non-empty candidate profile configured on this machine."""
        if not PROFILE_PATH.exists():
            return False
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                name = data.get("name") or (data.get("personal", {}).get("full_name") if isinstance(data.get("personal"), dict) else "")
                skills = data.get("skills", [])
                if not name or not str(name).strip() or not skills:
                    return False
                if str(name).strip().lower() in ["", "candidate", "your name", "software professional"]:
                    return False
                return True
        except Exception:
            return False

    def is_setup_complete(self, runtime_api_key: Optional[str] = None, allow_offline: bool = True) -> bool:
        """Checks if the user has already configured their profile, resume, and optional API key."""
        has_profile = self.has_configured_profile()
        has_resume = RESUME_PATH.exists()
        has_api_key = bool(runtime_api_key or settings.GEMINI_API_KEY or settings.OPENAI_API_KEY or self._get_env_key("GEMINI_API_KEY") or self._get_env_key("OPENAI_API_KEY"))

        if allow_offline:
            return has_profile and has_resume
        return has_profile and has_resume and has_api_key

    def _get_env_key(self, key_name: str) -> Optional[str]:
        if not ENV_PATH.exists():
            return None
        with open(ENV_PATH, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key_name}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return val if val else None
        return None

    def save_api_key_to_env(self, api_key: str, provider: str = "auto") -> None:
        """Permanently save API key to the .env file."""
        key_type = "OPENAI_API_KEY" if (provider == "openai" or api_key.startswith("sk-")) else "GEMINI_API_KEY"
        lines = []
        key_written = False

        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8-sig") as f:
                for line in f:
                    if line.strip().startswith(f"{key_type}="):
                        lines.append(f"{key_type}={api_key}\n")
                        key_written = True
                    else:
                        lines.append(line)

        if not key_written:
            lines.append(f"{key_type}={api_key}\n")

        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # Update running settings in memory
        if key_type == "GEMINI_API_KEY":
            settings.GEMINI_API_KEY = api_key
        else:
            settings.OPENAI_API_KEY = api_key

    def parse_resume_to_dict(self, resume_pdf_path: Path) -> Dict[str, Any]:
        """Extract text and parse candidate details dynamically from a resume PDF using latest Gemini AI and heuristics."""
        try:
            reader = pypdf.PdfReader(str(resume_pdf_path))
            raw_pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(raw_pages)
            normalized_text = re.sub(r'\s+', ' ', text).strip()
            raw_lines = [l.strip() for l in text.split("\n") if l.strip()]
        except Exception as e:
            logger.warning(f"Failed to read PDF text: {e}")
            text = ""
            normalized_text = ""
            raw_lines = []

        # Default fallback structure (no hardcoded roles or salaries)
        parsed: Dict[str, Any] = {
            "full_name": "",
            "email": "",
            "phone": "",
            "location": "",
            "designation": "",
            "current_company": "",
            "total_experience_years": 1.0,
            "skills": [],
            "suggested_target_roles": [],
            "highest_education": "",
            "current_ctc_inr": None,
            "expected_ctc_inr": None,
            "notice_period_days": None,
        }

        if not normalized_text:
            return parsed

        # 1. Regex heuristics for fast fallback
        # Email
        email_m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', text)
        if email_m:
            parsed["email"] = email_m.group(0).lower().strip()

        # Phone (Support +91 98765 43210, 98765-43210, (987) 654-3210, etc.)
        phone_m = re.search(r'(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}|(?:\+?1[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}|\b[6-9]\d{9}\b', text)
        if phone_m:
            digits = re.sub(r'\D', '', phone_m.group(0))
            if digits.startswith('91') and len(digits) == 12:
                digits = digits[2:]
            elif digits.startswith('1') and len(digits) == 11:
                digits = digits[1:]
            if len(digits) == 10:
                parsed["phone"] = digits

        # Candidate Name & Current Designation heuristic from top of resume
        skip_headers = {
            "curriculum", "vitae", "resume", "cv", "profile", "summary", "contact",
            "email", "phone", "address", "page", "about", "objective", "career",
            "personal", "details", "experience", "education", "skills", "hyderabad",
            "bangalore", "bengaluru", "pune", "chennai", "mumbai", "india", "remote"
        }
        cand_name = ""
        cand_role = ""
        for i, line in enumerate(raw_lines[:8]):
            clean = line.strip()
            lower = clean.lower()
            if not clean or any(term in lower for term in skip_headers):
                continue
            if "@" in clean or "http" in lower or "linkedin" in lower or "github" in lower:
                continue
            lead_part = re.split(r'[,|•\t]', clean)[0].strip()
            words = lead_part.split()
            if not cand_name:
                if 2 <= len(words) <= 4 and all(re.match(r'^[A-Za-z\.\'-]+$', w) for w in words):
                    cand_name = " ".join(w.capitalize() for w in words)
                    continue
                elif len(words) == 1 and words[0].isalpha() and i + 1 < len(raw_lines):
                    next_line = re.split(r'[,|•\t]', raw_lines[i + 1].strip())[0].strip()
                    next_words = next_line.split()
                    if len(next_words) == 1 and next_words[0].isalpha() and next_words[0].lower() not in skip_headers:
                        cand_name = f"{words[0].capitalize()} {next_words[0].capitalize()}"
                        continue
            elif not cand_role:
                if len(clean) <= 60 and any(kw in lower for kw in ["developer", "engineer", "lead", "architect", "manager", "analyst", "tester", "sdet", "consultant", "specialist", "qa"]):
                    cand_role = clean.strip()
                    continue

        if not cand_name:
            lead_chunk = re.split(r'[,|•\n\r]|(?:\+?\d{1,3}[-\s]?)?[6-9]\d{9}|[a-zA-Z0-9_.+-]+@', normalized_text)[0].strip()
            words = [w for w in lead_chunk.split() if w.isalpha() and w.lower() not in skip_headers]
            if 2 <= len(words) <= 4:
                cand_name = " ".join(w.capitalize() for w in words)
            elif len(words) > 4:
                cand_name = " ".join(w.capitalize() for w in words[:2])

        if cand_name:
            parsed["full_name"] = cand_name
        if cand_role:
            parsed["designation"] = cand_role

        # Total Experience Heuristic
        exp_m1 = re.search(r'(\d+(?:\.\d+)?)\+?\s*(?:years|year|yrs|yr)(?:\s+of)?\s+(?:experience|exp|total experience|it experience)', text, re.I)
        exp_m2 = re.search(r'(?:total\s+experience|overall\s+experience|work\s+experience|professional\s+experience|experience)[:\s]+(\d+(?:\.\d+)?)\+?\s*(?:years|year|yrs|yr)?', text, re.I)
        if exp_m1:
            try:
                parsed["total_experience_years"] = float(exp_m1.group(1))
            except Exception:
                pass
        elif exp_m2:
            try:
                parsed["total_experience_years"] = float(exp_m2.group(1))
            except Exception:
                pass
        else:
            date_ranges = re.findall(r'\b(20\d{2})\b\s*(?:-|to|–)\s*(?:present|current|\b(20\d{2})\b)', text, re.I)
            if date_ranges:
                from datetime import datetime
                curr_year = datetime.now().year
                start_year = min(int(r[0]) for r in date_ranges)
                calcd = round(float(curr_year - start_year), 1)
                if 0.5 <= calcd <= 30.0:
                    parsed["total_experience_years"] = calcd

        # Location Heuristic
        loc_m = re.search(r'(?:location|address|residence|city)[:\s]+([^\n\r\|•]+)', text, re.I)
        if loc_m:
            parsed["location"] = loc_m.group(1).strip()[:50]
        else:
            common_cities = ["hyderabad", "bengaluru", "bangalore", "pune", "chennai", "mumbai", "noida", "gurgaon", "gurugram", "delhi", "kolkata", "ahmedabad", "kochi", "coimbatore", "chandigarh", "jaipur"]
            for city in common_cities:
                if re.search(rf'\b{city}\b', text[:1500], re.I):
                    city_m = re.search(rf'\b({city}[,\s]+[A-Za-z\s]+)', text[:1500], re.I)
                    if city_m:
                        parsed["location"] = city_m.group(1).strip()[:40]
                    else:
                        parsed["location"] = city.title()
                    break

        # Current Company Heuristic
        comp_m = re.search(r'(?:current\s+company|current\s+employer|employer|company|organization)[:\s]+([A-Za-z0-9\s\.\,\&-]{2,40})(?=\n|\||•|,|\.|$)', text, re.I)
        if comp_m and not any(w in comp_m.group(1).lower() for w in ["name", "na", "none", "profile", "summary"]):
            parsed["current_company"] = comp_m.group(1).strip()
        else:
            comp_m2 = re.search(r'(?:working\s+(?:as|at)|employed\s+at|joined)\s+([A-Z][A-Za-z0-9\s\.\&-]{2,30})\b', text)
            if comp_m2:
                parsed["current_company"] = comp_m2.group(1).strip()

        # Technical skills heuristic scan across TECH_TAXONOMY
        heur_skills: List[str] = []
        for term in TECH_TAXONOMY:
            pattern = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
            if re.search(pattern, normalized_text, re.IGNORECASE):
                heur_skills.append(term)

        # Dedicated skills section extraction from raw text
        skills_sec_match = re.search(
            r'(?:TECHNICAL SKILLS|SKILLS|KEY SKILLS|CORE COMPETENCIES|AREAS OF EXPERTISE|TECHNOLOGIES)[:\s\n]+(.*?)(?=\n[A-Z\s]{4,25}(?:\n|:)|$)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if skills_sec_match:
            sec_text = skills_sec_match.group(1)
            tokens = re.split(r'[,|•*;\n/]+', sec_text)
            for tok in tokens:
                tok_clean = tok.strip()
                if 2 <= len(tok_clean) <= 40 and not re.match(r'^(and|or|the|with|using|in|for|of)$', tok_clean, re.I):
                    tok_clean = re.sub(r'^[\w\s]+:\s*', '', tok_clean).strip()
                    if tok_clean and len(tok_clean) >= 2:
                        heur_skills.append(tok_clean)

        # Deduplicate heur_skills preserving case
        seen_skills = set()
        deduped_heur: List[str] = []
        for s in heur_skills:
            if s.lower() not in seen_skills:
                seen_skills.add(s.lower())
                deduped_heur.append(s)
        parsed["skills"] = deduped_heur

        # Designation fallback scan across roles
        if not parsed.get("designation"):
            for t in ["QA Automation Engineer", "Software Development Engineer in Test", "SDET", "Software Test Engineer", "Backend Developer", "Java Developer", "Frontend Developer", "Full Stack Developer", "Software Engineer", "DevOps Engineer"]:
                if re.search(r'\b' + re.escape(t) + r'\b', normalized_text, re.IGNORECASE):
                    parsed["designation"] = t
                    break

        # 2. Dynamic AI extraction with latest Gemini models
        llm = LLMService()
        if llm.can_use_llm():
            prompt = f"""You are an elite ATS technical resume parser. Extract ALL candidate details and EVERY single technical skill from this resume text:
\"\"\"
{normalized_text[:8000]}
\"\"\"

Return ONLY a strictly valid JSON object with schema:
{{
  "full_name": "Full Name of candidate",
  "email": "Email address",
  "phone": "Clean 10-digit mobile phone number (digits only, e.g. 9876543210)",
  "location": "City, State, Country",
  "designation": "Current or most recent Job Title / Role",
  "current_company": "Current or most recent Employer / Company Name",
  "total_experience_years": number in years as float (e.g. 3.9 or 4.0),
  "current_ctc_inr": integer in INR or null if not explicitly mentioned,
  "expected_ctc_inr": integer in INR or null if not explicitly mentioned,
  "notice_period_days": integer in calendar days (e.g. 30, 60, 90) or null if not mentioned,
  "skills": ["Extract EVERY SINGLE technical skill, programming language, framework, database, tool, cloud technology, library, or protocol mentioned in the resume. Do NOT omit or truncate any skills!"],
  "suggested_target_roles": ["3 to 5 high-fit target job titles matching candidate's actual primary skills and designation"],
  "highest_education": "Degree, major, and institution name"
}}"""
            try:
                raw_text = ""
                if llm._is_gemini():
                    raw_text = llm.call_gemini_sync(prompt, json_output=True, temperature=0.1)
                else:
                    import httpx
                    res = httpx.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
                        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}},
                        timeout=20.0
                    )
                    if res.status_code == 200:
                        raw_text = res.json()["choices"][0]["message"]["content"]

                if raw_text:
                    clean_json = raw_text.strip()
                    if clean_json.startswith("```"):
                        clean_json = re.sub(r'^```(?:json)?\s*', '', clean_json)
                        clean_json = re.sub(r'\s*```$', '', clean_json)
                    llm_data = json.loads(clean_json)

                    if isinstance(llm_data, dict):
                        llm_skills = llm_data.get("skills", [])
                        if isinstance(llm_skills, list) and llm_skills:
                            combined_skills = list(llm_skills) + deduped_heur
                            merged_set = set()
                            merged_list = []
                            for s in combined_skills:
                                s_str = str(s).strip()
                                if s_str and s_str.lower() not in merged_set:
                                    merged_set.add(s_str.lower())
                                    merged_list.append(s_str)
                            llm_data["skills"] = merged_list

                        for k, v in llm_data.items():
                            if v is not None and v != "":
                                parsed[k] = v
            except Exception as e:
                logger.debug(f"LLM resume parsing skipped/fallback: {e}")

        # Ensure suggested_target_roles is dynamically synthesized if missing or empty
        if not parsed.get("suggested_target_roles"):
            parsed["suggested_target_roles"] = synthesize_target_roles(
                parsed.get("designation", ""),
                parsed.get("skills", [])
            )

        # Ensure phone is clean 10-digit number
        if parsed.get("phone"):
            clean_p = re.sub(r'\D', '', str(parsed["phone"]))
            if clean_p.startswith('91') and len(clean_p) == 12:
                clean_p = clean_p[2:]
            elif clean_p.startswith('1') and len(clean_p) == 11:
                clean_p = clean_p[1:]
            parsed["phone"] = clean_p

        return parsed

    async def run_setup_wizard(self) -> CandidateProfile:
        """Runs the interactive one-time onboarding wizard with deep dynamic resume extraction."""
        self.console.print(Panel.fit(
            "[bold cyan]Welcome to LinkedIn & Naukri AI Job Agent Setup[/bold cyan]\n"
            "[white]Configure your profile, API key, and resume once. The agent will remember everything.[/white]",
            border_style="cyan"
        ))

        # 1. API Key Setup
        curr_key = settings.GEMINI_API_KEY or settings.OPENAI_API_KEY or self._get_env_key("GEMINI_API_KEY") or self._get_env_key("OPENAI_API_KEY")
        if not curr_key:
            self.console.print("\n[bold yellow]Step 1: LLM API Key (Free Google Gemini or OpenAI)[/bold yellow]")
            self.console.print("[dim]Get free key at: https://aistudio.google.com/app/apikey[/dim]")
            api_key = Prompt.ask("[bold green]Enter your API Key[/bold green]")
            self.save_api_key_to_env(api_key.strip())
            self.console.print("[green][OK] API Key saved permanently to .env file![/green]")
        else:
            masked = curr_key[:6] + "..." + curr_key[-4:]
            self.console.print(f"\n[green][OK] Existing API Key detected: {masked}[/green]")
            if Confirm.ask("Would you like to change your API Key?", default=False):
                api_key = Prompt.ask("[bold green]Enter new API Key[/bold green]")
                self.save_api_key_to_env(api_key.strip())
                self.console.print("[green][OK] API Key updated![/green]")

        # 2. Resume PDF Path
        self.console.print("\n[bold yellow]Step 2: Resume PDF[/bold yellow]")
        default_resume = str(RESUME_PATH) if RESUME_PATH.exists() else ""
        resume_input = Prompt.ask(
            "[bold green]Enter path to your Resume PDF[/bold green]",
            default=default_resume or "config/resume.pdf"
        )
        src_resume = Path(resume_input.strip('"').strip("'"))
        if src_resume.exists():
            RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)
            if src_resume.resolve() != RESUME_PATH.resolve():
                shutil.copy2(src_resume, RESUME_PATH)
            self.console.print(f"[green][OK] Resume configured at {RESUME_PATH.name}![/green]")
        else:
            self.console.print("[yellow]Warning: Provided file not found, creating placeholder resume.[/yellow]")

        # 3. Dynamic AI Extraction
        self.console.print("\n[bold cyan]Extracting all details dynamically from your resume using latest Gemini AI...[/bold cyan]")
        parsed = self.parse_resume_to_dict(RESUME_PATH) if RESUME_PATH.exists() else {}

        # Display Extracted Summary Table
        self.console.print("\n[bold green]=== Deep Resume Extraction Complete ===[/bold green]")
        ext_table = Table(title="[bold cyan]AI-Extracted Details from Resume[/bold cyan]")
        ext_table.add_column("Property", style="bold white", width=22)
        ext_table.add_column("Extracted Value", style="cyan")

        ext_table.add_row("Full Name", parsed.get("full_name") or "Not detected")
        ext_table.add_row("Contact Phone", parsed.get("phone") or "Not detected")
        ext_table.add_row("Email", parsed.get("email") or "Not detected")
        ext_table.add_row("Location", parsed.get("location") or "Not detected")
        ext_table.add_row("Current Role", parsed.get("designation") or "Not detected")
        ext_table.add_row("Current Employer", parsed.get("current_company") or "Not detected")
        ext_table.add_row("Total Experience", f"{parsed.get('total_experience_years', 1.0)} Years")
        if parsed.get("highest_education"):
            ext_table.add_row("Highest Education", parsed.get("highest_education"))
        suggested_roles = parsed.get("suggested_target_roles", [])
        if suggested_roles:
            ext_table.add_row("Suggested Target Roles", ", ".join(suggested_roles))
        extracted_skills = parsed.get("skills", [])
        ext_table.add_row("Extracted Skills Count", f"{len(extracted_skills)} technical skills detected")

        self.console.print(ext_table)

        # Display ALL extracted skills without truncation
        if extracted_skills:
            self.console.print(f"\n[bold cyan]All Extracted Technical Skills ({len(extracted_skills)}):[/bold cyan]")
            self.console.print(f"[green]{', '.join(extracted_skills)}[/green]")

        self.console.print("\n[bold yellow]Please review and confirm each detail below (Press Enter to accept extracted value or type your edit):[/bold yellow]")

        # 4. Interactive Confirmation with User
        existing_profile: Optional[CandidateProfile] = None
        if PROFILE_PATH.exists():
            try:
                with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                    existing_profile = CandidateProfile(**json.load(f))
            except Exception:
                pass
        prefs = self.memory_service.get_all_preferences()

        # Personal
        self.console.print("\n[bold yellow]1. Personal & Contact Information[/bold yellow]")
        name = Prompt.ask("Full Name", default=parsed.get("full_name") or (existing_profile.personal.full_name if existing_profile else ""))
        email = Prompt.ask("Email Address", default=parsed.get("email") or (existing_profile.personal.email if existing_profile else ""))
        phone = Prompt.ask("Mobile Phone (10 digits without country code)", default=parsed.get("phone") or (existing_profile.personal.phone if existing_profile else ""))
        location = Prompt.ask("Location (City, State, Country)", default=parsed.get("location") or (existing_profile.personal.location if existing_profile else ""))

        # Professional
        self.console.print("\n[bold yellow]2. Professional Experience[/bold yellow]")
        designation = Prompt.ask("Current Designation / Role", default=parsed.get("designation") or (existing_profile.professional.designation if existing_profile else ""))
        company = Prompt.ask("Current Employer", default=parsed.get("current_company") or (existing_profile.professional.current_company if existing_profile else ""))
        default_exp = str(parsed.get("total_experience_years") or (existing_profile.professional.total_experience_years if existing_profile else 1.0))
        exp_years = float(Prompt.ask("Total Experience in Years", default=default_exp))

        # Skills Confirmation
        self.console.print("\n[bold yellow]3. Technical Skills Confirmation[/bold yellow]")
        self.console.print(f"[dim]Identified {len(extracted_skills)} technical skills from your resume.[/dim]")
        if extracted_skills:
            default_skills_str = ", ".join(extracted_skills)
        elif existing_profile and existing_profile.skills:
            default_skills_str = ", ".join(existing_profile.skills)
        else:
            default_skills_str = "Java, Python, SQL, Git, REST API"
        skills_raw = Prompt.ask("Confirm or edit technical skills (comma separated)", default=default_skills_str)
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

        # Target Roles Confirmation
        self.console.print("\n[bold yellow]4. Target Job Roles Confirmation (Universal)[/bold yellow]")
        self.console.print("[dim]Works for ANY role: Frontend, Backend, Fullstack, DevOps, QA, Data, Mobile, etc.[/dim]")
        if suggested_roles:
            default_roles_str = ", ".join(suggested_roles)
        elif existing_profile and existing_profile.preferred_roles:
            default_roles_str = ", ".join(existing_profile.preferred_roles)
        else:
            synth = synthesize_target_roles(designation, skills)
            default_roles_str = ", ".join(synth)
        roles_raw = Prompt.ask("Confirm or edit target job roles (comma separated)", default=default_roles_str)
        preferred_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

        # Compensation & Notice Period
        self.console.print("\n[bold yellow]5. Compensation & Notice Period[/bold yellow]")
        cur_ctc_default = ""
        if parsed.get("current_ctc_inr"):
            cur_ctc_default = str(parsed["current_ctc_inr"])
        elif existing_profile and existing_profile.professional.current_lpa and existing_profile.professional.current_lpa > 0:
            cur_ctc_default = str(int(existing_profile.professional.current_lpa * 100000))
        elif prefs.get("current_ctc_inr"):
            cur_ctc_default = str(prefs.get("current_ctc_inr"))

        if cur_ctc_default:
            cur_ctc_raw = Prompt.ask("Current CTC in INR", default=cur_ctc_default)
        else:
            cur_ctc_raw = Prompt.ask("Current CTC in INR (e.g. 600000)")

        clean_cur = re.sub(r'\D', '', cur_ctc_raw)
        cur_ctc_inr = int(clean_cur) if clean_cur else (int(cur_ctc_default) if cur_ctc_default else 0)
        cur_lpa = round(cur_ctc_inr / 100000, 2)

        exp_ctc_default = ""
        if parsed.get("expected_ctc_inr"):
            exp_ctc_default = str(parsed["expected_ctc_inr"])
        elif existing_profile and existing_profile.professional.expected_lpa and existing_profile.professional.expected_lpa > 0:
            exp_ctc_default = str(int(existing_profile.professional.expected_lpa * 100000))
        elif prefs.get("expected_ctc_inr"):
            exp_ctc_default = str(prefs.get("expected_ctc_inr"))
        elif cur_ctc_inr > 0:
            exp_ctc_default = str(int(cur_ctc_inr * 1.3))

        if exp_ctc_default:
            exp_ctc_raw = Prompt.ask("Expected CTC in INR", default=exp_ctc_default)
        else:
            exp_ctc_raw = Prompt.ask("Expected CTC in INR (e.g. 900000)")

        clean_exp = re.sub(r'\D', '', exp_ctc_raw)
        exp_ctc_inr = int(clean_exp) if clean_exp else (int(exp_ctc_default) if exp_ctc_default else 0)
        exp_lpa = round(exp_ctc_inr / 100000, 2)

        notice_default = ""
        if parsed.get("notice_period_days"):
            notice_default = str(parsed["notice_period_days"])
        elif existing_profile and existing_profile.professional.notice_period_days:
            notice_default = str(existing_profile.professional.notice_period_days)
        elif prefs.get("notice_period_days"):
            notice_default = str(prefs.get("notice_period_days"))
        else:
            notice_default = "30"

        notice_raw = Prompt.ask("Notice Period in Calendar Days (e.g. 15, 30, 60, 90)", default=notice_default)
        clean_notice = re.sub(r'\D', '', notice_raw)
        notice_days = int(clean_notice) if clean_notice else int(notice_default)

        # Target Platforms & Locations
        self.console.print("\n[bold yellow]6. Target Platforms & Preferred Locations[/bold yellow]")
        def_loc = ", ".join(existing_profile.preferred_locations) if (existing_profile and existing_profile.preferred_locations) else (parsed.get("location") or "Remote, Hybrid")
        loc_raw = Prompt.ask("Preferred Job Locations (comma separated)", default=def_loc)
        preferred_locations = [l.strip() for l in loc_raw.split(",") if l.strip()]

        platform_choice = Prompt.ask(
            "Target Platforms to Apply",
            choices=["all", "linkedin", "naukri"],
            default="all"
        )
        platforms = ["linkedin", "naukri"] if platform_choice == "all" else [platform_choice]

        # 5. Assemble CandidateProfile
        profile = CandidateProfile(
            personal=PersonalInfo(full_name=name, email=email, phone=phone, location=location),
            professional=ProfessionalInfo(
                designation=designation,
                total_experience_years=exp_years,
                current_company=company,
                current_lpa=cur_lpa,
                expected_lpa=exp_lpa,
                notice_period_days=notice_days
            ),
            skills=skills,
            preferred_roles=preferred_roles,
            preferred_locations=preferred_locations,
            job_preferences=JobPreferences(minimum_match_score=60.0, easy_apply_only=True, require_human_approval=True)
        )

        # Save profile
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))

        # Reset singleton cache
        ProfileLoader.reset()

        # Save to agent memory
        self.memory_service.set_preference("current_ctc_inr", cur_ctc_inr)
        self.memory_service.set_preference("expected_ctc_inr", exp_ctc_inr)
        self.memory_service.set_preference("notice_period_days", notice_days)
        self.memory_service.set_preference("preferred_platforms", platforms)
        self.memory_service.set_preference("total_experience_years", exp_years)
        self.memory_service.add_conversation_note(f"Candidate profile initialized for {name} ({designation}).")
        self.memory_service.add_conversation_note(f"Target roles: {', '.join(preferred_roles)}.")

        self.console.print("\n[bold green][OK] Setup Complete! All configurations saved permanently.[/bold green]")
        self.display_status(profile)

        # Helpful guidance panel for new users
        self.console.print(Panel(
            "[bold cyan]📌 Important Notes for New Users:[/bold cyan]\n\n"
            "1. [bold yellow]🔑 Step 1 - Platform Sign-In (Recommended First):[/bold yellow]\n"
            "   Select Option [bold cyan][1][/bold cyan] from the main menu to sign into your LinkedIn and/or Naukri accounts.\n"
            "   A visible browser window opens. Sign in once (complete 2FA/OTP if prompted).\n"
            "   [dim]Your session cookies are saved permanently in your local browser profile, so you never have to sign in again![/dim]\n\n"
            "2. [bold green]🚀 Step 2 - Direct Apply to Jobs:[/bold green]\n"
            "   Select Option [bold cyan][2][/bold cyan] to search and apply autonomously (or enable Safe DRY-RUN mode to test first).\n\n"
            "3. [bold blue]🖥️ Step 3 - Web Dashboard or AI Chat:[/bold blue]\n"
            "   Use Option [bold cyan][5][/bold cyan] (`jobagent --ui`) for the Web UI Control Center, or Option [bold cyan][3][/bold cyan] (`jobagent --chat`) for AI conversation.",
            title="[bold green]Quick Start Guide[/bold green]",
            border_style="green"
        ))

        # Ask to review resume with AI
        if Confirm.ask("\nWould you like to run an AI ATS Resume Review now?", default=True):
            await self.run_resume_review(profile)

        return profile

    async def run_resume_review(self, profile: Optional[CandidateProfile] = None) -> None:
        """Conducts an AI resume review and prints actionable ATS recommendations."""
        if not RESUME_PATH.exists():
            self.console.print("[red]No resume PDF found at config/resume.pdf[/red]")
            return

        if not profile:
            if not PROFILE_PATH.exists():
                self.console.print("[red]Candidate profile not found. Run setup first.[/red]")
                return
            with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                profile = CandidateProfile(**json.load(f))

        self.console.print("\n[bold cyan]=== AI Resume Review & Gap Analysis ===[/bold cyan]")
        self.console.print("[dim]Analyzing resume with LLM against ATS benchmarks...[/dim]")

        reviewer = ResumeReviewer()
        review = await reviewer.review_resume(str(RESUME_PATH), profile)

        score = review.get("ats_score", 80)
        score_color = "green" if score >= 80 else "yellow" if score >= 65 else "red"

        self.console.print(f"\n[bold]ATS Readiness Score:[/bold] [{score_color}]{score} / 100[/{score_color}]")
        self.console.print(f"[bold]Summary:[/bold] {review.get('executive_summary', '')}\n")

        # Missing information
        missing = review.get("missing_information", [])
        if missing:
            table_missing = Table(title="[bold red]Missing Information & Weak Elements[/bold red]")
            table_missing.add_column("#", style="dim", width=4)
            table_missing.add_column("Identified Gap", style="white")
            for i, item in enumerate(missing, 1):
                table_missing.add_row(str(i), item)
            self.console.print(table_missing)

        # Skill gaps
        gaps = review.get("skill_gaps", [])
        if gaps:
            table_gaps = Table(title="[bold yellow]In-Demand Skills Missing for Target Roles[/bold yellow]")
            table_gaps.add_column("Skill / Technology", style="cyan")
            for s in gaps:
                table_gaps.add_row(s)
            self.console.print(table_gaps)

        # Recommendations
        recs = review.get("recommendations", [])
        if recs:
            table_rec = Table(title="[bold green]Concrete Suggestions for Better Recruiter Selection[/bold green]")
            table_rec.add_column("Area", style="bold white")
            table_rec.add_column("Actionable Improvement", style="cyan")
            for r in recs:
                table_rec.add_row(r.get("area", "Improvement"), r.get("actionable_fix", ""))
            self.console.print(table_rec)

    def display_status(self, profile: Optional[CandidateProfile] = None) -> None:
        """Display rich formatted status dashboard of profile, memory, and settings."""
        if not profile:
            if not self.has_configured_profile():
                self.console.print("[yellow]No candidate profile configured yet. Run 'jobagent' or 'jobagent --setup' to get started.[/yellow]")
                return
            with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                profile = CandidateProfile(**json.load(f))

        prefs = self.memory_service.get_all_preferences()
        api_key = settings.GEMINI_API_KEY or settings.OPENAI_API_KEY or self._get_env_key("GEMINI_API_KEY") or self._get_env_key("OPENAI_API_KEY")
        key_status = "[bold green]Configured[/bold green]" if api_key else "[bold yellow]Not set (Offline NLP active)[/bold yellow]"

        table = Table(title="[bold cyan]Agent Status & Candidate Profile[/bold cyan]")
        table.add_column("Property", style="bold white")
        table.add_column("Configured Value", style="cyan")

        table.add_row("Candidate Name", profile.personal.full_name or profile.name or "Not configured")
        table.add_row("Contact Phone", profile.personal.phone or "Not configured")
        table.add_row("Email", profile.personal.email or "Not configured")
        table.add_row("Location", profile.personal.location or "Not configured")
        table.add_row("Current Title", profile.professional.designation or profile.current_role or "Not configured")
        table.add_row("Current Company", profile.professional.current_company or "Not configured")
        table.add_row("Total Experience", f"{profile.professional.total_experience_years} Years" if profile.professional.total_experience_years else "Not configured")

        cur_lpa = profile.professional.current_lpa or profile.current_ctc_lpa or 0.0
        exp_lpa = profile.professional.expected_lpa or profile.expected_ctc_lpa or 0.0
        cur_ctc_inr = prefs.get('current_ctc_inr') or (int(cur_lpa * 100000) if cur_lpa > 0 else None)
        exp_ctc_inr = prefs.get('expected_ctc_inr') or (int(exp_lpa * 100000) if exp_lpa > 0 else None)

        cur_disp = f"INR {cur_ctc_inr:,} ({cur_lpa} LPA)" if cur_ctc_inr else ("Not specified" if cur_lpa == 0 else f"{cur_lpa} LPA")
        exp_disp = f"INR {exp_ctc_inr:,} ({exp_lpa} LPA)" if exp_ctc_inr else ("Not specified" if exp_lpa == 0 else f"{exp_lpa} LPA")
        notice_val = profile.professional.notice_period_days or profile.notice_period_days
        notice_disp = f"{notice_val} Days (~{round(notice_val / 7)} Weeks)" if notice_val else "Not specified"

        table.add_row("Current CTC", cur_disp)
        table.add_row("Expected CTC", exp_disp)
        table.add_row("Notice Period", notice_disp)
        table.add_row("Target Roles", ", ".join(profile.preferred_roles) if profile.preferred_roles else "Not configured")
        table.add_row("Target Platforms", ", ".join(prefs.get("preferred_platforms", ["linkedin", "naukri"])))
        table.add_row("Default Date Filter", prefs.get("default_date_filter", "24h (Latest)"))
        table.add_row("AI API Key", key_status)
        table.add_row("Resume File", RESUME_PATH.name if RESUME_PATH.exists() else "Missing")

        self.console.print(table)

    async def edit_details(self) -> None:
        """Interactive editor to quickly modify specific profile fields without re-running full setup."""
        if not PROFILE_PATH.exists():
            self.console.print("[yellow]No profile found. Starting setup wizard instead...[/yellow]")
            await self.run_setup_wizard()
            return

        with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
            profile = CandidateProfile(**json.load(f))

        prefs = self.memory_service.get_all_preferences()

        self.console.print(Panel.fit(
            "[bold cyan]Edit Candidate Profile & Preferences[/bold cyan]\n"
            "[dim]Press Enter to keep current values, or type new values.[/dim]",
            border_style="cyan"
        ))

        # Optional: Sync fresh details from uploaded resume PDF
        if RESUME_PATH.exists():
            if Confirm.ask("Sync & pre-populate details from currently uploaded resume PDF?", default=False):
                self.console.print("[dim]Re-extracting details from uploaded resume PDF...[/dim]")
                parsed = self.parse_resume_to_dict(RESUME_PATH)
                if parsed.get("full_name"):
                    profile.personal.full_name = parsed["full_name"]
                    profile.name = parsed["full_name"]
                if parsed.get("email"):
                    profile.personal.email = parsed["email"]
                if parsed.get("phone"):
                    profile.personal.phone = parsed["phone"]
                if parsed.get("location"):
                    profile.personal.location = parsed["location"]
                if parsed.get("designation"):
                    profile.professional.designation = parsed["designation"]
                    profile.current_role = parsed["designation"]
                if parsed.get("current_company"):
                    profile.professional.current_company = parsed["current_company"]
                if parsed.get("total_experience_years"):
                    exp_val = float(parsed["total_experience_years"])
                    profile.professional.total_experience_years = exp_val
                    profile.experience_years = exp_val
                if parsed.get("skills"):
                    for s in parsed["skills"]:
                        if s not in profile.skills:
                            profile.skills.append(s)
                if parsed.get("suggested_target_roles"):
                    profile.preferred_roles = list(parsed["suggested_target_roles"])
                    profile.target_roles = list(parsed["suggested_target_roles"])
                self.console.print("[green][OK] Successfully refreshed details from resume! You can now verify or edit each field below:[/green]\n")

        # 1. Personal Information
        self.console.print("\n[bold yellow]1. Personal Information[/bold yellow]")
        name = Prompt.ask("Full Name", default=profile.personal.full_name or profile.name or "")
        email = Prompt.ask("Email Address", default=profile.personal.email or "")
        phone = Prompt.ask("Mobile Phone (10 digits)", default=profile.personal.phone or "")
        location = Prompt.ask("Location (City, State, Country)", default=profile.personal.location or "")

        clean_p = re.sub(r'\D', '', phone)
        if clean_p.startswith('91') and len(clean_p) == 12:
            clean_p = clean_p[2:]
        phone = clean_p

        profile.personal.full_name = name
        profile.personal.email = email
        profile.personal.phone = phone
        profile.personal.location = location
        profile.name = name

        # 2. Professional Details
        self.console.print("\n[bold yellow]2. Professional Details[/bold yellow]")
        designation = Prompt.ask("Current Designation / Role", default=profile.professional.designation or profile.current_role or "")
        company = Prompt.ask("Current Employer", default=profile.professional.current_company or "")
        default_exp = str(profile.professional.total_experience_years or profile.experience_years or 1.0)
        try:
            exp_years = float(Prompt.ask("Total Experience in Years", default=default_exp))
        except Exception:
            exp_years = float(default_exp)

        profile.professional.designation = designation
        profile.professional.current_company = company
        profile.professional.total_experience_years = exp_years
        profile.current_role = designation
        profile.experience_years = exp_years

        # 3. CTC
        self.console.print("\n[bold yellow]3. Compensation (CTC)[/bold yellow]")
        curr_ctc_default = str(prefs.get("current_ctc_inr", int(profile.professional.current_lpa * 100000)))
        exp_ctc_default = str(prefs.get("expected_ctc_inr", int(profile.professional.expected_lpa * 100000)))
        
        cur_raw = Prompt.ask("Current CTC in INR", default=curr_ctc_default)
        exp_raw = Prompt.ask("Expected CTC in INR", default=exp_ctc_default)
        try:
            cur_ctc_inr = int(re.sub(r'\D', '', cur_raw))
        except Exception:
            cur_ctc_inr = int(curr_ctc_default)
        try:
            exp_ctc_inr = int(re.sub(r'\D', '', exp_raw))
        except Exception:
            exp_ctc_inr = int(exp_ctc_default)

        cur_lpa = round(cur_ctc_inr / 100000, 2)
        exp_lpa = round(exp_ctc_inr / 100000, 2)
        profile.professional.current_lpa = cur_lpa
        profile.professional.expected_lpa = exp_lpa
        profile.current_ctc_lpa = cur_lpa
        profile.expected_ctc_lpa = exp_lpa
        self.memory_service.set_preference("current_ctc_inr", cur_ctc_inr)
        self.memory_service.set_preference("expected_ctc_inr", exp_ctc_inr)

        # 4. Notice Period
        self.console.print("\n[bold yellow]4. Notice Period[/bold yellow]")
        notice_default = str(profile.professional.notice_period_days or 30)
        notice_days = int(Prompt.ask("Notice Period in Calendar Days", default=notice_default))
        profile.professional.notice_period_days = notice_days
        profile.notice_period_days = notice_days
        self.memory_service.set_preference("notice_period_days", notice_days)

        # 5. Target Roles
        self.console.print("\n[bold yellow]5. Target Job Roles (Universal)[/bold yellow]")
        roles_default = ", ".join(profile.preferred_roles or profile.target_roles or [])
        roles_raw = Prompt.ask("Target Job Roles (comma separated)", default=roles_default)
        profile.preferred_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
        profile.target_roles = list(profile.preferred_roles)

        # 6. Preferred Locations
        self.console.print("\n[bold yellow]6. Preferred Job Locations[/bold yellow]")
        loc_default = ", ".join(profile.preferred_locations)
        loc_raw = Prompt.ask("Preferred Locations (comma separated)", default=loc_default)
        profile.preferred_locations = [l.strip() for l in loc_raw.split(",") if l.strip()]

        # 7. Target Platforms
        self.console.print("\n[bold yellow]7. Target Platforms[/bold yellow]")
        curr_plat = "all" if len(prefs.get("preferred_platforms", [])) > 1 else (prefs.get("preferred_platforms", ["all"])[0] if prefs.get("preferred_platforms") else "all")
        plat_choice = Prompt.ask(
            "Select Platforms to Apply",
            choices=["linkedin", "naukri", "all"],
            default=curr_plat
        )
        platforms = ["linkedin", "naukri"] if plat_choice == "all" else [plat_choice]
        self.memory_service.set_preference("preferred_platforms", platforms)

        # 8. Technical Skills
        self.console.print("\n[bold yellow]8. Core Technical Skills[/bold yellow]")
        skills_default = ", ".join(profile.skills)
        skills_raw = Prompt.ask("Skills (comma separated)", default=skills_default)
        profile.skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

        # Ensure root attributes are in sync
        profile.name = profile.personal.full_name
        profile.current_role = profile.professional.designation
        profile.experience_years = profile.professional.total_experience_years
        profile.current_ctc_lpa = cur_lpa
        profile.expected_ctc_lpa = exp_lpa
        profile.notice_period_days = notice_days

        # Save to disk
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))

        # Invalidate singleton cache and seed memory
        ProfileLoader.reset()
        self.memory_service.seed_from_profile(profile)

        self.memory_service.add_conversation_note(
            f"User updated profile details for {name} ({designation}): Target Roles: {', '.join(profile.preferred_roles)}; "
            f"CTC: INR {cur_ctc_inr:,} -> INR {exp_ctc_inr:,}; Notice: {notice_days} days."
        )

        self.console.print("\n[bold green][OK] Profile and preferences updated successfully![/bold green]")
        self.display_status(profile)

    async def update_resume(self, new_resume_path: Path) -> None:
        """Update the resume file, re-extract skills, and run ATS review."""
        if not new_resume_path or not new_resume_path.exists() or not new_resume_path.is_file():
            self.console.print(f"[bold red]Error: Resume file '{new_resume_path}' not found or is invalid.[/bold red]")
            return

        RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(new_resume_path, RESUME_PATH)
        self.console.print(f"[bold green][OK] Copied new resume to {RESUME_PATH.name}![/bold green]")

        self.console.print("[dim]Re-extracting candidate details and skills from updated resume with Gemini...[/dim]")
        parsed = self.parse_resume_to_dict(RESUME_PATH)

        # Display Extracted Summary
        self.console.print("\n[bold green]=== Details Extracted from New Resume ===[/bold green]")
        ext_table = Table(title="[bold cyan]AI-Extracted Details[/bold cyan]")
        ext_table.add_column("Property", style="bold white", width=22)
        ext_table.add_column("Extracted Value", style="cyan")
        ext_table.add_row("Full Name", parsed.get("full_name") or "Not detected")
        ext_table.add_row("Phone", parsed.get("phone") or "Not detected")
        ext_table.add_row("Email", parsed.get("email") or "Not detected")
        ext_table.add_row("Role / Title", parsed.get("designation") or "Not detected")
        ext_table.add_row("Company", parsed.get("current_company") or "Not detected")
        ext_table.add_row("Total Experience", f"{parsed.get('total_experience_years', 1.0)} Years")
        suggested_roles = parsed.get("suggested_target_roles", [])
        if suggested_roles:
            ext_table.add_row("Suggested Roles", ", ".join(suggested_roles))
        extracted_skills = parsed.get("skills", [])
        ext_table.add_row("Extracted Skills Count", f"{len(extracted_skills)} technical skills detected")
        self.console.print(ext_table)

        if extracted_skills:
            self.console.print(f"\n[bold cyan]All Extracted Technical Skills ({len(extracted_skills)}):[/bold cyan]")
            self.console.print(f"[green]{', '.join(extracted_skills)}[/green]")

        if PROFILE_PATH.exists():
            with open(PROFILE_PATH, "r", encoding="utf-8-sig") as f:
                profile = CandidateProfile(**json.load(f))
        else:
            profile = CandidateProfile(
                personal=PersonalInfo(
                    full_name=parsed.get("full_name") or "",
                    email=parsed.get("email") or "",
                    phone=parsed.get("phone") or "",
                    location=parsed.get("location") or ""
                ),
                professional=ProfessionalInfo(
                    designation=parsed.get("designation") or "",
                    total_experience_years=float(parsed.get("total_experience_years", 1.0)),
                    current_company=parsed.get("current_company") or "",
                    notice_period_days=30
                ),
                skills=extracted_skills,
                preferred_roles=suggested_roles or synthesize_target_roles(parsed.get("designation", ""), extracted_skills),
                preferred_locations=[parsed.get("location")] if parsed.get("location") else ["Remote", "Hybrid"]
            )

        # Apply newly extracted values as defaults into profile
        if parsed.get("full_name"):
            profile.personal.full_name = parsed["full_name"]
            profile.name = parsed["full_name"]
        if parsed.get("email"):
            profile.personal.email = parsed["email"]
        if parsed.get("phone"):
            profile.personal.phone = parsed["phone"]
        if parsed.get("location"):
            profile.personal.location = parsed["location"]
            if parsed["location"] not in profile.preferred_locations:
                profile.preferred_locations.insert(0, parsed["location"])
        if parsed.get("designation"):
            profile.professional.designation = parsed["designation"]
            profile.current_role = parsed["designation"]
        if parsed.get("current_company"):
            profile.professional.current_company = parsed["current_company"]
        if parsed.get("total_experience_years"):
            exp_num = float(parsed["total_experience_years"])
            profile.professional.total_experience_years = exp_num
            profile.experience_years = exp_num

        # Prompt user to confirm or edit details
        self.console.print("\n[bold yellow]Please review and confirm details extracted from new resume:[/bold yellow]")
        profile.personal.full_name = Prompt.ask("Full Name", default=profile.personal.full_name or profile.name or "")
        profile.name = profile.personal.full_name
        profile.personal.email = Prompt.ask("Email Address", default=profile.personal.email or "")
        profile.personal.phone = Prompt.ask("Mobile Phone (10 digits)", default=profile.personal.phone or "")
        profile.personal.location = Prompt.ask("Location (City, State, Country)", default=profile.personal.location or "")

        profile.professional.designation = Prompt.ask("Current Designation / Role", default=profile.professional.designation or profile.current_role or "")
        profile.current_role = profile.professional.designation
        profile.professional.current_company = Prompt.ask("Current Employer", default=profile.professional.current_company or "")
        try:
            exp_input = float(Prompt.ask("Total Experience in Years", default=str(profile.professional.total_experience_years or profile.experience_years or 1.0)))
            profile.professional.total_experience_years = exp_input
            profile.experience_years = exp_input
        except Exception:
            pass

        # Prompt user to confirm / edit skills
        self.console.print("\n[bold yellow]Confirm or update skills for resume:[/bold yellow]")
        combined_skills = list(profile.skills)
        for s in extracted_skills:
            if s not in combined_skills:
                combined_skills.append(s)

        skills_raw = Prompt.ask("Technical skills (comma separated)", default=", ".join(combined_skills))
        profile.skills = [s.strip() for s in skills_raw.split(",") if s.strip()]

        # Prompt user to confirm / edit target roles
        if suggested_roles:
            roles_raw = Prompt.ask("Target job roles (comma separated)", default=", ".join(suggested_roles))
            profile.preferred_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
        else:
            synth = synthesize_target_roles(profile.professional.designation, profile.skills)
            roles_raw = Prompt.ask("Target job roles (comma separated)", default=", ".join(synth))
            profile.preferred_roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
        profile.target_roles = list(profile.preferred_roles)

        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            f.write(profile.model_dump_json(indent=2))

        # Invalidate singleton cache and update memory
        ProfileLoader.reset()
        self.memory_service.seed_from_profile(profile)
        self.memory_service.set_preference("total_experience_years", profile.professional.total_experience_years)

        self.console.print(f"[green][OK] Candidate profile and {len(profile.skills)} technical skills updated successfully![/green]")

        self.memory_service.add_conversation_note(f"User updated resume file from {new_resume_path.name}.")

        self.console.print("\n[bold cyan]Running AI ATS Gap Analysis on your new resume...[/bold cyan]")
        await self.run_resume_review(profile)

