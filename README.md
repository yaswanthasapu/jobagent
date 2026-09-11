# ⚡ JobAgent - Autonomous Multi-Platform AI Job Application Agent

[![PyPI version](https://img.shields.io/pypi/v/jobagent.svg?color=blue)](https://pypi.org/project/jobagent/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green.svg)](https://playwright.dev/)

An autonomous AI agent designed to search, evaluate, match technical skills, dynamically fill application forms, and submit job applications across **LinkedIn Easy Apply** and **Naukri.com Quick Apply**.

Equipped with a **Web UI Control Center**, **Interactive Conversational AI Chat**, **ATS Resume Reviewer**, **Multi-Page Pagination**, and **Strict Technical Skill Matching**.

---

## 📌 IMPORTANT NOTES FOR NEW USERS (READ BEFORE FIRST RUN)

> [!IMPORTANT]
> 1. **Zero Credential / Password Storage**:
>    JobAgent **never** asks for, accesses, or saves your LinkedIn or Naukri passwords in plaintext or files. Authentication occurs strictly inside a visible Playwright Chromium browser window.
>
> 2. **One-Time Platform Sign-In (Option `[1]` / `jobagent --signin`)**:
>    When you first launch `jobagent`, choose **Option `[1] 🔑 Sign In / Verify Platform Accounts`** (or run `jobagent --signin`). A visible browser opens allowing you to log into LinkedIn and/or Naukri once (including entering your 2FA/OTP if enabled).
>    Your authenticated session cookies and security tokens are **permanently saved** inside your local user data directory (`.browser_context/`). All subsequent runs reuse these saved sessions without asking you to log in again.
>
> 3. **Automatic Playwright Browser Installation**:
>    JobAgent features self-healing browser launching. If Playwright's Chromium browser is missing on your machine (common on new `pip` installs or Python 3.14+), JobAgent detects this and downloads Chromium binaries automatically on first startup. You do not need to run manual terminal commands.
>
> 4. **Safe DRY-RUN Mode Available**:
>    If you want to test the entire automation pipeline safely without actually submitting applications, toggle **Safe DRY-RUN Mode** (`--dry-run` or select `Yes` when prompted). The agent will search, evaluate, load jobs, and fill out forms up to the final review step, then cleanly dismiss the dialog.
>
> 5. **Candidate Profile as Single Source of Truth**:
>    During the first-time setup wizard, JobAgent parses your resume PDF, extracts your technical skills, experience, designation, and target roles, and saves your profile to `candidate_profile.json`. You can update your resume PDF anytime via Option `[8]` or edit preferences via Option `[7]`.
>
> 6. **Multi-User Privacy & Data Isolation**:
>    JobAgent is completely user-agnostic and privacy-first. There are zero hardcoded personal details, phone numbers, or employer names in the shared source code. Every user gets an isolated local profile, local browser context, and application database.

---

## 🚀 Quick Start Guide

### Option A: Install from GitHub (Source)

```bash
# 1. Clone the repository
git clone https://github.com/yaswanthasapu/jobagent.git
cd jobagent

# 2. Create and activate a virtual environment
# Windows (PowerShell):
python -m venv venv
.\venv\Scripts\Activate.ps1

# macOS / Linux:
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies & CLI tool
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 4. Install Playwright browser
playwright install chromium

# 5. Launch the agent
jobagent
```

### Option B: Install via pip (PyPI)

```bash
pip install --upgrade jobagent
jobagent
```

---

## ⚙️ First-Time Onboarding Flow

On your first run (`jobagent` or `python main.py`):
1. **API Key Setup**: Free Google Gemini API Key (or OpenAI Key) saved permanently to your local `.env`.
2. **Resume Extraction**: Enter path to your resume PDF. The agent dynamically parses your skills, experience, designation, and suggests target roles.
3. **Review & Confirm**: Review and confirm your details (CTC, notice period, preferred locations).
4. **Platform Sign-In**: Choose Option **`[1]`** to log into LinkedIn & Naukri once in the visible browser. Session cookies are permanently saved.
5. **Start Applying**: Choose Option **`[2]`** to apply, Option **`[5]`** for the Web Dashboard, or Option **`[3]`** for AI chat!

---

## 🎮 Interactive Menu Options

When you run `jobagent`, you are greeted with the interactive assistant:

```text
=====================================================
           AI Job Application Agent CLI              
  Autonomous Multi-Platform Applications: LinkedIn & Naukri 
=====================================================

What would you like to do?
  [1] 🔑 Sign In / Verify Platform Accounts (Save LinkedIn & Naukri login permanently)
  [2] 🚀 Direct Apply to Jobs (LinkedIn, Naukri, or Both)
  [3] 💬 Chat with Agent (Search & apply, update details, ask questions)
  [4] 📋 View All Applied Jobs (Full history, links & status)
  [5] 🖥️  Launch Professional Web UI Dashboard
  [6] 📄 Run AI ATS Resume Review & Gap Analysis
  [7] ✏️  Edit Candidate Profile & Preferences
  [8] 📎 Update Resume PDF
  [9] 📊 View Session History & Memory Rules
  [0] 🚪 Exit
```

---

## 💻 CLI Command Line Shortcuts

You can also run specific modes directly via CLI flags:

| Command | Action |
|---|---|
| `jobagent` | Launch interactive menu |
| `jobagent --signin` | Open visible browser to sign into LinkedIn/Naukri and save session |
| `jobagent --ui` | Launch Web UI Control Center at `http://127.0.0.1:8000` |
| `jobagent --chat` | Start interactive conversational chat mode |
| `jobagent --applied-jobs` | View full history table of applied jobs (with local timestamps) |
| `jobagent --review-resume` | Run AI ATS Resume Review & Gap Analysis |
| `jobagent --keyword "SDET" --location "Hyderabad" --max-jobs 10` | Direct search & apply |
| `jobagent --keyword "QA" --dry-run` | Run in safe simulation mode (no submissions) |
| `jobagent --auto-approve` | Autonomous mode (skips manual confirmation step) |

---

## 🧠 Core Architecture Highlights

### 1. Strict Technical Skill Matching & Incompatible Stack Gate
- **50% Technical Skills | 30% Experience Fit | 20% Role Relevance**.
- Detects incompatible programming languages and frameworks (e.g. skips Python/C# postings if candidate's core stack is Java/Selenium/Playwright).
- Requires at least 50% technical match before proceeding; non-matching posts are automatically skipped and logged with clear reasoning.

### 2. LinkedIn Multi-Page Search Pagination & Query Precision
- Supports paginating across pages (`start=0, 25, 50, 75...`) to process up to 100+ job applications.
- Enriches short queries (e.g. expanding `"QA"` to `'"QA" OR "Quality Assurance" OR "SDET" OR "Test Automation"'`) to eliminate irrelevant postings.
- Deep scrolling ensures up to 25 job cards per page are extracted.

### 3. Dynamic Form Filling & Memory Engine
- Traverses multi-step Easy Apply and Quick Apply modals.
- Intelligently maps text fields, dropdowns, radio buttons, numbers, and resume uploads.
- Remembers previously answered employer questions (e.g., notice period, CTC, work authorization) in local memory so you never answer the same question twice.

### 4. Human-in-the-Loop (HITL) Gate
- Pause before submitting each job application to inspect candidate CTC, notice period, and match breakdown, or enable **Auto-Apply** for 100% autonomous operation.

---

## 🛠️ Troubleshooting & FAQs

### Q: Why is LinkedIn asking for login again?
Run Option **`[1] 🔑 Sign In / Verify Platform Accounts`** (or `jobagent --signin`). Sign into LinkedIn completely in the open browser. Once verified, session cookies are saved to `.browser_context/` and persist indefinitely across all future runs.

### Q: What happens if LinkedIn prompts for 2FA or CAPTCHA?
JobAgent detects security challenges, pauses automation, and displays an alert in the console. You can approve the prompt on your mobile LinkedIn app or solve the CAPTCHA in the open browser. The agent automatically detects verification and resumes where it left off.

### Q: How do I change my target location, role, or CTC?
- Select Option **`[7] ✏️  Edit Candidate Profile & Preferences`** from the interactive menu.
- Or select Option **`[8] 📎 Update Resume PDF`** to upload an updated resume.

### Q: Do I need an OpenAI or Gemini API Key?
An API key is **optional**. If you supply `GEMINI_API_KEY` or `OPENAI_API_KEY`, JobAgent uses LLM semantic reasoning. If left empty, JobAgent's built-in offline NLP heuristics perform deterministic extraction and matching automatically.

---

## 📄 License
MIT License. Created & maintained by [Yaswanth Asapu](mailto:yaswanth901@gmail.com).
