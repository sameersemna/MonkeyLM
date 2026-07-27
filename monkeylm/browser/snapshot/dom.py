from __future__ import annotations

from typing import Any, Dict

from playwright.async_api import Page


async def capture_dom_and_layout(page: Page) -> Dict[str, Any]:
    return await page.evaluate(
        """() => {
            const collectText = (el) => {
                let txt = el.innerText?.trim()
                    || el.getAttribute('aria-label')
                    || el.getAttribute('name')
                    || el.placeholder
                    || el.getAttribute('title')
                    || el.value
                    || '';
                if (txt.length > 80) txt = txt.slice(0, 80) + '...';
                return txt;
            };

            const normalizeAttr = (el, name) => {
                const v = el.getAttribute(name);
                return (v === null || v === undefined) ? '' : String(v).trim();
            };

            const isVisible = (el) => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };

            const resolveLabel = (el) => {
                let labelText = '';
                let confidence = 0.0;

                if (el.id) {
                    const explicitLabel = document.querySelector(`label[for="${el.id}"]`);
                    if (explicitLabel) {
                        labelText = explicitLabel.innerText?.trim() || '';
                        confidence = 1.0;
                    }
                }

                if (!labelText) {
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const refs = labelledBy.split(/\\s+/).map(id => document.getElementById(id)).filter(Boolean);
                        if (refs.length > 0) {
                            labelText = refs.map(ref => ref.innerText?.trim() || ref.getAttribute('aria-label') || '').join(' ').trim();
                            confidence = 0.95;
                        }
                    }
                }

                if (!labelText) {
                    let ancestor = el.parentElement;
                    while (ancestor && ancestor.tagName !== 'LABEL' && ancestor.tagName !== 'FORM') {
                        ancestor = ancestor.parentElement;
                    }
                    if (ancestor && ancestor.tagName === 'LABEL') {
                        labelText = ancestor.innerText?.trim() || '';
                        confidence = 0.9;
                    }
                }

                if (!labelText) {
                    const prev = el.previousElementSibling;
                    if (prev && /^(label|span|div|p)$/i.test(prev.tagName)) {
                        const txt = prev.innerText?.trim() || '';
                        if (txt.length > 0 && txt.length < 120) {
                            labelText = txt;
                            confidence = 0.7;
                        }
                    }
                }

                if (!labelText && el.placeholder) {
                    labelText = el.placeholder.trim();
                    confidence = 0.5;
                }

                if (!labelText) {
                    const token = el.getAttribute('name') || el.id || '';
                    if (token) {
                        labelText = token.replace(/[_-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').trim();
                        confidence = 0.4;
                    }
                }

                if (labelText.length > 80) labelText = labelText.slice(0, 80) + '...';
                return { text: labelText, confidence };
            };

            const computeSemanticKind = (el) => {
                const tag = el.tagName.toLowerCase();
                if (tag === 'select') return 'select';
                if (tag === 'textarea') return 'textarea';
                if (tag === 'input') {
                    const type = (el.type || 'text').toLowerCase();
                    if (type === 'email') return 'email';
                    if (type === 'password') return 'password';
                    if (type === 'tel') return 'phone';
                    if (type === 'number' || type === 'range') return 'numeric';
                    if (type === 'search') return 'search';
                    if (type === 'url') return 'url';
                    if (type === 'date' || type === 'datetime-local' || type === 'time') return 'datetime';
                    if (type === 'checkbox') return 'checkbox';
                    if (type === 'radio') return 'radio';
                    if (type === 'file') return 'file';
                    if (type === 'hidden') return 'hidden';
                    return 'text';
                }
                return 'generic';
            };

            const interactives = Array.from(document.querySelectorAll(
                'button, a, input, select, textarea, form, [onclick], ' +
                '[role="button"], [role="link"], [role="checkbox"], [role="radio"], ' +
                '[role="switch"], [role="tab"], [role="menuitem"], [role="option"], ' +
                '[tabindex], [contenteditable="true"]'
            ));
            const tags = [];
            const anchors = {};
            let visibleIndex = 0;

            const elementIdMap = new Map();
            interactives.forEach((el) => {
                if (!isVisible(el)) return;
                elementIdMap.set(el, visibleIndex);
                const itemId = visibleIndex;
                visibleIndex += 1;
                const text = collectText(el);
                let typeInfo = el.tagName;
                if (el.tagName === 'INPUT') typeInfo = `INPUT[type=${el.type}]`;
                tags.push(`[id=${itemId}] <${typeInfo} text="${text}" />`);

                const idPart = el.id ? `#${el.id}` : '';
                const clsPart = (el.className && typeof el.className === 'string')
                    ? '.' + el.className.split(/\\s+/).slice(0, 2).join('.')
                    : '';
                const key = `${itemId}::${el.tagName}${idPart}${clsPart}::${text.slice(0, 20)}`;
                const rect = el.getBoundingClientRect();
                anchors[key] = { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
            });

            const formControls = [];
            const formInputs = Array.from(document.querySelectorAll('input, select, textarea'));
            formInputs.forEach((el) => {
                if (!isVisible(el)) return;
                const controlId = elementIdMap.get(el);
                if (controlId === undefined) return;

                const labelInfo = resolveLabel(el);
                const semanticKind = computeSemanticKind(el);
                const formEl = el.closest('form');
                const formId = formEl ? (formEl.id || `form_${Array.from(document.querySelectorAll('form')).indexOf(formEl)}`) : null;

                const optionValues = (el.tagName.toLowerCase() === 'select')
                    ? Array.from(el.querySelectorAll('option')).map(o => o.value || o.textContent.trim()).filter(v => v)
                    : [];

                formControls.push({
                    control_id: controlId,
                    form_id: formId,
                    tag_name: el.tagName.toLowerCase(),
                    input_type: el.type ? String(el.type).toLowerCase() : '',
                    name_attr: normalizeAttr(el, 'name'),
                    id_attr: normalizeAttr(el, 'id'),
                    placeholder: normalizeAttr(el, 'placeholder'),
                    aria_label: normalizeAttr(el, 'aria-label'),
                    aria_labelledby: normalizeAttr(el, 'aria-labelledby'),
                    required: el.required === true,
                    disabled: el.disabled === true,
                    readonly: el.readOnly === true,
                    minlength: el.minLength ? parseInt(el.minLength, 10) : null,
                    maxlength: el.maxLength ? parseInt(el.maxLength, 10) : null,
                    pattern: normalizeAttr(el, 'pattern'),
                    min_value: normalizeAttr(el, 'min'),
                    max_value: normalizeAttr(el, 'max'),
                    step: normalizeAttr(el, 'step'),
                    resolved_label: labelInfo.text,
                    label_confidence: labelInfo.confidence,
                    semantic_kind: semanticKind,
                    visible: true,
                    options: optionValues,
                });
            });

            const forms = [];
            const allForms = Array.from(document.querySelectorAll('form'));
            allForms.forEach((formEl, idx) => {
                if (!isVisible(formEl)) return;
                const fid = formEl.id || `form_${idx}`;
                const controlIds = formControls
                    .filter(fc => fc.form_id === fid)
                    .map(fc => fc.control_id);

                let submitCandidateId = null;
                const submitBtn = formEl.querySelector('button[type="submit"], input[type="submit"]');
                if (submitBtn && isVisible(submitBtn)) {
                    submitCandidateId = elementIdMap.get(submitBtn);
                }

                var actionVal = normalizeAttr(formEl, 'action');
                var methodVal = normalizeAttr(formEl, 'method') || 'get';
                if (/^javascript:/i.test(actionVal) || /^data:/i.test(actionVal)) {
                    actionVal = '';
                }
                forms.push({
                    form_id: fid,
                    action: actionVal,
                    method: methodVal,
                    control_ids: controlIds,
                    submit_candidate_id: submitCandidateId,
                });
            });

            const looseControls = formControls.filter(fc => fc.form_id === null);
            if (looseControls.length > 0) {
                forms.push({
                    form_id: 'loose_controls',
                    action: '',
                    method: '',
                    control_ids: looseControls.map(fc => fc.control_id),
                    submit_candidate_id: null,
                });
            }

            const modals = Array.from(document.querySelectorAll('[role="dialog"], .modal, .popup, .alert'))
                .filter(el => isVisible(el));

            const spinnerSel = '[aria-busy="true"], .spinner, .loading, [data-testid*="spinner" i]';
            const spinnerCount = document.querySelectorAll(spinnerSel).length;
            const disabledControls = document.querySelectorAll(
                'button:disabled, input:disabled, select:disabled, textarea:disabled'
            ).length;

            const structure = tags.map(t => t.replace(/text=".*?"/, 'text=""')).join('|');
            const bodyTextLength = (document.body?.innerText || '').trim().length;
            const bodyChildCount = document.body ? document.body.querySelectorAll('*').length : 0;

            return {
                url: window.location.href,
                title: document.title,
                elements: tags,
                structure,
                layoutAnchors: anchors,
                modalCount: modals.length,
                spinnerCount,
                disabledControls,
                formControls,
                forms,
                bodyTextLength,
                bodyChildCount,
            };
        }"""
    )
