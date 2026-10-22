"""Browser error and console anomaly interception."""

from __future__ import annotations

from typing import Any, Dict, List

from playwright.async_api import Page

from .defects import DefectTracker, sanitize_for_storage


def _dedupe_batch(anomalies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for anomaly in anomalies:
        message = str(anomaly.get("message") or anomaly.get("failure_reason") or "")
        key = (
            anomaly.get("type"),
            anomaly.get("action"),
            anomaly.get("url"),
            message[:200],
            anomaly.get("status"),
            anomaly.get("method"),
            anomaly.get("resource_type"),
        )
        if key not in deduped:
            merged = dict(anomaly)
            merged["occurrences"] = 1
            merged["occurrence_count"] = 1
            step = anomaly.get("step")
            merged["occurrence_steps"] = [step] if step is not None else []
            deduped[key] = merged
            order.append(key)
            continue
        existing = deduped[key]
        existing["occurrences"] = int(existing.get("occurrences", 1)) + 1
        existing["occurrence_count"] = existing["occurrences"]
        step = anomaly.get("step")
        if step is not None:
            existing["last_seen_step"] = step
            existing["occurrence_steps"] = sorted(
                set(existing.get("occurrence_steps", []) + [step])
            )
    result: List[Dict[str, Any]] = []
    for key in order:
        item = deduped[key]
        steps = item.get("occurrence_steps") or []
        if steps:
            item["step_range"] = [steps[0], steps[-1]]
        result.append(item)
    return result


def _network_detail(anomaly: Dict[str, Any]) -> str:
    """Render the actionable part of a network anomaly.

    Network anomalies have no human-readable ``message``; the operator needs
    the URL, the HTTP status, and the method. Rendering them into ``message``
    keeps one summary format for every anomaly type instead of printing an
    empty ``message=`` for exactly the anomalies that matter most.
    """
    url = str(anomaly.get("url") or "")
    status = anomaly.get("status")
    method = str(anomaly.get("method") or "")
    resource_type = str(anomaly.get("resource_type") or "")
    if not url and status is None:
        return ""
    bits = []
    if status is not None:
        bits.append(f"status={status}")
    if method:
        bits.append(f"method={method}")
    if resource_type:
        bits.append(f"type={resource_type}")
    suffix = " ".join(bits)
    return f"{suffix} url={url}" if suffix else f"url={url}"


def format_anomaly_summary(anomalies: List[Dict[str, Any]], limit: int = 5) -> str:
    parts: List[str] = []
    for anomaly in anomalies[:limit]:
        # Prefer the structured network rendering over a terse failure_reason:
        # it carries method, status, and resource type, which is what an
        # operator needs to act on a 5xx.
        message = str(anomaly.get("message") or "")
        if not message:
            message = _network_detail(anomaly)
        if not message:
            message = str(anomaly.get("failure_reason") or "")
        count = int(anomaly.get("occurrences", anomaly.get("occurrence_count", 1)) or 1)
        count_part = f" occurrences={count}" if count > 1 else ""
        parts.append(
            f"step={anomaly.get('step', '?')} "
            f"action={anomaly.get('action', '?')} "
            f"type={anomaly.get('type', '?')}"
            f"{count_part} "
            f"message={message[:200]}"
        )
    if len(anomalies) > limit:
        parts.append(f"+{len(anomalies) - limit} more")
    return "; ".join(parts)


class BrowserAnomalySensor:
    """Intercepts hidden browser context anomalies and maps them to monkey actions."""

    def __init__(self, defects: DefectTracker) -> None:
        self.defects = defects
        self._anomalies: List[Dict[str, Any]] = []
        self._current_step: int = -1
        self._current_action: str = ""
        self._installed_pages: set[int] = set()
        self._network_installed: bool = False

    async def install(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._installed_pages:
            return
        self._installed_pages.add(page_id)

        def _on_page_error(error: Any) -> None:
            raw = str(getattr(error, "message", error))
            msg = sanitize_for_storage(raw, max_len=1000)
            self._anomalies.append({
                "step": self._current_step,
                "action": self._current_action,
                "type": "unhandled-page-error",
                "severity": "error",
                "message": msg,
                "url": sanitize_for_storage(page.url, max_len=2048),
            })

        def _on_console(msg: Any) -> None:
            try:
                raw = getattr(msg, "text", "") or ""
                text = sanitize_for_storage(raw, max_len=1000)
                lower_text = text.lower()
                if "unhandled promise" in lower_text:
                    self._anomalies.append({
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": "unhandled-promise-rejection",
                        "severity": "error",
                        "message": text[:1000],
                        "url": page.url,
                    })
                elif ("content security policy" in lower_text or "csp" in lower_text) and "blocked" in lower_text:
                    directive = ""
                    _csp_resource = ""
                    for part in text.split(";"):
                        if "directive" in part.lower():
                            directive = part.strip().split(":", 1)[-1].strip() if ":" in part else part.strip()
                        if ("script-src" in part or "style-src" in part or "img-src" in part):
                            _csp_resource = part.strip()
                    self._anomalies.append({
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": "csp-violation",
                        "severity": "warning",
                        "message": text[:1000],
                        "blocked_directive": directive or lower_text.split("directive")[-1].strip() if "directive" in lower_text else "",
                        "url": page.url,
                    })
                elif (("uncaught" in lower_text or "error:" in lower_text) and msg.type in ("error", "warning", "assert")):
                    already = any(
                        a.get("step") == self._current_step and a.get("type") == "unhandled-page-error" and text[:100] in a.get("message", "")
                        for a in self._anomalies[-5:]
                    )
                    if not already:
                        self._anomalies.append({
                            "step": self._current_step,
                            "action": self._current_action,
                            "type": "console-error",
                            "severity": "warning",
                            "message": text,
                            "url": sanitize_for_storage(page.url, max_len=2048),
                            "console_type": str(getattr(msg, "type", "")),
                        })
            except Exception:
                pass

        page.on("pageerror", _on_page_error)
        page.on("console", _on_console)

    def set_action_context(self, step: int, action_desc: str) -> None:
        self._current_step = step
        self._current_action = action_desc

    async def check_network_failures(self, page: Page) -> None:
        if self._network_installed:
            return
        try:
            def _record(entry: Dict[str, Any]) -> None:
                self._anomalies.append(entry)

            def _on_response(response) -> None:
                try:
                    status = response.status
                    if status < 400:
                        return
                    request = response.request
                    resource_type = request.resource_type
                    url = sanitize_for_storage(request.url, max_len=2048)
                    method = request.method
                    if resource_type not in ("xhr", "fetch") and "/api/" not in url.lower():
                        return
                    # Framework route prefetch (Next.js ``?_rsc=``) answers 5xx
                    # routinely during client navigation and is not an
                    # application defect.  Report it under its own type at
                    # warning severity so a genuine API failure is not buried
                    # under prefetch noise.
                    is_prefetch = "_rsc=" in url.lower()
                    entry = {
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": (
                            f"network-{status // 100}xx-prefetch"
                            if is_prefetch
                            else f"network-{status // 100}xx-fetch"
                        ),
                        "severity": (
                            "warning"
                            if (is_prefetch or status < 500)
                            else "error"
                        ),
                        "url": url,
                        "method": sanitize_for_storage(str(method), max_len=64),
                        "status": status,
                        "resource_type": sanitize_for_storage(str(resource_type), max_len=64),
                        "failure_reason": f"HTTP {status} on {method} {url}",
                    }
                    if is_prefetch:
                        entry["prefetch"] = True
                    _record(entry)
                except Exception:
                    pass

            def _on_request_failed(request) -> None:
                # Transport-level failures (DNS, TLS, connection reset,
                # aborted preflight) never produce a ``response`` event, so
                # without this handler they vanish silently.
                try:
                    resource_type = request.resource_type
                    url = sanitize_for_storage(request.url, max_len=2048)
                    failure = request.failure or ""
                    text = str(failure)
                    if resource_type not in ("xhr", "fetch", "document") and "/api/" not in url.lower():
                        return
                    _record({
                        "step": self._current_step,
                        "action": self._current_action,
                        "type": "network-request-failed",
                        "severity": "error",
                        "url": url,
                        "method": sanitize_for_storage(str(request.method), max_len=64),
                        "status": None,
                        "resource_type": sanitize_for_storage(str(resource_type), max_len=64),
                        "failure_reason": sanitize_for_storage(
                            f"{request.method} {url} failed: {text or 'unknown transport error'}",
                            max_len=512,
                        ),
                    })
                except Exception:
                    pass

            page.on("response", _on_response)
            page.on("requestfailed", _on_request_failed)
        except Exception:
            pass

    async def flush_anomalies(self) -> List[Dict[str, Any]]:
        if not self._anomalies:
            return []
        batch = _dedupe_batch(list(self._anomalies))
        for anomaly in batch:
            self.defects.add("context_anomalies", anomaly)
        self._anomalies.clear()
        return batch
