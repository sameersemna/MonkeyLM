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


def format_anomaly_summary(anomalies: List[Dict[str, Any]], limit: int = 5) -> str:
    parts: List[str] = []
    for anomaly in anomalies[:limit]:
        message = str(anomaly.get("message") or anomaly.get("failure_reason") or "")
        count = int(anomaly.get("occurrences", anomaly.get("occurrence_count", 1)) or 1)
        count_part = f" occurrences={count}" if count > 1 else ""
        parts.append(
            f"step={anomaly.get('step', '?')} "
            f"action={anomaly.get('action', '?')} "
            f"type={anomaly.get('type', '?')}"
            f"{count_part} "
            f"message={message[:120]}"
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
            def _on_response(response) -> None:
                try:
                    status = response.status
                    if status >= 400:
                        request = response.request
                        resource_type = request.resource_type
                        url = sanitize_for_storage(request.url, max_len=2048)
                        method = request.method
                        if resource_type in ("xhr", "fetch") or "/api/" in url.lower():
                            self._anomalies.append({
                                "step": self._current_step,
                                "action": self._current_action,
                                "type": f"network-{status // 100}xx-fetch",
                                "severity": "error" if status >= 500 else "warning",
                                "url": url,
                                "method": sanitize_for_storage(str(method), max_len=64),
                                "status": status,
                                "resource_type": sanitize_for_storage(str(resource_type), max_len=64),
                            })
                except Exception:
                    pass
            page.on("response", _on_response)
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
