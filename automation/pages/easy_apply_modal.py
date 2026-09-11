import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from playwright.async_api import Page, Locator

from automation.pages.base_page import BasePage
from models.form import FormField, FormFieldType, FormStep

logger = logging.getLogger(__name__)

class EasyApplyModal(BasePage):
    """
    Page Object Model for the multi-step LinkedIn Easy Apply modal dialog.
    """

    MODAL_SELECTOR = "div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal'], div.artdeco-modal[role='dialog']"

    def __init__(self, page: Page):
        super().__init__(page)

    def get_modal(self) -> Locator:
        return self.page.locator(self.MODAL_SELECTOR).first

    async def is_modal_open(self, timeout_ms: int = 6000) -> bool:
        try:
            modal = self.get_modal()
            return await modal.is_visible(timeout=timeout_ms)
        except Exception:
            return False

    async def get_step_title(self) -> str:
        """Extracts current step header title (e.g., 'Contact info', 'Resume', 'Additional Questions')."""
        selectors = [
            f"{self.MODAL_SELECTOR} h3",
            f"{self.MODAL_SELECTOR} h2",
            f"{self.MODAL_SELECTOR} div.jobs-easy-apply-modal__content h3"
        ]
        for sel in selectors:
            loc = self.page.locator(sel).first
            if await loc.count() > 0:
                text = (await loc.inner_text()).strip()
                if text:
                    return text
        return "Application Step"

    async def scroll_modal_content(self, step: int = 450) -> bool:
        """
        Scrolls the modal content container down by `step` pixels.
        Returns True if scrolled, False if already at bottom or container not found.
        """
        modal = self.get_modal()
        content_selectors = [
            "div.jobs-easy-apply-modal__content",
            "div.artdeco-modal__content",
            "div[class*='easy-apply-modal__content']",
            "div.jobs-easy-apply-content",
            "form"
        ]
        for sel in content_selectors:
            loc = modal.locator(sel).first
            if await loc.count() > 0:
                try:
                    scrolled = await loc.evaluate("""(node, step) => {
                        const prev = node.scrollTop;
                        node.scrollTop += step;
                        return node.scrollTop > prev;
                    }""", step)
                    if scrolled:
                        await self.human_delay(250, 450)
                        return True
                except Exception:
                    pass
        return False

    async def scroll_modal_to_top(self) -> None:
        """Scrolls the modal content container back to the top."""
        modal = self.get_modal()
        for sel in ["div.jobs-easy-apply-modal__content", "div.artdeco-modal__content"]:
            loc = modal.locator(sel).first
            if await loc.count() > 0:
                try:
                    await loc.evaluate("node => { node.scrollTop = 0; }")
                    await self.human_delay(200, 350)
                    break
                except Exception:
                    pass

    async def scroll_modal_to_bottom(self) -> None:
        """Scrolls the modal content container to the bottom."""
        modal = self.get_modal()
        for sel in ["div.jobs-easy-apply-modal__content", "div.artdeco-modal__content"]:
            loc = modal.locator(sel).first
            if await loc.count() > 0:
                try:
                    await loc.evaluate("node => { node.scrollTop = node.scrollHeight; }")
                    await self.human_delay(200, 350)
                    break
                except Exception:
                    pass

    async def is_review_step(self) -> bool:
        """Determines if current step is the final review step ready for submission."""
        modal = self.get_modal()
        try:
            if await self.has_validation_errors():
                return False

            submit_btn = modal.locator(
                "button[aria-label='Submit application']:visible, "
                "button:visible:has-text('Submit application')"
            ).first
            if await submit_btn.is_visible(timeout=1000):
                return True

            title = await self.get_step_title()
            if "review" in title.lower():
                review_btn = modal.locator("button:visible:has-text('Submit'), button[aria-label='Submit application']").first
                return await review_btn.is_visible(timeout=1000)
        except Exception:
            pass
        return False

    async def inspect_fields(self) -> List[FormField]:
        """
        Inspects all form controls currently rendered in the active modal step.
        """
        modal = self.get_modal()
        fields: List[FormField] = []

        # 1. Text, Number, and Tel inputs
        input_locators = modal.locator(
            "input[type='text'], input[type='tel'], input[type='number'], input:not([type]), textarea"
        )
        count = await input_locators.count()
        for i in range(count):
            loc = input_locators.nth(i)
            # Skip hidden inputs
            if not await loc.is_visible():
                continue

            field_id = await loc.get_attribute("id") or f"input_{i}"
            label = await self._find_label_for_element(loc)
            help_text = await self._find_help_text_for_element(loc)
            val_err = await self._find_error_for_element(loc)
            combined_label = f"{label}\n{help_text}" if help_text and help_text.lower() not in label.lower() else label
            input_type = await loc.get_attribute("type") or "text"
            input_mode = await loc.get_attribute("inputmode") or ""
            current_val = await loc.input_value()

            ftype = FormFieldType.NUMBER if (input_type == "number" or input_mode == "numeric") else FormFieldType.TEXT

            fields.append(FormField(
                field_id=field_id,
                label=combined_label,
                field_type=ftype,
                current_value=current_val,
                selector=f"#{field_id}" if field_id.startswith("input") is False else None,
                is_required=await loc.get_attribute("required") is not None,
                help_text=help_text,
                validation_error=val_err
            ))

        # 2. Select / Dropdowns
        select_locators = modal.locator("select")
        sel_count = await select_locators.count()
        for i in range(sel_count):
            loc = select_locators.nth(i)
            if not await loc.is_visible():
                continue

            field_id = await loc.get_attribute("id") or f"select_{i}"
            label = await self._find_label_for_element(loc)
            help_text = await self._find_help_text_for_element(loc)
            val_err = await self._find_error_for_element(loc)
            combined_label = f"{label}\n{help_text}" if help_text and help_text.lower() not in label.lower() else label
            
            # Extract currently selected text/value if already chosen
            current_val = ""
            try:
                current_val = await loc.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")
            except Exception:
                pass

            # Fast batch extraction via single evaluate call instead of IPC roundtrips
            try:
                options = await loc.evaluate("el => Array.from(el.options).map(o => o.text.trim()).filter(t => t.length > 0)")
            except Exception:
                options = []
            clean_opts = [o for o in options if not o.lower().startswith("select an option")]

            fields.append(FormField(
                field_id=field_id,
                label=combined_label,
                field_type=FormFieldType.SELECT,
                options=clean_opts,
                current_value=current_val,
                selector=f"#{field_id}" if field_id.startswith("select") is False else None,
                is_required=await loc.get_attribute("required") is not None,
                help_text=help_text,
                validation_error=val_err
            ))

        # 3. Radio Groups (fieldsets)
        fieldset_locators = modal.locator("fieldset")
        fs_count = await fieldset_locators.count()
        for i in range(fs_count):
            loc = fieldset_locators.nth(i)
            if not await loc.is_visible():
                continue

            legend_elem = loc.locator("legend").first
            if await legend_elem.count() > 0:
                raw_label = await legend_elem.inner_text()
                # Clean label: strip 'Required', '*', extra newlines and spaces
                clean_label = re.sub(r'\s+', ' ', raw_label.replace("Required", "").replace("*", "")).strip()
            else:
                clean_label = f"Radio Group {i}"

            help_text = await self._find_help_text_for_element(loc)
            val_err = await self._find_error_for_element(loc)
            combined_label = f"{clean_label}\n{help_text}" if help_text and help_text.lower() not in clean_label.lower() else clean_label

            radio_inputs = loc.locator("input[type='radio']")
            r_count = await radio_inputs.count()
            if r_count == 0:
                continue

            radio_name = ""
            try:
                radio_name = (await radio_inputs.first.get_attribute("name") or "").strip()
            except Exception:
                pass

            current_val = ""
            try:
                checked = loc.locator("input[type='radio']:checked")
                if await checked.count() > 0:
                    current_val = (await checked.first.get_attribute("value") or "").strip()
            except Exception:
                pass

            try:
                options = await loc.evaluate(
                    "el => Array.from(el.querySelectorAll('label')).map(l => l.innerText.trim()).filter(t => t.length > 0)"
                )
            except Exception:
                options = ["Yes", "No"]

            field_id = radio_name or await loc.get_attribute("id") or f"fieldset_{i}"
            fields.append(FormField(
                field_id=field_id,
                label=combined_label,
                field_type=FormFieldType.RADIO,
                options=options,
                current_value=current_val,
                selector=f"input[name='{radio_name}']" if radio_name else None,
                is_required=True,
                help_text=help_text,
                validation_error=val_err
            ))

        # 4. Resume handling (prioritizes using existing resume on LinkedIn)
        existing_resumes = modal.locator(
            "div.jobs-document-upload__title, "
            "div.jobs-resume-picker__resume-item, "
            "span:has-text('.pdf'), span:has-text('.docx')"
        )
        has_existing = False
        try:
            has_existing = await existing_resumes.count() > 0 and await existing_resumes.first.is_visible()
        except Exception:
            has_existing = False

        if has_existing:
            # Ensure the existing resume radio button is checked
            resume_radio = modal.locator("input[type='radio']:visible").first
            try:
                if await resume_radio.is_visible():
                    await resume_radio.check()
            except Exception:
                pass
        else:
            file_inputs = modal.locator("input[type='file']")
            if await file_inputs.count() > 0:
                file_loc = file_inputs.first
                field_id = await file_loc.get_attribute("id") or "resume_upload"
                fields.append(FormField(
                    field_id=field_id,
                    label="Resume Upload",
                    field_type=FormFieldType.FILE_UPLOAD,
                    is_required=True
                ))

        return fields

    async def get_validation_errors(self) -> List[str]:
        """Returns list of visible validation error message texts on the modal."""
        modal = self.get_modal()
        error_locators = modal.locator(
            "div.artdeco-inline-feedback--error:visible, "
            "p.artdeco-inline-feedback__message:visible, "
            ".artdeco-inline-feedback--error:visible, "
            "span.artdeco-inline-feedback__message:visible, "
            "span:visible:has-text('Please make a selection'), "
            "span:visible:has-text('Enter a whole number'), "
            "span:visible:has-text('Please enter a valid answer')"
        )
        errors = []
        try:
            count = await error_locators.count()
            for i in range(count):
                txt = (await error_locators.nth(i).inner_text()).strip()
                if txt and txt not in errors:
                    errors.append(txt)
        except Exception:
            pass
        return errors

    async def has_validation_errors(self) -> bool:
        """Checks if red validation error messages are currently visible on the modal."""
        errors = await self.get_validation_errors()
        return len(errors) > 0

    async def fill_field(
        self,
        field: FormField,
        value: str,
        resume_file_path: Optional[str] = None
    ) -> bool:
        """
        Interacts with the modal element to set its value.
        """
        modal = self.get_modal()
        clean_label = field.label.replace("*", "").strip()

        try:
            if field.field_type in [FormFieldType.TEXT, FormFieldType.NUMBER]:
                loc = None
                if field.field_id and not field.field_id.startswith("input_"):
                    loc = modal.locator(f"[id='{field.field_id}']").first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.get_by_label(clean_label, exact=False).first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.locator(f"div:has-text('{clean_label}') input").first

                if loc and await loc.count() > 0 and await loc.is_visible():
                    # Safeguard numeric / experience values against text clutter (e.g. 'Answer: 3.9 years' -> '3.9')
                    fill_val = str(value).strip()
                    input_type = await loc.get_attribute("type") or "text"
                    input_mode = await loc.get_attribute("inputmode") or ""
                    is_num_input = (
                        field.field_type == FormFieldType.NUMBER
                        or input_type == "number"
                        or input_mode == "numeric"
                        or any(k in clean_label.lower() for k in [
                            "year", "experience", "how many", "ctc", "salary", "notice",
                            "days", "months", "lakh", "lpa", "total it", "decimal"
                        ])
                    )
                    if is_num_input and fill_val:
                        num_m = re.search(r'(\d+(?:\.\d+)?)', fill_val)
                        if num_m:
                            fill_val = num_m.group(1)
                        else:
                            fill_val = "0"

                        # Safeguard against LPA decimals in whole-number INR compensation fields
                        # e.g. "Enter a whole number larger than 100", "Example: 200000", "in INR"
                        comb_text = f"{clean_label} {getattr(field, 'help_text', '') or ''} {getattr(field, 'validation_error', '') or ''}".lower()
                        is_inr_comp = any(w in comb_text for w in ["in inr", "inr", "annual", "larger than 100", "200000", "350000", "rupees", "rupee"])
                        if is_inr_comp and any(w in comb_text for w in ["ctc", "salary", "compensation"]):
                            try:
                                val_f = float(fill_val)
                                if 0 < val_f <= 100:
                                    fill_val = str(int(round(val_f * 100000)))
                            except ValueError:
                                pass

                    await loc.scroll_into_view_if_needed()
                    await loc.click()
                    await loc.fill("")
                    await loc.fill(fill_val)
                    await self.human_delay(300, 600)

                    # Automatically select from typeahead/autocomplete dropdown if one appears (e.g. City/Location)
                    typeahead_item = modal.locator(
                        "div.basic-typeahead__selectable-item:visible, "
                        "div.artdeco-typeahead__result:visible, "
                        "li[role='option']:visible, "
                        "div[role='option']:visible, "
                        ".basic-typeahead__selectable-list li:visible"
                    ).first
                    try:
                        if await typeahead_item.is_visible(timeout=1200):
                            await typeahead_item.click()
                            await self.human_delay(200, 400)
                        elif "city" in clean_label.lower() or "location" in clean_label.lower():
                            await loc.press("ArrowDown")
                            await asyncio.sleep(0.2)
                            await loc.press("Enter")
                            await self.human_delay(200, 400)
                    except Exception:
                        pass

                    return True

            elif field.field_type == FormFieldType.SELECT:
                loc = None
                if field.field_id and not field.field_id.startswith("select_"):
                    candidate = modal.locator(f"[id='{field.field_id}']").first
                    if await candidate.count() > 0 and await candidate.is_visible():
                        loc = candidate
                if not loc:
                    short_label = clean_label[:40]
                    candidate = modal.get_by_label(short_label, exact=False).first
                    if await candidate.count() > 0 and await candidate.is_visible():
                        loc = candidate
                if not loc:
                    clean_short = "".join(c for c in clean_label[:30] if c.isalnum() or c.isspace())
                    candidate = modal.locator(f"div:has-text('{clean_short}') select").first
                    if await candidate.count() > 0 and await candidate.is_visible():
                        loc = candidate
                if not loc and field.field_id and field.field_id.startswith("select_"):
                    try:
                        idx = int(field.field_id.split("_")[1])
                        candidate = modal.locator("select:visible").nth(idx)
                        if await candidate.count() > 0 and await candidate.is_visible():
                            loc = candidate
                    except Exception:
                        pass
                if not loc:
                    clean_kw = re.sub(r'[^a-zA-Z0-9 ]', '', clean_label).strip()
                    first_words = " ".join(clean_kw.split()[:4])
                    if first_words:
                        candidate = modal.locator(f"div.fb-dash-form-element:has-text('{first_words}') select, div:has-text('{first_words}') select").first
                        if await candidate.count() > 0:
                            loc = candidate
                if not loc:
                    candidate = modal.locator("select:visible").first
                    if await candidate.count() > 0:
                        loc = candidate

                if loc and await loc.count() > 0 and await loc.is_visible():
                    await loc.scroll_into_view_if_needed()

                    # 1. Native Playwright select_option by label or value first (triggers React authentic synthetic events)
                    try:
                        await loc.select_option(label=value, timeout=1200)
                        await self.human_delay(200, 400)
                        curr_txt = await loc.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")
                        if curr_txt and not curr_txt.lower().startswith("select"):
                            return True
                    except Exception:
                        pass

                    try:
                        await loc.select_option(value=value, timeout=800)
                        await self.human_delay(200, 400)
                        curr_val = await loc.evaluate("el => el.value")
                        if curr_val:
                            return True
                    except Exception:
                        pass

                    # 2. DOM JavaScript selection with React prototype descriptor & event dispatching
                    try:
                        selected_val = await loc.evaluate(
                            """(sel, targetVal) => {
                                const norm = s => (s || '').toLowerCase().replace(/[\\u2010-\\u2015\\u2212\\-_—]/g, ' ').replace(/\\s+/g, ' ').trim();
                                const tNorm = norm(targetVal);
                                if (!tNorm) return null;
                                let matched = null;
                                // Exact text or value match
                                for (const opt of sel.options) {
                                    const oNorm = norm(opt.text);
                                    const vNorm = norm(opt.value);
                                    if (oNorm === tNorm || vNorm === tNorm) {
                                        matched = opt;
                                        break;
                                    }
                                }
                                // Prefix or substring match (skipping placeholder like 'Select an option')
                                if (!matched) {
                                    for (const opt of sel.options) {
                                        const oNorm = norm(opt.text);
                                        if (!oNorm || oNorm.startsWith('select')) continue;
                                        if (oNorm.startsWith(tNorm) || oNorm.includes(tNorm) || tNorm.includes(oNorm)) {
                                            matched = opt;
                                            break;
                                        }
                                        if (/^\\d+$/.test(tNorm) && oNorm.startsWith(tNorm)) {
                                            matched = opt;
                                            break;
                                        }
                                    }
                                }
                                if (matched) {
                                    const desc = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value');
                                    if (desc && desc.set) {
                                        desc.set.call(sel, matched.value);
                                    } else {
                                        sel.value = matched.value;
                                    }
                                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                                    return matched.value;
                                }
                                return null;
                            }""",
                            value
                        )
                        if selected_val is not None:
                            try:
                                await loc.select_option(value=selected_val, timeout=800)
                            except Exception:
                                pass
                            await self.human_delay(200, 400)
                            curr = await loc.evaluate("el => el.value")
                            if curr:
                                return True
                    except Exception as e:
                        logger.debug(f"JS Select evaluation error: {e}")

            elif field.field_type == FormFieldType.RADIO:
                modal_fieldset = None
                # 1. Unique radio name selector (most reliable on LinkedIn)
                if field.selector and "name=" in field.selector:
                    try:
                        candidate = modal.locator(f"fieldset:has({field.selector}), div:has({field.selector})").first
                        if await candidate.count() > 0 and await candidate.is_visible():
                            modal_fieldset = candidate
                    except Exception:
                        pass

                # 2. Radio name stored as field_id
                if not modal_fieldset and field.field_id and not field.field_id.startswith("fieldset_"):
                    try:
                        candidate = modal.locator(f"fieldset:has(input[name='{field.field_id}']), div:has(input[name='{field.field_id}']), [id='{field.field_id}']").first
                        if await candidate.count() > 0 and await candidate.is_visible():
                            modal_fieldset = candidate
                    except Exception:
                        pass

                # 3. Fieldset index
                if not modal_fieldset and field.field_id and field.field_id.startswith("fieldset_"):
                    try:
                        idx = int(field.field_id.split("_")[1])
                        candidate = modal.locator("fieldset:visible").nth(idx)
                        if await candidate.count() > 0:
                            modal_fieldset = candidate
                    except Exception:
                        pass

                # 4. Keyword filter on question label
                if not modal_fieldset:
                    clean_kw = re.sub(r'[^a-zA-Z0-9 ]', '', clean_label).strip()
                    first_words = " ".join(clean_kw.split()[:4])
                    if first_words:
                        candidate = modal.locator("fieldset:visible").filter(has_text=first_words).first
                        if await candidate.count() > 0:
                            modal_fieldset = candidate

                # 5. Fallback: first visible fieldset
                if not modal_fieldset or await modal_fieldset.count() == 0:
                    modal_fieldset = modal.locator("fieldset:visible").first

                if modal_fieldset and await modal_fieldset.count() > 0:
                    await modal_fieldset.scroll_into_view_if_needed()

                    # 1. Native Playwright label / span click first (authentically triggers React synthetic events)
                    target_label = modal_fieldset.locator("label, span.fb-radio__label, span").filter(
                        has_text=re.compile(rf"^\s*{re.escape(value)}\s*$", re.IGNORECASE)
                    ).first
                    if await target_label.count() > 0:
                        try:
                            await target_label.scroll_into_view_if_needed()
                            await target_label.click(timeout=1500)
                            await self.human_delay(200, 400)
                            if await modal_fieldset.locator("input[type='radio']:checked").count() > 0:
                                return True
                        except Exception:
                            pass

                    # Direct radio input check via Playwright
                    target_input = modal_fieldset.locator(f"input[type='radio'][value='{value}']").first
                    if await target_input.count() > 0:
                        try:
                            await target_input.scroll_into_view_if_needed()
                            await target_input.check(force=True)
                            await self.human_delay(150, 300)
                            if await target_input.is_checked():
                                return True
                        except Exception:
                            pass

                    # 2. Direct DOM JS check with React prototype descriptor & event dispatch
                    try:
                        success = await modal_fieldset.evaluate(
                            """(fieldset, targetVal) => {
                                const norm = s => (s || '').toLowerCase().replace(/[\\u2010-\\u2015\\u2212\\-_—]/g, ' ').replace(/\\s+/g, ' ').trim();
                                const tNorm = norm(targetVal);
                                if (!tNorm) return false;

                                const checkRadio = (r, lbl) => {
                                    const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked');
                                    if (desc && desc.set) {
                                        desc.set.call(r, true);
                                    } else {
                                        r.checked = true;
                                    }
                                    r.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                    r.dispatchEvent(new Event('change', { bubbles: true }));
                                    r.dispatchEvent(new Event('input', { bubbles: true }));
                                    if (lbl) {
                                        lbl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                                        lbl.click();
                                    }
                                    return true;
                                };

                                const radios = Array.from(fieldset.querySelectorAll("input[type='radio']"));
                                // 1. Check radio value
                                for (const radio of radios) {
                                    const vNorm = norm(radio.value);
                                    if (vNorm === tNorm || (vNorm && (vNorm.startsWith(tNorm) || tNorm.startsWith(vNorm)))) {
                                        const lbl = fieldset.querySelector(`label[for="${radio.id}"]`) || radio.closest('label');
                                        return checkRadio(radio, lbl);
                                    }
                                }

                                // 2. Check label text for each radio
                                for (const radio of radios) {
                                    const lbl = fieldset.querySelector(`label[for="${radio.id}"]`) || radio.closest('label');
                                    if (lbl) {
                                        const lNorm = norm(lbl.innerText);
                                        if (lNorm === tNorm || lNorm.startsWith(tNorm) || lNorm.includes(tNorm) || tNorm.includes(lNorm)) {
                                            return checkRadio(radio, lbl);
                                        }
                                    }
                                }

                                // 3. Check all labels in fieldset
                                const labels = Array.from(fieldset.querySelectorAll('label'));
                                for (const lbl of labels) {
                                    const lNorm = norm(lbl.innerText);
                                    if (lNorm === tNorm || lNorm.startsWith(tNorm) || lNorm.includes(tNorm) || tNorm.includes(lNorm)) {
                                        const r = lbl.querySelector("input[type='radio']") || fieldset.querySelector(`input[type='radio']#${lbl.getAttribute('for')}`);
                                        if (r) return checkRadio(r, lbl);
                                        lbl.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""",
                            value
                        )
                        if success:
                            await self.human_delay(200, 400)
                            return True
                    except Exception as e:
                        logger.debug(f"JS radio selection error: {e}")

                    # Substring match fallback
                    val_word = value.split()[0] if value.split() else value
                    target_label_sub = modal_fieldset.locator(f"label:has-text('{val_word}'), span:has-text('{val_word}')").first
                    if await target_label_sub.count() > 0 and await target_label_sub.is_visible():
                        try:
                            await target_label_sub.click(force=True)
                            await self.human_delay(150, 300)
                            return True
                        except Exception:
                            pass

            elif field.field_type == FormFieldType.FILE_UPLOAD:
                if resume_file_path:
                    file_input = modal.locator("input[type='file']").first
                    if await file_input.count() > 0:
                        await file_input.set_input_files(resume_file_path)
                        await self.human_delay(800, 1500)
                        return True

        except Exception as e:
            logger.debug(f"Failed to fill field {field.label}: {e}")

        return False

    async def click_next(self) -> bool:
        """Clicks 'Next' or 'Continue to next step'."""
        modal = self.get_modal()
        selectors = [
            "button[aria-label='Continue to next step']:visible",
            "button:visible:has-text('Next')",
            "footer button.artdeco-button--primary:visible"
        ]
        for sel in selectors:
            btn = modal.locator(sel).first
            if await btn.is_visible(timeout=800):
                btn_text = (await btn.inner_text()).strip().lower()
                if "review" in btn_text or "submit" in btn_text:
                    continue
                try:
                    await btn.click(timeout=1500)
                except Exception:
                    await btn.click(force=True)
                await self.human_delay(400, 800)
                if await self.has_validation_errors():
                    logger.warning("Validation errors detected after clicking Next.")
                    return False
                return True
        return False

    async def click_review(self) -> bool:
        """Clicks 'Review your application' or 'Review'."""
        modal = self.get_modal()
        selectors = [
            "button[aria-label='Review your application']:visible",
            "button:visible:has-text('Review your application')",
            "button:visible:has-text('Review')"
        ]
        for sel in selectors:
            btn = modal.locator(sel).first
            if await btn.is_visible(timeout=1000):
                btn_text = (await btn.inner_text()).strip().lower()
                if "next" in btn_text:
                    continue
                try:
                    await btn.click(timeout=2500)
                except Exception:
                    await btn.click(force=True)
                await self.human_delay(1000, 2000)
                if await self.has_validation_errors():
                    logger.warning("Validation errors detected after clicking Review.")
                    return False
                return True
        return False

    async def click_submit(self) -> bool:
        """Clicks the final 'Submit application' button."""
        modal = self.get_modal()
        selectors = [
            "button[aria-label='Submit application']:visible",
            "button:visible:has-text('Submit application')"
        ]
        for sel in selectors:
            btn = modal.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await self.safe_click(btn)
                await self.human_delay(2500, 4000)
                # Dismiss post-submission confirmation if present
                post_done_btn = self.page.locator("button[aria-label='Dismiss']:visible, button:visible:has-text('Done')").first
                if await post_done_btn.is_visible(timeout=2500):
                    await self.safe_click(post_done_btn)
                return True
        return False

    async def dismiss_modal(self) -> None:
        """Closes the modal and confirms discard if prompted."""
        try:
            if not self.page or self.page.is_closed():
                return
            modal = self.get_modal()
            dismiss_btn = modal.locator("button[aria-label='Dismiss'], button[data-test-modal-close-btn]").first
            if await dismiss_btn.is_visible():
                await self.safe_click(dismiss_btn)
                await self.human_delay(500, 1000)

                if self.page.is_closed():
                    return
                discard_btn = self.page.locator("button[data-control-name='discard_application_confirm_btn'], button:has-text('Discard')").first
                if await discard_btn.is_visible(timeout=1500):
                    await self.safe_click(discard_btn)
                    await self.human_delay(500, 1000)
        except Exception as e:
            logger.debug(f"dismiss_modal non-fatal error: {e}")

    async def _find_label_for_element(self, locator: Locator) -> str:
        """Finds human-readable label text associated with a form input."""
        modal = self.get_modal()
        elem_id = await locator.get_attribute("id")
        if elem_id:
            label_loc = modal.locator(f"label[for='{elem_id}']").first
            if await label_loc.count() > 0:
                txt = (await label_loc.inner_text()).strip()
                if txt:
                    return txt

        # Check aria-label
        aria = await locator.get_attribute("aria-label")
        if aria and aria.strip():
            return aria.strip()

        # Check aria-labelledby
        aria_lbl_id = await locator.get_attribute("aria-labelledby")
        if aria_lbl_id:
            lbl_el = modal.locator(f"[id='{aria_lbl_id}']").first
            if await lbl_el.count() > 0:
                txt = (await lbl_el.inner_text()).strip()
                if txt:
                    return txt

        # Check enclosing or ancestor form grouping
        grouping = locator.locator("xpath=ancestor::div[contains(@class, 'fb-dash-form-element') or contains(@class, 'jobs-easy-apply-form-section__grouping') or contains(@class, 'artdeco-text-input') or contains(@class, 'artdeco-dropdown')][1]")
        if await grouping.count() > 0:
            label_elem = grouping.locator("label, legend, span.fb-dash-form-element__label, .artdeco-text-input--label").first
            if await label_elem.count() > 0:
                txt = (await label_elem.inner_text()).strip()
                if txt:
                    return txt

        # Check immediate parent or grandparent text
        for xp in ["xpath=..", "xpath=../.."]:
            parent = locator.locator(xp)
            if await parent.count() > 0:
                lbl = parent.locator("label").first
                if await lbl.count() > 0:
                    txt = (await lbl.inner_text()).strip()
                    if txt:
                        return txt
                txt = (await parent.inner_text()).strip()
                lines = [line.strip() for line in txt.split("\n") if line.strip() and not line.strip().lower().startswith("select an option")]
                if lines and len(lines[0]) < 500:
                    return lines[0]

        return "Unlabeled Input"

    async def _find_help_text_for_element(self, locator: Locator) -> Optional[str]:
        """Finds helper, example, or sub-label text associated with a form control."""
        try:
            grouping = locator.locator("xpath=ancestor::div[contains(@class, 'fb-dash-form-element') or contains(@class, 'jobs-easy-apply-form-section__grouping') or contains(@class, 'artdeco-text-input') or contains(@class, 'artdeco-dropdown')][1]")
            if await grouping.count() > 0:
                sub_label = grouping.locator("span.fb-dash-form-element__sub-label, p.fb-dash-form-element__sub-label, .artdeco-text-input--help-text, .t-12.t-black--light").first
                if await sub_label.count() > 0 and await sub_label.is_visible():
                    txt = (await sub_label.inner_text()).strip()
                    if txt:
                        return txt
        except Exception:
            pass
        return None

    async def _find_error_for_element(self, locator: Locator) -> Optional[str]:
        """Finds inline validation error message for a specific form control."""
        try:
            grouping = locator.locator("xpath=ancestor::div[contains(@class, 'fb-dash-form-element') or contains(@class, 'jobs-easy-apply-form-section__grouping') or contains(@class, 'artdeco-text-input') or contains(@class, 'artdeco-dropdown')][1]")
            if await grouping.count() > 0:
                err_loc = grouping.locator("div.artdeco-inline-feedback--error:visible, p.artdeco-inline-feedback__message:visible, span.artdeco-inline-feedback__message:visible, [data-test-form-element-error-messages]:visible").first
                if await err_loc.count() > 0 and await err_loc.is_visible():
                    txt = (await err_loc.inner_text()).strip()
                    if txt:
                        return txt
        except Exception:
            pass
        return None

    async def get_field_current_value(self, field: FormField) -> Optional[str]:
        """Reads the live current value for a given field from the modal."""
        modal = self.get_modal()
        clean_label = field.label.replace("*", "").strip()
        try:
            if field.field_type in [FormFieldType.TEXT, FormFieldType.NUMBER]:
                loc = None
                if field.field_id and not field.field_id.startswith("input_"):
                    loc = modal.locator(f"[id='{field.field_id}']").first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.get_by_label(clean_label, exact=False).first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.locator(f"div:has-text('{clean_label}') input").first
                if loc and await loc.count() > 0:
                    val = (await loc.input_value()).strip()
                    return val if val else None

            elif field.field_type == FormFieldType.SELECT:
                loc = None
                if field.field_id and not field.field_id.startswith("select_"):
                    loc = modal.locator(f"[id='{field.field_id}']").first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.get_by_label(clean_label[:40], exact=False).first
                if not loc or await loc.count() == 0 or not await loc.is_visible():
                    loc = modal.locator(f"div:has-text('{clean_label[:30]}') select").first
                if loc and await loc.count() > 0:
                    val = await loc.evaluate("el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''")
                    if val and not val.lower().startswith("select"):
                        return val

            elif field.field_type == FormFieldType.RADIO:
                modal_fieldset = None
                if field.selector and "name=" in field.selector:
                    try:
                        candidate = modal.locator(f"fieldset:has({field.selector}), div:has({field.selector})").first
                        if await candidate.count() > 0:
                            modal_fieldset = candidate
                    except Exception:
                        pass
                if not modal_fieldset and field.field_id and not field.field_id.startswith("fieldset_"):
                    try:
                        candidate = modal.locator(f"fieldset:has(input[name='{field.field_id}']), div:has(input[name='{field.field_id}']), [id='{field.field_id}']").first
                        if await candidate.count() > 0:
                            modal_fieldset = candidate
                    except Exception:
                        pass
                if not modal_fieldset and field.field_id and field.field_id.startswith("fieldset_"):
                    try:
                        idx = int(field.field_id.split("_")[1])
                        modal_fieldset = modal.locator("fieldset:visible").nth(idx)
                    except Exception:
                        pass
                if not modal_fieldset or await modal_fieldset.count() == 0:
                    clean_kw = re.sub(r'[^a-zA-Z0-9 ]', '', clean_label).strip()
                    first_words = " ".join(clean_kw.split()[:4])
                    if first_words:
                        modal_fieldset = modal.locator("fieldset:visible").filter(has_text=first_words).first
                if modal_fieldset and await modal_fieldset.count() > 0:
                    checked = modal_fieldset.locator("input[type='radio']:checked")
                    if await checked.count() > 0:
                        radio_id = await checked.first.get_attribute("id")
                        if radio_id:
                            lbl = modal_fieldset.locator(f"label[for='{radio_id}']").first
                            if await lbl.count() > 0:
                                txt = (await lbl.inner_text()).strip()
                                if txt:
                                    return txt
                        return (await checked.first.get_attribute("value") or "").strip()
        except Exception as e:
            logger.debug(f"Error reading current value for {field.label}: {e}")
        return None

    async def wait_for_human_field_input(
        self,
        field: FormField,
        timeout_sec: int = 60,
        check_interval: float = 1.0
    ) -> Optional[str]:
        """
        Pauses and monitors the visible browser modal for human user interaction on a specific field.
        Returns the entered/selected value once detected, or None if timeout elapses.
        """
        initial_val = await self.get_field_current_value(field) or ""
        elapsed = 0.0
        while elapsed < timeout_sec:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            # Check if modal was closed or moved past this step
            if not await self.is_modal_open(timeout_ms=500):
                return None

            cur_val = await self.get_field_current_value(field)
            if cur_val and cur_val != initial_val:
                logger.info(f"Detected human response in browser for '{field.label}': '{cur_val}'")
                return cur_val

            # Also check if review step reached or Next was clicked by user
            if await self.is_review_step():
                return cur_val or initial_val or None

        return None
