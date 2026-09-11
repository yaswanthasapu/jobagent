import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from config.settings import settings
from models.profile import CandidateProfile

logger = logging.getLogger(__name__)

class ResumeService:
    def __init__(self, resume_path: Optional[str] = None):
        self.resume_path = Path(resume_path or settings.RESUME_PATH)
        self.meta_path = self.resume_path.parent / "resume_meta.json"

    def get_resume_path(self, candidate_profile: Optional[CandidateProfile] = None) -> str:
        """
        Return the absolute path of the resume PDF.
        If the file does not exist, automatically generate a professional PDF.
        """
        if not self.resume_path.exists():
            self._generate_default_resume(candidate_profile)
        return str(self.resume_path.resolve())

    def extract_text(self) -> str:
        """
        Extract all text from the candidate resume PDF.
        """
        if not self.resume_path.exists():
            return ""
        try:
            import pypdf
            reader = pypdf.PdfReader(str(self.resume_path))
            pages_text = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text)
            return "\n\n".join(pages_text)
        except Exception as e:
            logger.error(f"Error reading PDF {self.resume_path}: {e}")
            return ""

    def get_resume_metadata(self) -> Dict[str, Any]:
        """
        Returns structured metadata regarding the currently configured resume.
        """
        original_filename = None
        if self.meta_path.exists():
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    original_filename = meta_data.get("original_filename") or meta_data.get("filename")
            except Exception:
                pass

        if not original_filename:
            try:
                from services.profile_loader import ProfileLoader
                prof = ProfileLoader.get_instance().profile
                original_filename = getattr(prof, "resume_filename", None)
            except Exception:
                pass

        display_filename = original_filename or self.resume_path.name

        if not self.resume_path.exists():
            return {
                "exists": False,
                "filename": display_filename,
                "original_filename": original_filename,
                "path": str(self.resume_path.resolve()),
                "size_bytes": 0,
                "size_kb": 0,
                "last_modified": None,
                "text_snippet": ""
            }

        stat = self.resume_path.stat()
        text = self.extract_text()
        clean_preview = " ".join(text.split())[:350]

        return {
            "exists": True,
            "filename": display_filename,
            "original_filename": original_filename,
            "path": str(self.resume_path.resolve()),
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 1),
            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "text_snippet": clean_preview,
            "full_text": text
        }

    def save_resume_bytes(self, content: bytes, original_filename: Optional[str] = None) -> Dict[str, Any]:
        """
        Saves uploaded resume binary content to disk, overwriting settings.RESUME_PATH.
        """
        self.resume_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.resume_path, "wb") as f:
            f.write(content)

        fname = original_filename or self.resume_path.name

        # Persist original filename in resume_meta.json
        try:
            meta = {
                "original_filename": fname,
                "filename": fname,
                "size_bytes": len(content),
                "size_kb": round(len(content) / 1024, 1),
                "uploaded_at": datetime.now(timezone.utc).isoformat()
            }
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save resume_meta.json: {e}")

        # Update candidate_profile.json
        try:
            from services.profile_loader import ProfileLoader
            loader = ProfileLoader.get_instance()
            prof_path = Path(settings.PROFILE_PATH)
            if prof_path.exists():
                with open(prof_path, "r", encoding="utf-8-sig") as f:
                    p_data = json.load(f)
                p_data["resume_filename"] = fname
                with open(prof_path, "w", encoding="utf-8") as f:
                    json.dump(p_data, f, indent=2)
                loader.reload()
        except Exception as e:
            logger.warning(f"Could not update candidate profile resume_filename: {e}")

        logger.info(f"Saved new candidate resume '{fname}' ({len(content)} bytes) to {self.resume_path}")
        return self.get_resume_metadata()

    def _generate_default_resume(self, profile: Optional[CandidateProfile]) -> None:
        """
        Generate a professional single-page PDF resume using reportlab.
        """
        self.resume_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib import colors

            doc = SimpleDocTemplate(str(self.resume_path), pagesize=letter, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
            styles = getSampleStyleSheet()

            name = profile.personal.full_name if profile else "Yaswanth Asapu"
            title = profile.professional.designation if profile else "QA Automation Engineer"
            email = profile.personal.email or "yaswanth.qa@example.com"
            phone = profile.personal.phone or "+91-9876543210"
            location = profile.personal.location if profile else "Hyderabad, Telangana, India"
            exp = profile.professional.total_experience_years if profile else 4
            company = profile.professional.current_company if profile else "Magellanic-Cloud"
            skills_str = ", ".join(profile.skills) if profile else "Selenium, Playwright, Java, TestNG, RestAssured"

            story = []

            # Header
            header_style = ParagraphStyle(
                'HeaderTitle',
                parent=styles['Heading1'],
                fontSize=22,
                leading=26,
                textColor=colors.HexColor("#0A66C2"),
                alignment=1
            )
            subhead_style = ParagraphStyle(
                'SubHeader',
                parent=styles['Normal'],
                fontSize=10,
                leading=14,
                textColor=colors.HexColor("#333333"),
                alignment=1
            )
            sec_title_style = ParagraphStyle(
                'SecTitle',
                parent=styles['Heading2'],
                fontSize=13,
                leading=16,
                textColor=colors.HexColor("#0A66C2"),
                spaceBefore=10,
                spaceAfter=4
            )
            body_style = ParagraphStyle(
                'Body',
                parent=styles['Normal'],
                fontSize=10,
                leading=14,
                textColor=colors.HexColor("#222222")
            )

            story.append(Paragraph(name, header_style))
            story.append(Paragraph(f"{title} | {location} | {email} | {phone}", subhead_style))
            story.append(Spacer(1, 10))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0A66C2"), spaceAfter=10))

            # Summary
            story.append(Paragraph("<b>PROFESSIONAL SUMMARY</b>", sec_title_style))
            story.append(Paragraph(
                f"Experienced <b>{title}</b> with <b>{exp} years</b> of proven experience delivering scalable test automation frameworks "
                f"using Playwright, Selenium, Java, and TypeScript. Extensive expertise in API testing (RestAssured, Postman), "
                f"CI/CD automation pipelines (Jenkins, Docker), and agile test management.",
                body_style
            ))
            story.append(Spacer(1, 8))

            # Skills
            story.append(Paragraph("<b>TECHNICAL SKILLS</b>", sec_title_style))
            story.append(Paragraph(f"<b>Test Automation:</b> Playwright, Selenium WebDriver, TestNG, Cucumber (BDD), JMeter", body_style))
            story.append(Paragraph(f"<b>Languages:</b> Java, TypeScript, JavaScript, SQL", body_style))
            story.append(Paragraph(f"<b>API & Web Services:</b> RestAssured, Postman, REST API Testing", body_style))
            story.append(Paragraph(f"<b>DevOps & Tools:</b> Jenkins, Git, Docker, JIRA, TestRail", body_style))
            story.append(Spacer(1, 8))

            # Experience
            story.append(Paragraph("<b>PROFESSIONAL EXPERIENCE</b>", sec_title_style))
            story.append(Paragraph(f"<b>{company}</b> — <i>{title}</i> (2022 – Present)", body_style))
            story.append(Paragraph("• Designed, developed, and maintained robust end-to-end automation frameworks with Playwright and Selenium.", body_style))
            story.append(Paragraph("• Implemented automated API test suites using RestAssured and Postman, decreasing regression cycle time by 45%.", body_style))
            story.append(Paragraph("• Integrated automated test suites into Jenkins CI/CD pipelines with real-time reporting.", body_style))
            story.append(Paragraph("• Mentored junior engineers and collaborated closely with product managers and developers in Agile sprints.", body_style))

            doc.build(story)
            logger.info(f"Generated default candidate resume at {self.resume_path}")
        except Exception as e:
            logger.warning(f"Could not generate PDF with reportlab ({e}). Creating blank placeholder.")
            with open(self.resume_path, "wb") as f:
                f.write(b"%PDF-1.4 Placeholder resume")
