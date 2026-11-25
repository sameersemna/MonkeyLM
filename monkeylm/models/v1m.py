"""V1M native System One shadow decision integration."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, Iterable, List, Optional

from monkeylm.config import Settings, _local_service_log, httpx


class V1MDecisionError(RuntimeError):
    """Raised when the V1M native decision endpoint is unusable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        kind: str = "api_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


def normalize_v1m_base_url(base_url: Optional[str]) -> str:
    """Normalize a V1M origin or endpoint into the native System One URL."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        base = "https://v1m.ir"
    if base.endswith("/v1/systemone"):
        return base
    if base.endswith("/v1"):
        return f"{base}/systemone"
    if base.endswith("/systemone"):
        return f"{base}"
    return f"{base}/v1/systemone"


def _require_httpx() -> Any:
    if httpx is None:
        raise V1MDecisionError(
            "httpx is required to call the V1M native decision endpoint",
            kind="dependency_error",
        )
    return httpx


def decide_v1m(
    settings: Settings,
    *,
    state: Any,
    questions: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Send one native V1M System One request and return its decoded payload."""
    http = _require_httpx()
    url = normalize_v1m_base_url(settings.v1m_base_url)
    api_key = (settings.v1m_api_key or "").strip()
    if not api_key:
        raise V1MDecisionError("V1M API key is not configured", kind="missing_api_key")

    payload = {
        "state": state,
        "model": settings.v1m_model,
        "questions": questions,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    for attempt in range(2):
        try:
            response = http.post(
                url,
                json=payload,
                headers=headers,
                timeout=max(0.1, float(settings.v1m_timeout_seconds)),
            )
        except Exception as exc:
            name = type(exc).__name__.lower()
            text = str(exc).lower()
            if "timeout" in name or "timeout" in text:
                raise V1MDecisionError(
                    "V1M decision request timed out",
                    kind="timeout",
                ) from exc
            raise V1MDecisionError(
                "V1M decision transport failed",
                kind="transport_error",
            ) from exc

        status_code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status_code < 300:
            try:
                decoded = response.json()
            except Exception as exc:
                raise V1MDecisionError(
                    "V1M decision endpoint returned invalid JSON",
                    status_code=status_code,
                    kind="malformed_response",
                ) from exc
            if not isinstance(decoded, dict):
                raise V1MDecisionError(
                    "V1M decision endpoint returned an unexpected payload",
                    status_code=status_code,
                    kind="malformed_response",
                )
            return decoded

        if status_code in {429, 503, 529} and attempt == 0:
            time.sleep(0.25)
            continue

        raise V1MDecisionError(
            f"V1M decision endpoint returned HTTP {status_code}",
            status_code=status_code,
            kind="http_error",
        )

    raise V1MDecisionError(
        "V1M decision endpoint failed after retry",
        kind="http_error",
    )


def parse_v1m_choice(
    payload: Dict[str, Any],
    *,
    question_name: str = "next_action",
) -> Dict[str, Any]:
    """Parse and validate the native next_action choice answer."""
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise V1MDecisionError(
            "V1M response omitted its answers object",
            kind="malformed_response",
        )
    answer = answers.get(question_name)
    if not isinstance(answer, dict):
        raise V1MDecisionError(
            f"V1M response omitted {question_name}",
            kind="malformed_response",
        )
    choice = answer.get("choice")
    if not isinstance(choice, str) or not choice.strip():
        raise V1MDecisionError(
            "V1M choice answer was missing or invalid",
            kind="malformed_response",
        )
    try:
        confidence = float(answer.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise V1MDecisionError(
            "V1M choice confidence was missing or invalid",
            kind="malformed_response",
        ) from exc
    if confidence < 0.0 or confidence > 1.0:
        raise V1MDecisionError(
            "V1M choice confidence was outside [0, 1]",
            kind="malformed_response",
        )

    probabilities_raw = answer.get("probabilities")
    probabilities: Dict[str, float] = {}
    if isinstance(probabilities_raw, dict):
        for option, probability in probabilities_raw.items():
            try:
                value = float(probability)
            except (TypeError, ValueError):
                continue
            if 0.0 <= value <= 1.0:
                probabilities[str(option)] = value

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    return {
        "choice": choice.strip(),
        "confidence": confidence,
        "probabilities": probabilities,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
    }


def _sanitize_shadow_state(value: Any) -> str:
    text = str(value or "")
    control_chars = "".join(chr(code) for code in range(32) if code not in (9, 10, 13))
    text = text.translate(str.maketrans("", "", control_chars))
    text = text.replace("<<<", "<< <").replace(">>>", "> >")
    if len(text) > 64_000:
        text = text[:64_000] + "\n...[truncated]"
    return text


def _extract_target_id(target: Any) -> Optional[int]:
    match = re.search(r"\[id=(\d+)\]", str(target or ""), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _snapshot_target_ids(snapshot: Any) -> set:
    ids = set()
    for element in getattr(snapshot, "elements", []) or []:
        for match in re.finditer(r"\[id=(\d+)\]", str(element), re.IGNORECASE):
            ids.add(int(match.group(1)))
    return ids


def _typeable_control_ids(snapshot: Any) -> set:
    ids = set()
    for control in getattr(snapshot, "form_controls", []) or []:
        if getattr(control, "visible", True) is False:
            continue
        if getattr(control, "disabled", False):
            continue
        if getattr(control, "readonly", False):
            continue
        if str(getattr(control, "tag_name", "")).lower() not in {"input", "textarea", "select"}:
            continue
        ids.add(int(getattr(control, "control_id")))
    return ids


def _candidate(
    candidate_id: str,
    action: str,
    target: str,
    description: str,
) -> Dict[str, str]:
    return {
        "id": candidate_id,
        "action": action,
        "target": target,
        "description": description,
    }


def build_v1m_candidates(
    primary_plan: Dict[str, Any],
    snapshot: Any,
    max_candidates: int,
    *,
    loop_context: bool = False,
    seen_click_targets: Optional[Iterable[str]] = None,
    blacklisted_targets: Optional[Iterable[str]] = None,
    failed_targets: Optional[Iterable[str]] = None,
    repeat_count: int = 0,
) -> List[Dict[str, str]]:
    """Build a bounded finite action space from the current visible DOM."""
    limit = min(8, max(2, int(max_candidates)))
    candidate_list: List[Dict[str, str]] = []
    seen = set()
    seen_targets = {
        str(target).strip()
        for target in (seen_click_targets or [])
        if str(target).strip()
    }
    blacklisted_target_set = {
        str(target).strip()
        for target in (blacklisted_targets or [])
        if str(target).strip()
    }
    failed_target_set = {
        str(target).strip()
        for target in (failed_targets or [])
        if str(target).strip()
    }

    def add(candidate: Dict[str, str]) -> None:
        candidate_id = candidate["id"]
        candidate_target = str(candidate.get("target", "")).strip()
        candidate_action = str(candidate.get("action", "")).strip().lower()
        if loop_context:
            if candidate_action in {"type", "submit_form", "press_key", "random_jump", "restart_target"}:
                return
            if candidate_target and candidate_target not in seen_targets | blacklisted_target_set | failed_target_set:
                pass
            elif candidate_target:
                primary_target = str(primary_plan.get("target", "")).strip()
                if candidate_target != primary_target:
                    return
        if candidate_id not in seen and len(candidate_list) < limit:
            seen.add(candidate_id)
            candidate_list.append(candidate)

    action = str(primary_plan.get("action", "scroll")).strip().lower()
    target = str(primary_plan.get("target", "")).strip()
    target_id = _extract_target_id(target)
    snapshot_ids = _snapshot_target_ids(snapshot)
    typeable_ids = _typeable_control_ids(snapshot)

    primary_valid = False
    if action in {"click", "submit_form"}:
        if target_id is not None and target_id in snapshot_ids:
            primary_valid = True
    elif action == "type":
        if target_id is not None and target_id in typeable_ids:
            primary_valid = True
    elif action == "handle_modal":
        if int(getattr(snapshot, "modal_count", 0) or 0) > 0:
            primary_valid = True
    elif action in {"scroll", "back", "press_key", "random_jump", "restart_target"}:
        primary_valid = True

    if primary_valid:
        if action == "press_key":
            value = str(primary_plan.get("value", "Enter") or "Enter")
            candidate_id = f"press_key:{value}"
            add(_candidate(candidate_id, action, "", f"Press the {value} keyboard key"))
        elif action in {"scroll", "back", "random_jump", "restart_target"}:
            descriptions = {
                "scroll": "Scroll the current page to reveal more content",
                "back": "Navigate back in browser history",
                "random_jump": "Use a bounded exploration jump to a different in-scope page",
                "restart_target": "Restart navigation at the configured target URL",
            }
            add(_candidate(action, action, "", descriptions[action]))
        elif action == "handle_modal":
            add(_candidate("handle_modal", action, target, "Handle or close the currently visible modal"))
        else:
            candidate_id = f"{action}:{target}"
            add(
                _candidate(
                    candidate_id,
                    action,
                    target,
                    f"Use {action} on the visible target {target}",
                )
            )

    add(_candidate("scroll", "scroll", "", "Scroll the current page to reveal more content"))
    add(_candidate("back", "back", "", "Navigate back in browser history"))

    if int(getattr(snapshot, "modal_count", 0) or 0) > 0:
        add(
            _candidate(
                "handle_modal",
                "handle_modal",
                "",
                "Handle or close the currently visible modal",
            )
        )

    for form_control in getattr(snapshot, "form_controls", []) or []:
        if len(candidate_list) >= limit:
            break
        control_id = int(getattr(form_control, "control_id"))
        if control_id not in typeable_ids:
            continue
        target = f"[id={control_id}]"
        add(
            _candidate(
                f"type:{target}",
                "type",
                target,
                f"Enter a bounded test value into visible form control {target}",
            )
        )

    for form in getattr(snapshot, "forms", []) or []:
        if len(candidate_list) >= limit:
            break
        submit_id = getattr(form, "submit_candidate_id", None)
        if submit_id is None:
            continue
        target = f"[id={submit_id}]"
        if submit_id not in snapshot_ids:
            continue
        add(
            _candidate(
                f"submit_form:{target}",
                "submit_form",
                target,
                f"Submit the visible form using submit target {target}",
            )
        )

    for element in getattr(snapshot, "elements", []) or []:
        if len(candidate_list) >= limit:
            break
        element_text = str(element)
        if not re.search(r"<(BUTTON|A|INPUT)(?:\b|>)", element_text, re.IGNORECASE):
            continue
        target_match = re.search(r"\[id=(\d+)\]", element_text)
        if target_match is None:
            continue
        target = f"[id={target_match.group(1)}]"
        add(
            _candidate(
                f"click:{target}",
                "click",
                target,
                f"Click visible control {target}",
            )
        )

    return candidate_list


def primary_v1m_candidate(
    primary_plan: Dict[str, Any],
    snapshot: Any,
    candidates: Iterable[Dict[str, str]],
) -> str:
    """Return the candidate ID corresponding to the existing primary plan."""
    action = str(primary_plan.get("action", "scroll")).strip().lower()
    target = str(primary_plan.get("target", "")).strip()
    if action in {"scroll", "back", "random_jump", "restart_target"}:
        candidate_id = action
    elif action == "press_key":
        candidate_id = f"press_key:{str(primary_plan.get('value', 'Enter') or 'Enter')}"
    elif action == "handle_modal":
        candidate_id = "handle_modal"
    elif target:
        candidate_id = f"{action}:{target}"
    else:
        candidate_id = "scroll"

    candidate_ids = {candidate["id"] for candidate in candidates}
    if candidate_id in candidate_ids:
        return candidate_id
    return candidate_id


def v1m_candidate_to_action_plan(
    choice: str,
    candidates: Iterable[Dict[str, str]],
    snapshot: Any,
    *,
    seen_click_targets: Optional[Iterable[str]] = None,
    blacklisted_targets: Optional[Iterable[str]] = None,
    failed_targets: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Translate an approved V1M choice into a DOM-validated action plan."""
    selected = next(
        (candidate for candidate in candidates if candidate.get("id") == choice),
        None,
    )
    if selected is None:
        return None

    action = str(selected.get("action", "")).strip().lower()
    target = str(selected.get("target", "")).strip()
    if action == "click":
        target_id = _extract_target_id(target)
        if target_id is None or target_id not in _snapshot_target_ids(snapshot):
            return None
        seen = {str(value).strip() for value in (seen_click_targets or []) if str(value).strip()}
        blacklisted = {str(value).strip() for value in (blacklisted_targets or []) if str(value).strip()}
        failed = {str(value).strip() for value in (failed_targets or []) if str(value).strip()}
        if target in seen or target in blacklisted or target in failed:
            return None
        return {
            "action": "click",
            "target": target,
            "value": "",
            "action_strategy": "",
            "input_payloads": [],
        }
    if action == "handle_modal":
        if int(getattr(snapshot, "modal_count", 0) or 0) <= 0:
            return None
        return {
            "action": "handle_modal",
            "target": "",
            "value": "",
            "action_strategy": "",
            "input_payloads": [],
        }
    if action in {"scroll", "back"}:
        return {
            "action": action,
            "target": "",
            "value": "",
            "action_strategy": "",
            "input_payloads": [],
        }
    return None


async def evaluate_v1m_shadow_decision(
    settings: Settings,
    *,
    page_state: str,
    snapshot: Any,
    primary_plan: Dict[str, Any],
    loop_context: bool = False,
    seen_click_targets: Optional[Iterable[str]] = None,
    blacklisted_targets: Optional[Iterable[str]] = None,
    failed_targets: Optional[Iterable[str]] = None,
    repeat_count: int = 0,
) -> Dict[str, Any]:
    """Evaluate V1M alongside the primary plan without changing execution."""
    telemetry: Dict[str, Any] = {
        "mode": "shadow",
        "status": "skipped",
        "outcome": "disabled",
        "primary_choice": "",
        "v1m_choice": "",
        "confidence": None,
        "probabilities": {},
        "latency_ms": 0.0,
        "candidates": [],
        "usage": {},
        "rerank_applied": False,
        "rerank_plan": None,
        "rerank_blocked_reason": "",
    }

    if not getattr(settings, "v1m_shadow_enabled", False) and not getattr(settings, "v1m_rerank_enabled", False):
        return telemetry

    api_key = str(getattr(settings, "v1m_api_key", "") or "").strip()
    if not api_key:
        telemetry["outcome"] = "missing_api_key"
        return telemetry

    candidates = build_v1m_candidates(
        primary_plan,
        snapshot,
        getattr(settings, "v1m_max_candidates", 6),
        loop_context=loop_context,
        seen_click_targets=seen_click_targets,
        blacklisted_targets=blacklisted_targets,
        failed_targets=failed_targets,
        repeat_count=repeat_count,
    )
    telemetry["candidates"] = [
        {"id": candidate["id"], "action": candidate["action"], "target": candidate["target"]}
        for candidate in candidates
    ]
    if len(candidates) < 2:
        telemetry["outcome"] = "insufficient_candidates"
        return telemetry

    primary_choice = primary_v1m_candidate(primary_plan, snapshot, candidates)
    telemetry["primary_choice"] = primary_choice
    criteria = {
        candidate["id"]: candidate["description"]
        for candidate in candidates
    }
    loop_context_payload = {
        "loop_context": bool(loop_context),
        "repeat_count": int(repeat_count),
        "seen_click_targets": sorted({
            str(target).strip()
            for target in (seen_click_targets or [])
            if str(target).strip()
        }),
        "blacklisted_targets": sorted({
            str(target).strip()
            for target in (blacklisted_targets or [])
            if str(target).strip()
        }),
        "failed_targets": sorted({
            str(target).strip()
            for target in (failed_targets or [])
            if str(target).strip()
        }),
    }
    state_payload = (
        "MonkeyLM bounded browser-testing decision state:\n"
        f"{_sanitize_shadow_state(page_state)}\n"
        "Existing primary plan:\n"
        f"{_sanitize_shadow_state(primary_plan)}\n"
        "Loop context:\n"
        f"{_sanitize_shadow_state(loop_context_payload)}\n"
        "Candidate set is finite. Do not select seen, blacklisted, failed, or write-form targets."
    )
    questions = {
        "next_action": {
            "type": "choice",
            "instructions": (
                "Choose the one bounded next action most likely to improve test coverage "
                "or break a repeated browser state. Do not invent targets or actions "
                "outside the criteria."
            ),
            "criteria": criteria,
        }
    }

    print(
        f"   └─ 🧭 V1M shadow calling {normalize_v1m_base_url(settings.v1m_base_url)} "
        f"primary={primary_choice} candidates={len(candidates)} "
        f"[{', '.join(candidate['id'] for candidate in candidates)}]"
    )
    started = time.perf_counter()
    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(
                decide_v1m,
                settings,
                state=state_payload,
                questions=questions,
            ),
            timeout=max(0.1, float(settings.v1m_timeout_seconds) + 0.5),
        )
        parsed = parse_v1m_choice(payload)
    except asyncio.TimeoutError:
        telemetry["latency_ms"] = round(
            (time.perf_counter() - started) * 1000.0,
            3,
        )
        telemetry["outcome"] = "timeout"
        telemetry["status"] = "fallback"
        print(
            f"   └─ 🧭 V1M shadow fallback=timeout "
            f"latency={telemetry['latency_ms']}ms"
        )
        _local_service_log(
            "V1M shadow decision timed out; continuing with the primary planner",
            settings.output_dir,
        )
        return telemetry
    except V1MDecisionError as exc:
        telemetry["latency_ms"] = round(
            (time.perf_counter() - started) * 1000.0,
            3,
        )
        telemetry["outcome"] = exc.kind
        telemetry["status"] = "fallback"
        print(
            f"   └─ 🧭 V1M shadow fallback={exc.kind} "
            f"latency={telemetry['latency_ms']}ms"
        )
        _local_service_log(
            f"V1M shadow decision unavailable ({exc.kind}); continuing with the primary planner",
            settings.output_dir,
        )
        return telemetry
    except Exception as exc:
        telemetry["latency_ms"] = round(
            (time.perf_counter() - started) * 1000.0,
            3,
        )
        telemetry["outcome"] = "unexpected_error"
        telemetry["status"] = "fallback"
        print(
            f"   └─ 🧭 V1M shadow fallback=unexpected_error "
            f"latency={telemetry['latency_ms']}ms"
        )
        _local_service_log(
            f"V1M shadow decision failed ({type(exc).__name__}); continuing with the primary planner",
            settings.output_dir,
        )
        return telemetry

    telemetry["latency_ms"] = round(
        (time.perf_counter() - started) * 1000.0,
        3,
    )
    telemetry["status"] = "evaluated"
    telemetry["v1m_choice"] = parsed["choice"]
    telemetry["confidence"] = parsed["confidence"]
    telemetry["probabilities"] = parsed["probabilities"]
    telemetry["usage"] = parsed["usage"]

    criteria_ids = {candidate["id"] for candidate in candidates}
    if parsed["choice"] not in criteria_ids:
        telemetry["outcome"] = "invalid_choice"
        telemetry["status"] = "fallback"
        probability = parsed["probabilities"].get(parsed["choice"])
        probability_text = (
            f"probability={probability:.3f} "
            if probability is not None
            else "probability=unknown "
        )
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"confidence={parsed['confidence']:.3f} {probability_text}"
            f"outcome=invalid_choice "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry
    if parsed["confidence"] < float(settings.v1m_confidence_threshold):
        telemetry["outcome"] = "low_confidence"
        probability = parsed["probabilities"].get(parsed["choice"])
        probability_text = (
            f"probability={probability:.3f} "
            if probability is not None
            else "probability=unknown "
        )
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"confidence={parsed['confidence']:.3f} {probability_text}"
            f"outcome=low_confidence "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry
    if parsed["choice"] == primary_choice:
        telemetry["outcome"] = "agrees"
        probability = parsed["probabilities"].get(parsed["choice"])
        probability_text = (
            f"probability={probability:.3f} "
            if probability is not None
            else "probability=unknown "
        )
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"confidence={parsed['confidence']:.3f} {probability_text}"
            f"outcome=agrees "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry

    primary_probability = parsed["probabilities"].get(primary_choice)
    chosen_probability = parsed["probabilities"].get(parsed["choice"])
    probability_margin = float(getattr(settings, "v1m_probability_margin", 0.15))
    if primary_probability is None or chosen_probability is None:
        telemetry["outcome"] = "weak_rerank"
        telemetry["rerank_blocked_reason"] = "missing_probability"
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"confidence={parsed['confidence']:.3f} probability=unknown "
            f"outcome=weak_rerank "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry

    advantage = float(chosen_probability) - float(primary_probability)
    if advantage < probability_margin:
        telemetry["outcome"] = "weak_rerank"
        telemetry["rerank_blocked_reason"] = "probability_margin"
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"confidence={parsed['confidence']:.3f} "
            f"probability={chosen_probability:.3f} "
            f"primary_probability={primary_probability:.3f} "
            f"advantage={advantage:.3f} outcome=weak_rerank "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry

    telemetry["outcome"] = "would_rerank"
    probability = parsed["probabilities"].get(parsed["choice"])
    probability_text = (
        f"probability={probability:.3f} "
        if probability is not None
        else "probability=unknown "
    )
    print(
        f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
        f"confidence={parsed['confidence']:.3f} {probability_text}"
        f"outcome=would_rerank "
        f"latency={telemetry['latency_ms']}ms"
    )
    return telemetry
