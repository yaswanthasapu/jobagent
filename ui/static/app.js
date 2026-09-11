/* ==========================================================================
   JobAgent Frontend Application Logic
   Pure Vanilla JavaScript for speed and zero external dependencies.
   ========================================================================== */

// State
let currentProfile = null;
let currentMemory = null;
let currentApplications = [];
let agentPollInterval = null;

document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initTagInput();
    loadDashboardStatus();
    loadProfileData();
    loadMemoryData();
    loadApplicationsData();
    loadResumeReviewData();
    loadResumeInfo();
    initResumeUpload();
    initHitlControls();

    // Setup global listeners
    document.getElementById("btn-save-profile").addEventListener("click", saveProfileData);
    document.getElementById("btn-save-memory-prefs").addEventListener("click", saveMemoryPreferences);
    document.getElementById("btn-add-answer").addEventListener("click", openAddAnswerModal);
    document.getElementById("btn-add-rule").addEventListener("click", addConversationRule);
    document.getElementById("btn-launch-agent").addEventListener("click", launchAgentAutomation);
    document.getElementById("search-jobs").addEventListener("input", filterApplicationsTable);
    document.getElementById("filter-platform").addEventListener("change", filterApplicationsTable);
    document.getElementById("search-memory").addEventListener("input", filterMemoryTable);

    // Stop Agent & Clear Logs listeners
    ["btn-stop-agent", "btn-header-stop-agent", "btn-stop-agent-logs"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("click", stopAgentAutomation);
    });
    const clearLogsBtn = document.getElementById("btn-clear-logs");
    if (clearLogsBtn) clearLogsBtn.addEventListener("click", clearTerminalLogs);

    // Check if an agent is already running and sync UI
    checkAgentStatusOnce().then(data => {
        if (data && data.is_running) {
            startAgentStatusPolling();
        }
    });
});

// Toast notification system
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(40px)";
        toast.style.transition = "all 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

// Tab navigation
function initNavigation() {
    const navItems = document.querySelectorAll(".nav-item");
    const views = document.querySelectorAll(".view-panel");

    navItems.forEach(item => {
        item.addEventListener("click", () => {
            const targetViewId = item.getAttribute("data-view");
            navItems.forEach(n => n.classList.remove("active"));
            views.forEach(v => v.classList.remove("active"));

            item.classList.add("active");
            const targetView = document.getElementById(targetViewId);
            if (targetView) targetView.classList.add("active");

            // Update header title
            const viewTitles = {
                "view-overview": { title: "Agent Dashboard", sub: "Autonomous job automation overview & metrics" },
                "view-profile": { title: "Candidate Profile", sub: "Manage and edit your professional details & skills" },
                "view-memory": { title: "Agent Memory & Knowledge", sub: "Review & edit persistent answers reused in forms" },
                "view-applications": { title: "Applied Jobs Tracker", sub: "Complete history of submitted & dry-run applications" },
                "view-resume": { title: "ATS Resume Review", sub: "AI-powered gap analysis & keyword optimization" },
                "view-runner": { title: "Direct Job Application", sub: "Configure and launch live automation with streaming logs" }
            };
            if (viewTitles[targetViewId]) {
                document.getElementById("page-title").textContent = viewTitles[targetViewId].title;
                document.getElementById("page-subtitle").textContent = viewTitles[targetViewId].sub;
            }
        });
    });
}

// 1. Dashboard Status & Overview
async function loadDashboardStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) return;
        const data = await res.json();
        
        const candName = data.candidate_name || "Candidate";
        document.getElementById("candidate-display-name").textContent = candName;
        document.getElementById("candidate-display-role").textContent = data.designation || "Software Professional";
        const avatarEl = document.getElementById("candidate-avatar");
        if (avatarEl && candName) {
            const initials = candName.split(" ").filter(Boolean).map(n => n[0].toUpperCase()).slice(0, 2).join("");
            avatarEl.textContent = initials || "JA";
        }
        document.getElementById("metric-applied-count").textContent = data.total_applied_jobs || "0";
        document.getElementById("metric-skills-count").textContent = data.skills_count || "0";
        document.getElementById("metric-answers-count").textContent = data.saved_answers_count || "0";
        document.getElementById("metric-rules-count").textContent = data.rules_count || "0";
    } catch (err) {
        console.error("Error loading status:", err);
    }
}

// 2. Candidate Profile & Tag Manager
async function loadProfileData() {
    try {
        const res = await fetch("/api/profile");
        if (!res.ok) return;
        currentProfile = await res.json();

        // Personal
        document.getElementById("prof-full-name").value = currentProfile.personal.full_name || "";
        document.getElementById("prof-email").value = currentProfile.personal.email || "";
        document.getElementById("prof-phone").value = currentProfile.personal.phone || "";
        document.getElementById("prof-location").value = currentProfile.personal.location || "";

        // Professional
        document.getElementById("prof-designation").value = currentProfile.professional.designation || "";
        document.getElementById("prof-experience").value = currentProfile.professional.total_experience_years || 0;
        document.getElementById("prof-company").value = currentProfile.professional.current_company || "";
        document.getElementById("prof-current-lpa").value = currentProfile.professional.current_lpa || 0;
        document.getElementById("prof-expected-lpa").value = currentProfile.professional.expected_lpa || 0;
        document.getElementById("prof-notice-days").value = currentProfile.professional.notice_period_days || 0;

        // Preferences
        document.getElementById("prof-roles").value = (currentProfile.preferred_roles || []).join(", ");
        document.getElementById("prof-locations").value = (currentProfile.preferred_locations || []).join(", ");
        document.getElementById("prof-min-score").value = currentProfile.job_preferences.minimum_match_score || 70;
        document.getElementById("prof-easy-apply").checked = currentProfile.job_preferences.easy_apply_only !== false;
        document.getElementById("prof-require-approval").checked = currentProfile.job_preferences.require_human_approval !== false;

        // Render Skills
        renderSkillTags(currentProfile.skills || []);
        renderExcludedSkillTags(currentProfile.excluded_skills || []);

        // Pre-populate runner defaults
        if (currentProfile.preferred_roles && currentProfile.preferred_roles.length > 0) {
            document.getElementById("run-role").value = currentProfile.preferred_roles[0];
        }
        if (currentProfile.preferred_locations && currentProfile.preferred_locations.length > 0) {
            document.getElementById("run-location").value = currentProfile.preferred_locations[0];
        }
    } catch (err) {
        console.error("Error loading profile:", err);
    }
}

function renderSkillTags(skills) {
    const container = document.getElementById("skills-tag-container");
    const input = document.getElementById("skill-inline-input");
    if (!container || !input) return;
    // Remove existing chips
    container.querySelectorAll(".tag-chip").forEach(c => c.remove());

    skills.forEach(skill => {
        const chip = document.createElement("span");
        chip.className = "tag-chip";
        chip.innerHTML = `${escapeHtml(skill)} <span class="tag-remove" data-skill="${escapeHtml(skill)}">&times;</span>`;
        chip.querySelector(".tag-remove").addEventListener("click", () => {
            currentProfile.skills = currentProfile.skills.filter(s => s !== skill);
            chip.remove();
        });
        container.insertBefore(chip, input);
    });
}

function renderExcludedSkillTags(excludedSkills) {
    const container = document.getElementById("excluded-skills-tag-container");
    const input = document.getElementById("excluded-skill-inline-input");
    if (!container || !input) return;
    container.querySelectorAll(".tag-chip").forEach(c => c.remove());

    excludedSkills.forEach(skill => {
        const chip = document.createElement("span");
        chip.className = "tag-chip";
        chip.style.borderColor = "rgba(239, 68, 68, 0.4)";
        chip.style.color = "#fca5a5";
        chip.innerHTML = `${escapeHtml(skill)} <span class="tag-remove" data-skill="${escapeHtml(skill)}">&times;</span>`;
        chip.querySelector(".tag-remove").addEventListener("click", () => {
            currentProfile.excluded_skills = (currentProfile.excluded_skills || []).filter(s => s !== skill);
            chip.remove();
        });
        container.insertBefore(chip, input);
    });
}

function initTagInput() {
    const input = document.getElementById("skill-inline-input");
    if (input) {
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                const val = input.value.trim().replace(/^,+|,+$/g, '');
                if (val && currentProfile) {
                    if (!currentProfile.skills) currentProfile.skills = [];
                    if (!currentProfile.skills.includes(val)) {
                        currentProfile.skills.push(val);
                        renderSkillTags(currentProfile.skills);
                    }
                    input.value = "";
                }
            }
        });
    }

    const exclInput = document.getElementById("excluded-skill-inline-input");
    if (exclInput) {
        exclInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                const val = exclInput.value.trim().replace(/^,+|,+$/g, '');
                if (val && currentProfile) {
                    if (!currentProfile.excluded_skills) currentProfile.excluded_skills = [];
                    if (!currentProfile.excluded_skills.includes(val)) {
                        currentProfile.excluded_skills.push(val);
                        renderExcludedSkillTags(currentProfile.excluded_skills);
                    }
                    exclInput.value = "";
                }
            }
        });
    }
}

async function saveProfileData() {
    if (!currentProfile) return;

    // Collect values
    currentProfile.personal.full_name = document.getElementById("prof-full-name").value.trim();
    currentProfile.personal.email = document.getElementById("prof-email").value.trim();
    currentProfile.personal.phone = document.getElementById("prof-phone").value.trim();
    currentProfile.personal.location = document.getElementById("prof-location").value.trim();

    currentProfile.professional.designation = document.getElementById("prof-designation").value.trim();
    currentProfile.professional.total_experience_years = parseFloat(document.getElementById("prof-experience").value) || 0;
    currentProfile.professional.current_company = document.getElementById("prof-company").value.trim();
    currentProfile.professional.current_lpa = parseFloat(document.getElementById("prof-current-lpa").value) || 0;
    currentProfile.professional.expected_lpa = parseFloat(document.getElementById("prof-expected-lpa").value) || 0;
    currentProfile.professional.notice_period_days = parseInt(document.getElementById("prof-notice-days").value) || 0;

    currentProfile.preferred_roles = document.getElementById("prof-roles").value.split(",").map(s => s.trim()).filter(Boolean);
    currentProfile.preferred_locations = document.getElementById("prof-locations").value.split(",").map(s => s.trim()).filter(Boolean);
    currentProfile.job_preferences.minimum_match_score = parseFloat(document.getElementById("prof-min-score").value) || 70;
    currentProfile.job_preferences.easy_apply_only = document.getElementById("prof-easy-apply").checked;
    currentProfile.job_preferences.require_human_approval = document.getElementById("prof-require-approval").checked;

    try {
        const res = await fetch("/api/profile", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(currentProfile)
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Candidate profile saved and synced with memory!");
            loadDashboardStatus();
            loadMemoryData();
        } else {
            showToast("Error saving profile: " + data.detail, "error");
        }
    } catch (err) {
        showToast("Failed to connect to server: " + err, "error");
    }
}

// 3. Agent Memory & Saved Form Answers
async function loadMemoryData() {
    try {
        const res = await fetch("/api/memory");
        if (!res.ok) return;
        currentMemory = await res.json();

        // Render Preferences
        const prefs = currentMemory.user_preferences || {};
        document.getElementById("pref-platform").value = (prefs.preferred_platforms && prefs.preferred_platforms.length === 1) ? prefs.preferred_platforms[0] : "all";
        document.getElementById("pref-freshness").value = prefs.default_date_filter || "24h";
        document.getElementById("pref-auto-approve").checked = prefs.auto_approve_applications === true;

        // Render Rules
        renderConversationRules(currentMemory.conversation_notes || []);

        // Render Saved Form Answers Table
        renderSavedAnswersTable(currentMemory.saved_form_answers || {});
    } catch (err) {
        console.error("Error loading memory:", err);
    }
}

function renderSavedAnswersTable(savedAnswers) {
    const tbody = document.getElementById("memory-answers-tbody");
    tbody.innerHTML = "";

    const entries = Object.entries(savedAnswers);
    if (entries.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No form answers remembered yet.</td></tr>`;
        return;
    }

    entries.forEach(([key, item]) => {
        const tr = document.createElement("tr");
        tr.setAttribute("data-key", key);

        const rawLabel = item.raw_label || key;
        const answer = item.answer || "";
        const ftype = item.field_type || "text";

        tr.innerHTML = `
            <td style="font-weight: 600; max-width: 280px; word-break: break-word;">${escapeHtml(rawLabel)}</td>
            <td>
                <input type="text" class="form-control inline-answer-input" value="${escapeHtml(answer)}" style="padding: 6px 10px; font-size: 13px;" />
            </td>
            <td><span class="badge" style="background: rgba(255,255,255,0.06); color: var(--text-muted);">${escapeHtml(ftype)}</span></td>
            <td>
                <div style="display: flex; gap: 8px;">
                    <button class="btn btn-secondary btn-sm btn-save-inline" title="Save changes">Save</button>
                    <button class="btn btn-danger btn-sm btn-delete-inline" title="Delete answer">&times;</button>
                </div>
            </td>
        `;

        // Inline Save
        tr.querySelector(".btn-save-inline").addEventListener("click", async () => {
            const newAns = tr.querySelector(".inline-answer-input").value;
            await saveAnswer(rawLabel, newAns, ftype);
        });

        // Inline Delete
        tr.querySelector(".btn-delete-inline").addEventListener("click", async () => {
            if (confirm(`Remove remembered answer for '${rawLabel}'?`)) {
                await deleteAnswer(key);
            }
        });

        tbody.appendChild(tr);
    });
}

function filterMemoryTable() {
    const query = document.getElementById("search-memory").value.toLowerCase();
    const rows = document.querySelectorAll("#memory-answers-tbody tr");
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(query) ? "" : "none";
    });
}

async function saveAnswer(label, answer, field_type) {
    try {
        const res = await fetch("/api/memory/answer", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ label, answer, field_type })
        });
        if (res.ok) {
            showToast(`Saved answer for '${label}'!`);
            loadMemoryData();
            loadDashboardStatus();
        } else {
            showToast("Failed to save answer.", "error");
        }
    } catch (err) {
        showToast("Error saving answer: " + err, "error");
    }
}

async function deleteAnswer(key) {
    try {
        const res = await fetch(`/api/memory/answer/${encodeURIComponent(key)}`, {
            method: "DELETE"
        });
        if (res.ok) {
            showToast("Answer removed from memory.");
            loadMemoryData();
            loadDashboardStatus();
        }
    } catch (err) {
        showToast("Error deleting answer: " + err, "error");
    }
}

function openAddAnswerModal() {
    const label = prompt("Enter Question Label (e.g. 'Selenium', 'Notice Period', 'Current CTC'):");
    if (!label) return;
    const answer = prompt(`Enter Answer for '${label}':`);
    if (answer === null) return;
    saveAnswer(label, answer, "text");
}

function renderConversationRules(rules) {
    const container = document.getElementById("rules-list-container");
    container.innerHTML = "";
    if (rules.length === 0) {
        container.innerHTML = `<div style="color: var(--text-muted); font-size: 13px;">No conversation rules recorded.</div>`;
        return;
    }
    rules.forEach(rule => {
        const item = document.createElement("div");
        item.style = "display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: var(--bg-input); border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); font-size: 12.5px;";
        item.innerHTML = `
            <span>${escapeHtml(rule)}</span>
            <button class="btn btn-danger btn-sm" style="padding: 2px 6px; font-size: 11px;">&times;</button>
        `;
        item.querySelector("button").addEventListener("click", async () => {
            await deleteRule(rule);
        });
        container.appendChild(item);
    });
}

async function addConversationRule() {
    const input = document.getElementById("new-rule-input");
    const rule = input.value.trim();
    if (!rule) return;
    try {
        const res = await fetch("/api/memory/rule", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rule })
        });
        if (res.ok) {
            input.value = "";
            showToast("Rule added to memory!");
            loadMemoryData();
            loadDashboardStatus();
        }
    } catch (err) {
        showToast("Error adding rule: " + err, "error");
    }
}

async function deleteRule(rule) {
    try {
        const res = await fetch(`/api/memory/rule?rule_text=${encodeURIComponent(rule)}`, {
            method: "DELETE"
        });
        if (res.ok) {
            showToast("Rule removed.");
            loadMemoryData();
        }
    } catch (err) {
        showToast("Error deleting rule: " + err, "error");
    }
}

async function saveMemoryPreferences() {
    const plat = document.getElementById("pref-platform").value;
    const freshness = document.getElementById("pref-freshness").value;
    const autoApprove = document.getElementById("pref-auto-approve").checked;

    const payload = {
        preferred_platforms: plat === "all" ? ["linkedin", "naukri"] : [plat],
        default_date_filter: freshness,
        auto_approve_applications: autoApprove
    };

    try {
        const res = await fetch("/api/memory/preferences", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            showToast("Agent preferences updated!");
        }
    } catch (err) {
        showToast("Error updating preferences: " + err, "error");
    }
}

// 4. Applied Jobs Tracker
async function loadApplicationsData() {
    try {
        const res = await fetch("/api/applications?limit=200");
        if (!res.ok) return;
        const data = await res.json();
        currentApplications = data.applications || [];
        renderApplicationsTable(currentApplications);
    } catch (err) {
        console.error("Error loading applications:", err);
    }
}

function renderApplicationsTable(apps) {
    const tbody = document.getElementById("applications-tbody");
    tbody.innerHTML = "";

    if (apps.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 32px;">No applied jobs found in database.</td></tr>`;
        return;
    }

    apps.forEach(app => {
        const tr = document.createElement("tr");

        const platClass = (app.platform || "").toLowerCase().includes("naukri") ? "badge-naukri" : "badge-linkedin";
        
        let scoreClass = "score-low";
        if (app.match_score >= 80) scoreClass = "score-high";
        else if (app.match_score >= 60) scoreClass = "score-med";

        const statusBadge = (app.status === "APPLIED" || app.status === "SUBMITTED") 
            ? `<span class="badge badge-success">${app.status}</span>` 
            : `<span class="badge badge-warning">${app.status}</span>`;

        const dateStr = app.applied_at_display || (app.applied_at ? app.applied_at.substring(0, 16).replace("T", " ") : "-");

        tr.innerHTML = `
            <td style="font-weight: 600;">${escapeHtml(app.job_title)}</td>
            <td style="color: #cbd5e1;">${escapeHtml(app.company)}</td>
            <td style="color: var(--text-muted);">${escapeHtml(app.location)}</td>
            <td><span class="badge ${platClass}">${escapeHtml(app.platform)}</span></td>
            <td><span class="score-badge ${scoreClass}">${Math.round(app.match_score)}%</span></td>
            <td>${statusBadge}</td>
            <td style="color: var(--text-muted); font-size: 12px;">${dateStr}</td>
            <td>
                <a href="${escapeHtml(app.job_url)}" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration: none;">View Job ↗</a>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function filterApplicationsTable() {
    const query = document.getElementById("search-jobs").value.toLowerCase();
    const plat = document.getElementById("filter-platform").value.toLowerCase();

    const filtered = currentApplications.filter(app => {
        const matchesQuery = (app.job_title + " " + app.company + " " + app.location).toLowerCase().includes(query);
        const matchesPlat = (plat === "all") || (app.platform && app.platform.toLowerCase().includes(plat));
        return matchesQuery && matchesPlat;
    });
    renderApplicationsTable(filtered);
}

// 5. ATS Resume Review
async function loadResumeReviewData() {
    try {
        const res = await fetch("/api/resume/review");
        if (!res.ok) return;
        const data = await res.json();

        document.getElementById("ats-score-display").textContent = data.score || "85";
        document.getElementById("ats-summary-text").textContent = data.summary || "";

        // Strengths
        const strengthsList = document.getElementById("ats-strengths-list");
        strengthsList.innerHTML = "";
        (data.strengths || []).forEach(s => {
            const li = document.createElement("li");
            li.style = "margin-bottom: 6px; color: #a7f3d0;";
            li.textContent = "✓ " + s;
            strengthsList.appendChild(li);
        });

        // Recommendations
        const recList = document.getElementById("ats-recommendations-list");
        recList.innerHTML = "";
        (data.improvements || []).forEach(r => {
            const li = document.createElement("li");
            li.style = "margin-bottom: 6px; color: #fde68a;";
            li.textContent = "⚡ " + r;
            recList.appendChild(li);
        });

        // Missing Keywords
        const missingContainer = document.getElementById("ats-missing-keywords");
        missingContainer.innerHTML = "";
        (data.missing_keywords || []).forEach(k => {
            const badge = document.createElement("span");
            badge.className = "badge";
            badge.style = "background: rgba(244,63,94,0.15); color: #fb7185; border: 1px solid rgba(244,63,94,0.3);";
            badge.textContent = "+ " + k;
            missingContainer.appendChild(badge);
        });
    } catch (err) {
        console.error("Error loading resume review:", err);
    }
}

// 5b. Resume Info & Upload Handler
async function loadResumeInfo() {
    try {
        const res = await fetch("/api/resume/info");
        if (!res.ok) return;
        const meta = await res.json();
        
        const filename = meta.filename || "resume.pdf";
        const sizeText = meta.size_kb ? `${meta.size_kb} KB` : "0 KB";
        const dateText = meta.last_modified ? `Last updated: ${meta.last_modified}` : "No file found";

        ["resume-active-name", "profile-resume-active-name"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = filename;
        });

        ["resume-active-size", "profile-resume-active-size"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = sizeText;
        });

        ["resume-active-date", "profile-resume-active-date"].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.textContent = dateText;
        });

        const textEl = document.getElementById("resume-extracted-text");
        if (textEl) {
            textEl.textContent = meta.full_text ? meta.full_text : (meta.text_snippet || "No text extracted yet.");
        }
    } catch (err) {
        console.error("Error loading resume info:", err);
    }
}

async function uploadResumeFile(file) {
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['pdf', 'docx'].includes(ext)) {
        showToast("Please select a PDF or DOCX file.", "error");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    const dropzones = [
        document.getElementById("resume-dropzone"),
        document.getElementById("profile-resume-dropzone")
    ].filter(Boolean);

    dropzones.forEach(dz => {
        dz.style.opacity = "0.5";
        dz.style.pointerEvents = "none";
    });

    showToast(`Uploading and analyzing '${file.name}'...`);

    try {
        const res = await fetch("/api/resume/upload", {
            method: "POST",
            body: formData
        });
        const data = await res.json();

        if (res.ok) {
            showToast(data.message || "Resume uploaded and analyzed!");
            await loadResumeInfo();
            await loadResumeReviewData();
            await loadDashboardStatus();
            await loadProfileData();
        } else {
            showToast(data.detail || "Failed to upload resume.", "error");
        }
    } catch (err) {
        showToast("Upload failed: " + err, "error");
    } finally {
        dropzones.forEach(dz => {
            dz.style.opacity = "1";
            dz.style.pointerEvents = "all";
        });
        ["resume-file-input", "profile-resume-file-input"].forEach(id => {
            const fi = document.getElementById(id);
            if (fi) fi.value = "";
        });
    }
}

function setupDropzoneEvents(dropzoneId, inputId, browseBtnId) {
    const dropzone = document.getElementById(dropzoneId);
    const fileInput = document.getElementById(inputId);
    const browseBtn = document.getElementById(browseBtnId);

    if (browseBtn && fileInput) {
        browseBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            fileInput.click();
        });
    }

    if (dropzone && fileInput) {
        dropzone.addEventListener("click", () => {
            fileInput.click();
        });

        dropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            dropzone.classList.add("dragover");
        });

        dropzone.addEventListener("dragleave", () => {
            dropzone.classList.remove("dragover");
        });

        dropzone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropzone.classList.remove("dragover");
            if (e.dataTransfer && e.dataTransfer.files.length > 0) {
                uploadResumeFile(e.dataTransfer.files[0]);
            }
        });

        fileInput.addEventListener("change", (e) => {
            if (e.target.files && e.target.files.length > 0) {
                uploadResumeFile(e.target.files[0]);
            }
        });
    }
}

function initResumeUpload() {
    setupDropzoneEvents("resume-dropzone", "resume-file-input", "btn-browse-resume");
    setupDropzoneEvents("profile-resume-dropzone", "profile-resume-file-input", "btn-profile-browse-resume");

    const reanalyzeHandler = async () => {
        showToast("Re-analyzing ATS readiness...");
        await loadResumeReviewData();
        await loadResumeInfo();
        showToast("Resume re-analysis complete!");
    };

    const reanalyzeBtn = document.getElementById("btn-reanalyze-resume");
    if (reanalyzeBtn) {
        reanalyzeBtn.addEventListener("click", reanalyzeHandler);
    }

    const profileReanalyzeBtn = document.getElementById("btn-profile-reanalyze-resume");
    if (profileReanalyzeBtn) {
        profileReanalyzeBtn.addEventListener("click", reanalyzeHandler);
    }
}

// 6. Direct Job Automation Runner & Controls

function setAgentRunningUI(isRunning) {
    const launchBtn = document.getElementById("btn-launch-agent");
    const stopBtns = [
        document.getElementById("btn-stop-agent"),
        document.getElementById("btn-header-stop-agent"),
        document.getElementById("btn-stop-agent-logs")
    ].filter(Boolean);
    const runningPill = document.getElementById("header-running-pill");

    if (isRunning) {
        if (launchBtn) {
            launchBtn.disabled = true;
            launchBtn.innerHTML = `<span class="status-dot" style="background: #fbbf24;"></span> Running Agent...`;
        }
        stopBtns.forEach(btn => {
            btn.style.display = "inline-flex";
            btn.disabled = false;
            btn.innerHTML = `🛑 Stop Agent`;
        });
        if (runningPill) runningPill.style.display = "inline-flex";
    } else {
        if (launchBtn) {
            launchBtn.disabled = false;
            launchBtn.innerHTML = `🚀 Launch Job Agent`;
        }
        stopBtns.forEach(btn => {
            btn.style.display = "none";
        });
        if (runningPill) runningPill.style.display = "none";
    }
}

async function launchAgentAutomation() {
    const role = document.getElementById("run-role").value.trim();
    const loc = document.getElementById("run-location").value.trim();
    const plat = document.getElementById("run-platform").value;
    const freshness = document.getElementById("run-freshness").value;
    const maxJobs = parseInt(document.getElementById("run-max-jobs").value) || 5;
    const minScore = parseFloat(document.getElementById("run-min-score")?.value) || 60.0;
    const remoteOnly = document.getElementById("run-remote-only").checked;
    const dryRun = document.getElementById("run-dry-run").checked;
    const autoApprove = document.getElementById("run-auto-approve").checked;

    const payload = {
        keyword: role,
        location: loc,
        platform: plat,
        date_posted: freshness,
        remote_only: remoteOnly,
        max_jobs: maxJobs,
        min_score: minScore,
        auto_approve: autoApprove,
        dry_run: dryRun
    };

    setAgentRunningUI(true);

    try {
        const res = await fetch("/api/run-agent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast("Automation started! Streaming live logs below.");
            startAgentStatusPolling();
        } else {
            showToast(data.detail || "Error starting agent.", "error");
            setAgentRunningUI(false);
        }
    } catch (err) {
        showToast("Failed to launch agent: " + err, "error");
        setAgentRunningUI(false);
    }
}

async function stopAgentAutomation() {
    const stopBtns = [
        document.getElementById("btn-stop-agent"),
        document.getElementById("btn-header-stop-agent"),
        document.getElementById("btn-stop-agent-logs")
    ].filter(Boolean);

    stopBtns.forEach(btn => {
        btn.disabled = true;
        btn.innerHTML = `<span class="status-dot" style="background: #fbbf24;"></span> Stopping...`;
    });
    showToast("Stopping JobAgent automation...", "warning");

    try {
        const res = await fetch("/api/run-agent/stop", { method: "POST" });
        const data = await res.json();
        showToast(data.message || "Agent stopped.", "warning");
        setAgentRunningUI(false);
        renderHitlBanner(null);
        if (agentPollInterval) {
            clearInterval(agentPollInterval);
            agentPollInterval = null;
        }
        await checkAgentStatusOnce();
        loadApplicationsData();
        loadDashboardStatus();
    } catch (err) {
        showToast("Error stopping agent: " + err, "error");
        setAgentRunningUI(false);
    }
}

async function clearTerminalLogs() {
    try {
        await fetch("/api/run-agent/clear-logs", { method: "POST" });
        const terminal = document.getElementById("runner-terminal");
        if (terminal) {
            terminal.innerHTML = `<div class="console-line"><span class="console-time">[System]</span> <span class="console-info">Logs cleared. Ready for next run.</span></div>`;
        }
        showToast("Logs cleared.");
    } catch (err) {
        console.error("Failed to clear logs:", err);
    }
}

function renderTerminalLogs(logs) {
    const terminal = document.getElementById("runner-terminal");
    if (!terminal) return;
    terminal.innerHTML = "";
    if (!logs || logs.length === 0) {
        terminal.innerHTML = `<div class="console-line"><span class="console-time">[System]</span> <span class="console-info">Ready to launch job automation. Logs will stream here.</span></div>`;
        return;
    }
    logs.forEach(log => {
        const line = document.createElement("div");
        line.className = "console-line";
        const lvlClass = log.level === "ERROR" ? "console-error" : (log.level === "WARNING" ? "console-warn" : "console-info");
        line.innerHTML = `<span class="console-time">[${log.time}]</span> <span class="${lvlClass}">[${log.level}]</span> <span>${escapeHtml(log.message)}</span>`;
        terminal.appendChild(line);
    });
    terminal.scrollTop = terminal.scrollHeight;
}

async function checkAgentStatusOnce() {
    try {
        const res = await fetch("/api/run-agent/status");
        if (!res.ok) return null;
        const data = await res.json();
        renderTerminalLogs(data.logs || []);
        setAgentRunningUI(Boolean(data.is_running));
        renderHitlBanner(data.hitl_active ? data.hitl_request : null);
        renderLiveDecisionCard(data.latest_decision);
        return data;
    } catch (err) {
        console.error("Error checking status:", err);
        return null;
    }
}

function startAgentStatusPolling() {
    if (agentPollInterval) clearInterval(agentPollInterval);

    agentPollInterval = setInterval(async () => {
        try {
            const res = await fetch("/api/run-agent/status");
            if (!res.ok) return;
            const data = await res.json();

            renderTerminalLogs(data.logs || []);
            renderHitlBanner(data.hitl_active ? data.hitl_request : null);
            renderLiveDecisionCard(data.latest_decision);

            if (data.is_running) {
                setAgentRunningUI(true);
            } else if (data.completed_at) {
                clearInterval(agentPollInterval);
                agentPollInterval = null;
                setAgentRunningUI(false);
                renderHitlBanner(null);
                showToast("Agent run finished!");
                loadApplicationsData();
                loadDashboardStatus();
            }
        } catch (err) {
            console.error("Polling error:", err);
        }
    }, 1200);
}

// 6b. Autonomous Decision Engine — Live Decision Card Renderer
function renderLiveDecisionCard(decision) {
    const container = document.getElementById("live-decision-container");
    if (!container) return;

    if (!decision || !decision.job_title) {
        container.style.display = "none";
        return;
    }

    container.style.display = "block";

    // Decision badge
    const badge = document.getElementById("live-decision-badge");
    const decVal = (decision.decision || "APPLY").toUpperCase();
    badge.textContent = decVal;
    if (decVal === "APPLY") {
        badge.style.background = "rgba(16, 185, 129, 0.2)";
        badge.style.color = "#34d399";
        badge.style.border = "1px solid #10b981";
        container.style.borderColor = "rgba(16, 185, 129, 0.4)";
    } else if (decVal === "REVIEW") {
        badge.style.background = "rgba(245, 158, 11, 0.2)";
        badge.style.color = "#fbbf24";
        badge.style.border = "1px solid #f59e0b";
        container.style.borderColor = "rgba(245, 158, 11, 0.4)";
    } else {
        badge.style.background = "rgba(239, 68, 68, 0.2)";
        badge.style.color = "#f87171";
        badge.style.border = "1px solid #ef4444";
        container.style.borderColor = "rgba(239, 68, 68, 0.4)";
    }

    // Priority badge
    const prioBadge = document.getElementById("live-priority-badge");
    const score = Math.round(decision.match_score || 0);
    let prioTier = "Priority A";
    if (score >= 95) prioTier = "Priority A (>=95%)";
    else if (score >= 85) prioTier = "Priority B (85-94%)";
    else if (score >= 70) prioTier = "Priority C (70-84%)";
    else prioTier = "Low Fit (<70%)";
    prioBadge.textContent = prioTier;

    // Header info
    document.getElementById("live-decision-title").textContent = decision.job_title || "Job Title";
    document.getElementById("live-decision-meta").textContent = `${decision.company || "Company"} • ${decision.location || "Location"}`;
    document.getElementById("live-decision-score").textContent = `${score}%`;

    // Factor breakdown
    document.getElementById("score-bar-tech").textContent = `${Math.round(decision.technical_fit || 0)}%`;
    document.getElementById("score-bar-exp").textContent = `${Math.round(decision.experience_fit || 0)}%`;
    document.getElementById("score-bar-role").textContent = `${Math.round(decision.role_fit || 0)}%`;
    document.getElementById("score-bar-prefs").textContent = `${Math.round(decision.preference_fit || 100)}%`;
    document.getElementById("score-bar-risk").textContent = `${Math.round(decision.risk || 0)}%`;

    // Matched skills
    const matchedRow = document.getElementById("live-decision-matched-row");
    const matchedEl = document.getElementById("live-decision-matched-skills");
    if (decision.matched_skills && decision.matched_skills.length > 0) {
        matchedRow.style.display = "block";
        matchedEl.innerHTML = decision.matched_skills.map(s => `<span class="badge" style="background: rgba(16,185,129,0.15); color: #34d399; margin: 2px;">${escapeHtml(s)}</span>`).join("");
    } else {
        matchedRow.style.display = "none";
    }

    // Missing skills
    const missingRow = document.getElementById("live-decision-missing-row");
    const missingEl = document.getElementById("live-decision-missing-skills");
    if (decision.missing_skills && decision.missing_skills.length > 0) {
        missingRow.style.display = "block";
        missingEl.innerHTML = decision.missing_skills.map(s => `<span class="badge" style="background: rgba(245,158,11,0.15); color: #fbbf24; margin: 2px;">${escapeHtml(s)}</span>`).join("");
    } else {
        missingRow.style.display = "none";
    }

    // Incompatible skills
    const incompRow = document.getElementById("live-decision-incompatible-row");
    const incompEl = document.getElementById("live-decision-incompatible-skills");
    if (decision.incompatible_skills && decision.incompatible_skills.length > 0) {
        incompRow.style.display = "block";
        incompEl.innerHTML = decision.incompatible_skills.map(s => `<span class="badge" style="background: rgba(239,68,68,0.15); color: #f87171; margin: 2px;">${escapeHtml(s)}</span>`).join("");
    } else {
        incompRow.style.display = "none";
    }

    // Reasoning
    document.getElementById("live-decision-reasoning").textContent = decision.reason || "Autonomous multi-factor evaluation completed.";
}

// 7. Human-In-The-Loop (HITL) Interactive UI Handlers
let lastHitlKey = "";

function renderHitlBanner(req) {
    const banner = document.getElementById("hitl-alert-container");
    if (!banner) return;

    if (!req) {
        banner.style.display = "none";
        lastHitlKey = "";
        return;
    }

    banner.style.display = "block";

    const titleEl = document.getElementById("hitl-title");
    if (titleEl) titleEl.textContent = req.title || "Action Required: Human Input Needed";

    const msgEl = document.getElementById("hitl-message");
    if (msgEl) msgEl.textContent = req.message || "";

    const timerEl = document.getElementById("hitl-timer");
    if (timerEl) timerEl.textContent = req.remaining_sec !== undefined ? req.remaining_sec : 120;

    const currentKey = `${req.type}_${req.title}_${(req.options || []).join(",")}`;
    if (currentKey === lastHitlKey) {
        return; // Already rendered interactive controls for this request
    }
    lastHitlKey = currentKey;

    const bodyEl = document.getElementById("hitl-interactive-body");
    if (!bodyEl) return;
    bodyEl.innerHTML = "";

    if (req.type === "job_review") {
        const wrap = document.createElement("div");
        wrap.style = "display: flex; gap: 10px; align-items: center; margin-bottom: 12px;";
        wrap.innerHTML = `
            <button class="btn btn-success" id="btn-hitl-review-apply" style="background: #10b981; border-color: #059669; font-weight: 700; padding: 10px 20px; color: white;">
                ✓ Proceed to Apply
            </button>
            <button class="btn btn-secondary" id="btn-hitl-review-skip" style="border-color: #ef4444; color: #f87171; font-weight: 600; padding: 10px 20px;">
                ✕ Skip Job
            </button>
        `;
        bodyEl.appendChild(wrap);

        const btnApp = wrap.querySelector("#btn-hitl-review-apply");
        if (btnApp) btnApp.addEventListener("click", () => sendHitlResponse("resolved", "apply"));
        const btnSkp = wrap.querySelector("#btn-hitl-review-skip");
        if (btnSkp) btnSkp.addEventListener("click", () => sendHitlResponse("resolved", "skip"));
    } else if (req.type === "submit_approval") {
        const wrap = document.createElement("div");
        wrap.style = "display: flex; gap: 10px; align-items: center; margin-bottom: 12px;";
        wrap.innerHTML = `
            <button class="btn btn-success" id="btn-hitl-approve-submit" style="background: #10b981; border-color: #059669; font-weight: 700; padding: 10px 20px; color: white;">
                ✓ Approve & Submit Application
            </button>
        `;
        bodyEl.appendChild(wrap);

        const btnApprove = wrap.querySelector("#btn-hitl-approve-submit");
        if (btnApprove) {
            btnApprove.addEventListener("click", () => sendHitlResponse("submit"));
        }
    } else if (req.type === "field_input") {
        const wrap = document.createElement("div");
        wrap.style = "display: flex; flex-direction: column; gap: 10px;";

        if (req.options && req.options.length > 0) {
            const optHeader = document.createElement("div");
            optHeader.style = "font-weight: 600; font-size: 13px; color: #94a3b8;";
            optHeader.textContent = "Select an option:";
            wrap.appendChild(optHeader);

            const optGrid = document.createElement("div");
            optGrid.style = "display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 4px;";
            req.options.forEach(opt => {
                const b = document.createElement("button");
                b.className = "btn btn-secondary btn-sm";
                b.style = "font-size: 13px; padding: 6px 12px; border-color: #475569;";
                b.textContent = opt;
                b.addEventListener("click", () => sendHitlResponse("answer", opt));
                optGrid.appendChild(b);
            });
            wrap.appendChild(optGrid);
        }

        const inputGroup = document.createElement("div");
        inputGroup.style = "display: flex; gap: 8px; align-items: center;";
        inputGroup.innerHTML = `
            <input type="text" id="hitl-input-val" class="form-control" placeholder="Or enter custom answer..." value="${escapeHtml(req.suggested_value || '')}" style="flex: 1;">
            <button class="btn btn-primary btn-sm" id="btn-hitl-submit-answer" style="padding: 8px 16px;">Submit Answer</button>
        `;
        wrap.appendChild(inputGroup);
        bodyEl.appendChild(wrap);

        const submitBtn = wrap.querySelector("#btn-hitl-submit-answer");
        const valInput = wrap.querySelector("#hitl-input-val");
        if (submitBtn && valInput) {
            submitBtn.addEventListener("click", () => {
                const v = valInput.value.trim();
                if (v) sendHitlResponse("answer", v);
            });
            valInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter") {
                    const v = valInput.value.trim();
                    if (v) sendHitlResponse("answer", v);
                }
            });
        }
    } else {
        const info = document.createElement("div");
        info.style = "font-size: 13px; color: #cbd5e1; margin-bottom: 8px;";
        info.innerHTML = `Please select or enter the required value directly in the Chromium browser window, then click <strong>"I Resolved It in Browser"</strong> below.`;
        bodyEl.appendChild(info);
    }
}

async function sendHitlResponse(action, value = null) {
    try {
        const res = await fetch("/api/run-agent/hitl-respond", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: action, value: value })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Response submitted: ${action}`);
            const banner = document.getElementById("hitl-alert-container");
            if (banner) banner.style.display = "none";
            lastHitlKey = "";
        }
    } catch (err) {
        console.error("Error sending HITL response:", err);
    }
}

function initHitlControls() {
    const btnResolved = document.getElementById("btn-hitl-browser-resolved");
    if (btnResolved) {
        btnResolved.addEventListener("click", () => sendHitlResponse("resolved"));
    }
    const btnSkip = document.getElementById("btn-hitl-skip-job");
    if (btnSkip) {
        btnSkip.addEventListener("click", () => sendHitlResponse("skip"));
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

