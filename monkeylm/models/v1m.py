"""V1M native System One shadow decision integration."""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
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
        self.retry_count = 0
        self.attempt_count = 0
        self.http_status = status_code
        self.retry_delays_ms: List[float] = []
        self.requested_model = ""
        self.response_model = ""
        self.model_changed = False
        self.model_gate = ""
        self.circuit_state = "closed"


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


_CIRCUIT_BREAKER_LOCK = threading.Lock()
_CIRCUIT_BREAKER_STATE: Dict[str, Dict[str, Any]] = {}


def reset_v1m_circuit_breaker() -> None:
    """Reset process-local V1M circuit-breaker state (for tests and manual recovery)."""
    with _CIRCUIT_BREAKER_LOCK:
        _CIRCUIT_BREAKER_STATE.clear()


def _circuit_allows(url: str, threshold: int, cooldown_seconds: float) -> tuple:
    now = time.monotonic()
    with _CIRCUIT_BREAKER_LOCK:
        state = _CIRCUIT_BREAKER_STATE.setdefault(
            url,
            {"failure_count": 0, "open_until": 0.0, "state": "closed"},
        )
        open_until = float(state.get("open_until", 0.0) or 0.0)
        if open_until > now:
            return False, "open", state
        if open_until and open_until <= now:
            state["failure_count"] = 0
            state["open_until"] = 0.0
            state["state"] = "closed"
        state["state"] = "closed"
        return True, "closed", state


def _circuit_record_success(url: str) -> None:
    with _CIRCUIT_BREAKER_LOCK:
        state = _CIRCUIT_BREAKER_STATE.setdefault(
            url,
            {"failure_count": 0, "open_until": 0.0, "state": "closed"},
        )
        state["failure_count"] = 0
        state["open_until"] = 0.0
        state["state"] = "closed"


def _circuit_record_failure(url: str, threshold: int, cooldown_seconds: float) -> str:
    with _CIRCUIT_BREAKER_LOCK:
        state = _CIRCUIT_BREAKER_STATE.setdefault(
            url,
            {"failure_count": 0, "open_until": 0.0, "state": "closed"},
        )
        state["failure_count"] = int(state.get("failure_count", 0) or 0) + 1
        if state["failure_count"] >= max(1, int(threshold)):
            state["open_until"] = time.monotonic() + max(1.0, float(cooldown_seconds))
            state["state"] = "open"
        else:
            state["state"] = "closed"
        return str(state["state"])


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            value = headers.get(name)
            if value:
                return str(value)
        except Exception:
            pass
    return ""


def _parse_retry_after(response: Any) -> float:
    value = _response_header(response, "retry-after").strip()
    if not value:
        return 0.0
    try:
        seconds = float(value)
        if seconds >= 0:
            return seconds
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return 0.0


def _x_should_retry(response: Any) -> Optional[bool]:
    value = _response_header(response, "x-should-retry").strip().lower()
    if value in {"false", "0", "no"}:
        return False
    if value in {"true", "1", "yes"}:
        return True
    return None


def _is_quota_or_spend_error(status_code: int, response: Any) -> bool:
    if status_code != 429:
        return False
    text = str(getattr(response, "text", "") or "").lower()
    markers = (
        "quota",
        "billing",
        "payment",
        "credit",
        "insufficient",
        "spend",
        "plan limit",
    )
    return any(marker in text for marker in markers)


def _full_jitter_delay(attempt: int, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt)))
    return random.uniform(0.0, max(0.0, ceiling))


def _is_model_alias(model_id: str) -> bool:
    value = str(model_id or "").strip().lower()
    return (
        value in {"latest", "rev-latest", "default"}
        or value.endswith("-latest")
        or value.endswith(":latest")
    )


_V1M_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "systemone"
_V1M_LOG_LOCK = threading.Lock()
_SECRET_KEY_RE = re.compile(
    r"(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"bearer|password|credential|cookie|set[_-]?cookie|secret)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY_RE = re.compile(r"(?i)\b(?:sk|v1m_live)[-_][A-Za-z0-9._~-]+")


def _sanitize_v1m_log_value(value: Any) -> Any:
    """Redact credential-shaped keys and text from V1M call logs."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_RE.search(key_text):
                sanitized[key_text] = "[REDACTED]"
            else:
                sanitized[key_text] = _sanitize_v1m_log_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_v1m_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_v1m_log_value(item) for item in value]
    if isinstance(value, str):
        text = _BEARER_RE.sub("Bearer [REDACTED]", value)
        text = _API_KEY_RE.sub("[REDACTED]", text)
        return text
    return value


def _safe_v1m_response_headers(response: Any) -> Dict[str, str]:
    headers = {}
    for name in (
        "content-type",
        "retry-after",
        "x-should-retry",
        "x-request-id",
        "request-id",
        "content-length",
    ):
        value = _response_header(response, name).strip()
        if value:
            headers[name] = value
    return headers


def write_v1m_call_log(
    settings: Settings,
    *,
    url: str,
    request_payload: Any,
    response_payload: Any = None,
    error_payload: Any = None,
    attempt: int = 1,
    now: Optional[datetime] = None,
    log_dir: Optional[Any] = None,
) -> Optional[Path]:
    """Write one sanitized V1M HTTP attempt to logs/systemone."""
    if not getattr(settings, "v1m_logs", False):
        return None

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    timestamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    filename_time = now.strftime("%Y%m%d_%H%M%S")
    directory = Path(log_dir) if log_dir is not None else _V1M_LOG_DIR

    request_record = {
        "method": "POST",
        "attempt": max(1, int(attempt)),
        "timeout_seconds": float(getattr(settings, "v1m_timeout_seconds", 3.0)),
        "payload": request_payload,
    }
    response_record: Dict[str, Any] = {}
    if response_payload is not None:
        response_record["body"] = response_payload
    if error_payload is not None:
        response_record["error"] = error_payload

    record = {
        "url": str(url),
        "timestamp": timestamp,
        "request": _sanitize_v1m_log_value(request_record),
        "response": _sanitize_v1m_log_value(response_record),
    }

    try:
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except (AttributeError, OSError):
            pass

        with _V1M_LOG_LOCK:
            candidate = directory / f"{filename_time}.json"
            collision = 1
            while candidate.exists():
                candidate = directory / f"{filename_time}_{collision:03d}.json"
                collision += 1

            fd, temporary_name = tempfile.mkstemp(
                dir=str(directory),
                prefix=".v1m_",
                suffix=".json",
            )
            temporary_path = Path(temporary_name)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, candidate)
                candidate.chmod(0o600)
            finally:
                if temporary_path.exists():
                    temporary_path.unlink(missing_ok=True)
        return candidate
    except Exception as exc:
        _local_service_log(
            f"Failed to write V1M call log: {type(exc).__name__}",
            getattr(settings, "output_dir", ""),
        )
        return None


def decide_v1m(
    settings: Settings,
    *,
    state: Any,
    questions: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Send one native V1M System One request and return its decoded payload."""
    url = normalize_v1m_base_url(settings.v1m_base_url)
    payload = {
        "state": state,
        "model": settings.v1m_model,
        "questions": questions,
    }
    try:
        http = _require_httpx()
    except V1MDecisionError as exc:
        write_v1m_call_log(
            settings,
            url=url,
            request_payload=payload,
            response_payload={
                "status_code": None,
                "headers": {},
                "body": None,
            },
            error_payload={
                "kind": exc.kind,
                "message": str(exc),
            },
            attempt=0,
        )
        raise
    api_key = (settings.v1m_api_key or "").strip()
    if not api_key:
        write_v1m_call_log(
            settings,
            url=url,
            request_payload=payload,
            response_payload=None,
            error_payload={
                "kind": "missing_api_key",
                "message": "V1M API key is not configured",
            },
            attempt=0,
        )
        raise V1MDecisionError("V1M API key is not configured", kind="missing_api_key")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    max_attempts = min(4, max(1, int(getattr(settings, "v1m_max_attempts", 3))))
    retry_base = min(5.0, max(0.0, float(getattr(settings, "v1m_retry_base_seconds", 0.25))))
    retry_max = min(30.0, max(0.1, float(getattr(settings, "v1m_retry_max_seconds", 2.0))))
    breaker_threshold = min(20, max(1, int(getattr(settings, "v1m_circuit_breaker_threshold", 3))))
    breaker_cooldown = min(3600.0, max(1.0, float(getattr(settings, "v1m_circuit_breaker_cooldown_seconds", 60.0))))

    allowed, circuit_state, _state = _circuit_allows(
        url,
        breaker_threshold,
        breaker_cooldown,
    )
    if not allowed:
        error = V1MDecisionError(
            "V1M circuit breaker is open",
            status_code=None,
            kind="circuit_open",
        )
        error.circuit_state = circuit_state
        error.requested_model = str(settings.v1m_model)
        write_v1m_call_log(
            settings,
            url=url,
            request_payload=payload,
            response_payload={
                "status_code": None,
                "headers": {},
                "body": None,
            },
            error_payload={
                "kind": "circuit_open",
                "message": "V1M circuit breaker is open",
            },
            attempt=0,
        )
        raise error

    retry_count = 0
    retry_delays_ms: List[float] = []
    http_status_history: List[int] = []
    attempt_count = 0

    for attempt in range(max_attempts):
        attempt_count = attempt + 1
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
            kind = "timeout" if "timeout" in name or "timeout" in text else "transport_error"
            write_v1m_call_log(
                settings,
                url=url,
                request_payload=payload,
                response_payload={
                    "status_code": None,
                    "headers": {},
                    "body": None,
                },
                error_payload={
                    "kind": kind,
                    "message": str(exc),
                    "attempt": attempt_count,
                },
                attempt=attempt_count,
            )
            if attempt < max_attempts - 1:
                retry_count += 1
                delay = _full_jitter_delay(attempt, retry_base, retry_max)
                retry_delays_ms.append(round(delay * 1000.0, 3))
                time.sleep(delay)
                continue
            circuit_state = _circuit_record_failure(
                url,
                breaker_threshold,
                breaker_cooldown,
            )
            error = V1MDecisionError(
                "V1M decision request timed out" if kind == "timeout" else "V1M decision transport failed",
                kind=kind,
            )
            error.retry_count = retry_count
            error.attempt_count = attempt_count
            error.retry_delays_ms = retry_delays_ms
            error.requested_model = str(settings.v1m_model)
            error.circuit_state = circuit_state
            raise error

        status_code = int(getattr(response, "status_code", 0) or 0)
        http_status_history.append(status_code)
        if 200 <= status_code < 300:
            try:
                decoded = response.json()
            except Exception as exc:
                write_v1m_call_log(
                    settings,
                    url=url,
                    request_payload=payload,
                    response_payload={
                        "status_code": status_code,
                        "headers": _safe_v1m_response_headers(response),
                        "body": None,
                    },
                    error_payload={
                        "kind": "malformed_response",
                        "message": "V1M decision endpoint returned invalid JSON",
                        "attempt": attempt_count,
                    },
                    attempt=attempt_count,
                )
                circuit_state = _circuit_record_failure(
                    url,
                    breaker_threshold,
                    breaker_cooldown,
                )
                error = V1MDecisionError(
                    "V1M decision endpoint returned invalid JSON",
                    status_code=status_code,
                    kind="malformed_response",
                )
                error.requested_model = str(settings.v1m_model)
                error.http_status = status_code
                error.attempt_count = attempt_count
                error.retry_count = retry_count
                error.retry_delays_ms = retry_delays_ms
                error.circuit_state = circuit_state
                raise error from exc
            if not isinstance(decoded, dict):
                write_v1m_call_log(
                    settings,
                    url=url,
                    request_payload=payload,
                    response_payload={
                        "status_code": status_code,
                        "headers": _safe_v1m_response_headers(response),
                        "body": None,
                    },
                    error_payload={
                        "kind": "malformed_response",
                        "message": "V1M decision endpoint returned an unexpected payload",
                        "attempt": attempt_count,
                    },
                    attempt=attempt_count,
                )
                circuit_state = _circuit_record_failure(
                    url,
                    breaker_threshold,
                    breaker_cooldown,
                )
                error = V1MDecisionError(
                    "V1M decision endpoint returned an unexpected payload",
                    status_code=status_code,
                    kind="malformed_response",
                )
                error.requested_model = str(settings.v1m_model)
                error.http_status = status_code
                error.attempt_count = attempt_count
                error.retry_count = retry_count
                error.retry_delays_ms = retry_delays_ms
                error.circuit_state = circuit_state
                raise error
            response_model = str(decoded.get("model") or "")
            model_changed = bool(response_model and response_model != str(settings.v1m_model))
            model_alias = _is_model_alias(str(settings.v1m_model))
            model_gate = "ok"
            if not response_model:
                model_gate = "missing_response_model"
            elif model_alias:
                model_gate = "requested_alias"
            elif model_changed:
                model_gate = "response_mismatch"
            decoded["_v1m_transport"] = {
                "attempt_count": attempt_count,
                "retry_count": retry_count,
                "retry_delays_ms": retry_delays_ms,
                "http_status_history": http_status_history,
                "http_status": status_code,
                "requested_model": str(settings.v1m_model),
                "response_model": response_model,
                "model_changed": model_changed,
                "model_alias": model_alias,
                "model_gate": model_gate,
                "circuit_state": "closed",
            }
            write_v1m_call_log(
                settings,
                url=url,
                request_payload=payload,
                response_payload={
                    "status_code": status_code,
                    "headers": _safe_v1m_response_headers(response),
                    "body": decoded,
                    "attempt": attempt_count,
                },
                attempt=attempt_count,
            )
            _circuit_record_success(url)
            return decoded

        should_retry_header = _x_should_retry(response)
        quota_or_spend = _is_quota_or_spend_error(status_code, response)
        retryable_status = status_code in {429, 503, 529}
        should_retry = (
            retryable_status
            and should_retry_header is not False
            and not quota_or_spend
            and attempt < max_attempts - 1
        )
        write_v1m_call_log(
            settings,
            url=url,
            request_payload=payload,
            response_payload={
                "status_code": status_code,
                "headers": _safe_v1m_response_headers(response),
                "body": getattr(response, "text", ""),
                "attempt": attempt_count,
                "retryable": should_retry,
                "quota_or_spend": quota_or_spend,
            },
            error_payload=None if should_retry else {
                "kind": "http_error",
                "message": f"V1M decision endpoint returned HTTP {status_code}",
                "attempt": attempt_count,
            },
            attempt=attempt_count,
        )
        if should_retry:
            retry_count += 1
            retry_after = _parse_retry_after(response)
            delay = retry_after if retry_after > 0 else _full_jitter_delay(
                attempt,
                retry_base,
                retry_max,
            )
            delay = min(retry_max, delay)
            retry_delays_ms.append(round(delay * 1000.0, 3))
            time.sleep(delay)
            continue

        circuit_state = _circuit_record_failure(
            url,
            breaker_threshold,
            breaker_cooldown,
        )
        error = V1MDecisionError(
            f"V1M decision endpoint returned HTTP {status_code}",
            status_code=status_code,
            kind="http_error",
        )
        error.retry_count = retry_count
        error.attempt_count = attempt_count
        error.http_status = status_code
        error.retry_delays_ms = retry_delays_ms
        error.requested_model = str(settings.v1m_model)
        error.circuit_state = circuit_state
        raise error

    raise V1MDecisionError(
        "V1M decision endpoint failed after retry",
        kind="http_error",
    )


def parse_v1m_choice(
    payload: Dict[str, Any],
    *,
    question_name: str = "next_action",
    requested_model: Optional[str] = None,
    pin_required: bool = False,
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

    transport = payload.get("_v1m_transport")
    if not isinstance(transport, dict):
        transport = {}
    response_model = str(payload.get("model") or transport.get("response_model") or "")
    requested = str(requested_model or transport.get("requested_model") or "")
    model_changed = bool(requested and response_model and requested != response_model)
    model_alias = _is_model_alias(requested)
    model_gate = "ok"
    if pin_required:
        if not response_model:
            model_gate = "missing_response_model"
        elif model_alias:
            model_gate = "requested_alias"
        elif model_changed:
            model_gate = "response_mismatch"

    return {
        "choice": choice.strip(),
        "confidence": confidence,
        "probabilities": probabilities,
        "requested_model": requested,
        "response_model": response_model,
        "model_changed": model_changed,
        "model_alias": model_alias,
        "model_pin_required": bool(pin_required),
        "model_gate": model_gate,
        "transport": transport,
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


def get_v1m_action_risk(
    choice: str,
    candidates: Iterable[Dict[str, str]],
) -> str:
    """Classify a bounded V1M choice as read, write, or destructive."""
    selected = next(
        (candidate for candidate in candidates if candidate.get("id") == choice),
        None,
    )
    if selected is None:
        return "write"

    action = str(selected.get("action", "")).strip().lower()
    description = str(selected.get("description", "")).lower()
    destructive_markers = (
        "delete",
        "remove",
        "purge",
        "destroy",
        "logout",
        "sign out",
        "pay",
        "checkout",
        "purchase",
        "transfer",
        "confirm",
        "submit",
    )
    write_markers = (
        "type",
        "submit",
        "save",
        "create",
        "register",
        "update",
        "edit",
        "upload",
        "send",
        "post",
        "approve",
        "accept",
    )
    if action in {"submit_form"} or any(marker in description for marker in destructive_markers):
        return "destructive"
    if action in {"type"} or any(marker in description for marker in write_markers):
        return "write"
    return "read"


def get_v1m_probability_threshold(
    settings: Settings,
    risk: str,
) -> float:
    """Return the starting probability gate for a risk class."""
    if risk == "destructive":
        return min(1.0, max(0.0, float(getattr(settings, "v1m_destructive_probability_threshold", 0.80))))
    if risk == "write":
        return min(1.0, max(0.0, float(getattr(settings, "v1m_write_probability_threshold", 0.60))))
    return min(1.0, max(0.0, float(getattr(settings, "v1m_read_probability_threshold", 0.50))))


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
        "mode": "rerank" if getattr(settings, "v1m_rerank_enabled", False) else "shadow",
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
        "action_risk": None,
        "probability_threshold": None,
        "chosen_probability": None,
        "primary_probability": None,
        "probability_margin": float(getattr(settings, "v1m_probability_margin", 0.15)),
        "probability_advantage": None,
        "model_requested": str(getattr(settings, "v1m_model", "")),
        "model_response": "",
        "model_changed": False,
        "model_alias": _is_model_alias(str(getattr(settings, "v1m_model", ""))),
        "model_gate": "",
        "transport": {},
        "permutation_check": None,
        "loop_context": bool(loop_context),
        "repeat_count": int(repeat_count),
        "baseline_plan": None,
        "executed_plan": None,
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
        parsed = parse_v1m_choice(
            payload,
            requested_model=str(settings.v1m_model),
            pin_required=bool(getattr(settings, "v1m_model_pin_required", True)),
        )
    except asyncio.TimeoutError:
        telemetry["latency_ms"] = round(
            (time.perf_counter() - started) * 1000.0,
            3,
        )
        telemetry["outcome"] = "timeout"
        telemetry["status"] = "fallback"
        telemetry["transport"] = {
            "kind": "timeout",
            "attempt_count": 0,
            "retry_count": 0,
            "http_status": None,
            "circuit_state": "unknown",
        }
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
        telemetry["transport"] = {
            "kind": exc.kind,
            "attempt_count": exc.attempt_count,
            "retry_count": exc.retry_count,
            "retry_delays_ms": exc.retry_delays_ms,
            "http_status": exc.http_status,
            "requested_model": exc.requested_model,
            "response_model": exc.response_model,
            "model_changed": exc.model_changed,
            "model_gate": exc.model_gate,
            "circuit_state": exc.circuit_state,
        }
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
        telemetry["transport"] = {
            "kind": "unexpected_error",
            "attempt_count": 0,
            "retry_count": 0,
            "http_status": None,
            "circuit_state": "unknown",
        }
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
    telemetry["model_requested"] = parsed["requested_model"]
    telemetry["model_response"] = parsed["response_model"]
    telemetry["model_changed"] = parsed["model_changed"]
    telemetry["model_alias"] = parsed["model_alias"]
    telemetry["model_gate"] = parsed["model_gate"]
    telemetry["transport"] = parsed["transport"]

    if (
        getattr(settings, "v1m_rerank_enabled", False)
        and getattr(settings, "v1m_model_pin_required", True)
        and parsed["model_gate"] != "ok"
    ):
        telemetry["outcome"] = "model_drift"
        telemetry["status"] = "fallback"
        telemetry["rerank_blocked_reason"] = "model_drift"
        print(
            f"   └─ 🧭 V1M rerank blocked: model gate={parsed['model_gate']} "
            f"requested={parsed['requested_model'] or 'unknown'} "
            f"response={parsed['response_model'] or 'unknown'}"
        )
        return telemetry

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
    rerank_enabled = bool(getattr(settings, "v1m_rerank_enabled", False))
    if (
        parsed["confidence"] < float(settings.v1m_confidence_threshold)
        and not rerank_enabled
    ):
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
    if (
        parsed["confidence"] < float(settings.v1m_confidence_threshold)
        and rerank_enabled
    ):
        telemetry["raw_confidence_below_threshold"] = True
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

    action_risk = get_v1m_action_risk(parsed["choice"], candidates)
    probability_threshold = get_v1m_probability_threshold(settings, action_risk)
    telemetry["action_risk"] = action_risk
    telemetry["probability_threshold"] = probability_threshold
    telemetry["chosen_probability"] = chosen_probability
    telemetry["primary_probability"] = primary_probability
    telemetry["probability_advantage"] = advantage
    if float(chosen_probability) < probability_threshold:
        telemetry["outcome"] = "weak_rerank"
        telemetry["rerank_blocked_reason"] = "risk_probability_threshold"
        print(
            f"   └─ 🧭 V1M shadow choice={parsed['choice']} "
            f"risk={action_risk} probability={chosen_probability:.3f} "
            f"threshold={probability_threshold:.3f} outcome=weak_rerank "
            f"latency={telemetry['latency_ms']}ms"
        )
        return telemetry

    if (
        rerank_enabled
        and loop_context
        and bool(getattr(settings, "v1m_permutation_check_enabled", False))
    ):
        reversed_items = list(reversed(list(criteria.items())))
        reversed_questions = {
            "next_action": {
                "type": "choice",
                "instructions": "Choose the one bounded next action, consistently across candidate order.",
                "criteria": dict(reversed_items),
            }
        }
        try:
            reversed_payload = await asyncio.wait_for(
                asyncio.to_thread(
                    decide_v1m,
                    settings,
                    state=state_payload,
                    questions=reversed_questions,
                ),
                timeout=max(0.1, float(settings.v1m_timeout_seconds) + 0.5),
            )
            reversed_parsed = parse_v1m_choice(
                reversed_payload,
                requested_model=str(settings.v1m_model),
                pin_required=bool(getattr(settings, "v1m_model_pin_required", True)),
            )
            if reversed_parsed["model_gate"] != "ok":
                telemetry["permutation_check"] = {
                    "checked": False,
                    "original_choice": parsed["choice"],
                    "reversed_choice": "",
                    "flip": True,
                    "error": "permutation_model_drift",
                }
                telemetry["outcome"] = "weak_rerank"
                telemetry["rerank_blocked_reason"] = "permutation_model_drift"
                print("   └─ 🧭 V1M permutation model drift; rerank blocked")
                return telemetry
            reversed_choice = str(reversed_parsed["choice"] or "")
            permutation_flip = reversed_choice != parsed["choice"]
            telemetry["permutation_check"] = {
                "checked": True,
                "original_choice": parsed["choice"],
                "reversed_choice": reversed_choice,
                "flip": permutation_flip,
            }
            if permutation_flip:
                telemetry["outcome"] = "weak_rerank"
                telemetry["rerank_blocked_reason"] = "candidate_order_flip"
                print(
                    f"   └─ 🧭 V1M candidate-order flip "
                    f"original={parsed['choice']} reversed={reversed_choice} "
                    f"outcome=weak_rerank"
                )
                return telemetry
        except Exception:
            telemetry["permutation_check"] = {
                "checked": False,
                "original_choice": parsed["choice"],
                "reversed_choice": "",
                "flip": True,
                "error": "permutation_check_failed",
            }
            telemetry["outcome"] = "weak_rerank"
            telemetry["rerank_blocked_reason"] = "permutation_check_failed"
            print("   └─ 🧭 V1M permutation check failed; rerank blocked")
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
