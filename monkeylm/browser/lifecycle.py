"""Browser lifecycle - launch, page readiness, navigation, dialog handling, URL validation."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import random
import re
import urllib.parse
from typing import Any, Dict, List, Literal, Optional, Tuple

from playwright.async_api import Dialog, Page

from monkeylm.config import _local_service_log


async def _check_and_handle_dialogs(page: Page) -> None:
    try:
        await asyncio.sleep(0.1)
    except Exception:
        pass


def _looks_like_ip_literal(host: str) -> bool:
    if not host:
        return False
    if host.startswith("["):
        return True
    if ":" in host:
        return True
    if re.fullmatch(r"[0-9.]+", host):
        return True
    return False


def _validate_navigation_url(url: str) -> str:
    if not isinstance(url, str):
        raise TypeError(f"Navigation URL must be a string, got {type(url).__name__}")
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("Navigation URL is empty")

    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Navigation to '{parsed.scheme}' URI scheme is not allowed"
        )

    host = parsed.hostname
    if host:
        if _looks_like_ip_literal(host):
            try:
                addr = ipaddress.ip_address(host)
            except ValueError:
                raise ValueError(f"Navigation to malformed IP '{host}' blocked (SSRF protection)")
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
                raise ValueError(f"Navigation to private/reserved IP ({host}) is blocked")

    return cleaned


async def resilient_page_goto(
    page: Page,
    url: str,
    *,
    timeout: int = 45000,
    wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] | None = "domcontentloaded",
    phase: str = "navigation",
    max_retries: int = 3,
) -> Optional[Any]:
    last_error = None

    try:
        url = _validate_navigation_url(url)
    except (TypeError, ValueError) as exc:
        print(f"❌ {phase}: Navigation URL rejected: {exc}")
        return None

    for attempt in range(max_retries):
        try:
            response = await page.goto(url, wait_until=wait_until, timeout=timeout)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await _check_and_handle_dialogs(page)

            if response is None:
                print(f"⚠️ {phase}: No response received during navigation to {url}")
                continue

            status = response.status
            if 200 <= status < 400:
                return response
            elif status == 401 or status == 403:
                print(f"⚠️ {phase}: Authentication/authorization error ({status}) for {url}")
                return response
            else:
                last_error = f"HTTP {status}"
                if attempt < max_retries - 1:
                    print(f"⚠️ {phase}: HTTP {status}, retrying... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(random.uniform(1, 3))
                    continue

        except Exception as exc:
            error_str = str(exc).lower()

            if "aborted" in error_str or "err_aborted" in error_str or "net::err_aborted" in error_str:
                last_error = "net::ERR_ABORTED"
                print(f"⚠️ {phase}: Navigation aborted ({error_str}), retrying... (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(random.uniform(0.5, 1.5))
                continue

            if "timeout" in error_str or "timed out" in error_str:
                last_error = "navigation_timeout"
                print(f"⚠️ {phase}: Navigation timed out, retrying... (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(random.uniform(1, 2))
                continue

            if "execution context" in error_str or "closed" in error_str or "crash" in error_str:
                last_error = f"context_error: {error_str}"
                print(f"⚠️ {phase}: Browser context issue ({error_str}), retrying... (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(random.uniform(1, 2))
                continue

            last_error = error_str
            if attempt < max_retries - 1:
                print(f"⚠️ {phase}: Navigation error ({error_str}), retrying... (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(random.uniform(1, 2))
                continue

    print(f"❌ {phase}: All {max_retries} navigation attempts failed for {url}. Last error: {last_error}")
    return None


async def wait_for_page_ready(page: Page, phase: str, strict: bool = False) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
        return
    except Exception:
        pass

    try:
        await page.wait_for_load_state("load", timeout=12000)
        return
    except Exception as exc:
        msg = f"⚠️ Readiness fallback failed during {phase}: {exc}"
        if strict:
            raise RuntimeError(msg) from exc
        print(msg)


async def launch_context_with_fallback(
    playwright_instance: Any,
    *,
    settings: Any,
    user_data_dir: str,
    worker_label: str,
) -> Tuple[Any, Dict[str, Any]]:
    window_size = str(settings.browser_window_size)
    if not re.match(r"^\d+x\d+$", window_size):
        window_size = "1280x720"
    base_args = [f"--window-size={window_size}", "--disable-blink-features=AutomationControlled"]
    sandbox_args = list(base_args)
    no_sandbox_args = base_args + ["--no-sandbox", "--disable-setuid-sandbox"]

    try:
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=settings.headless,
            args=sandbox_args,
            no_viewport=settings.no_viewport,
        )
        launch_info = {
            "worker": worker_label,
            "mode": "sandbox",
            "args": sandbox_args,
            "error": None,
            "window_size": settings.browser_window_size,
            "no_viewport": settings.no_viewport,
            "headless": settings.headless,
            "user_data_dir": user_data_dir,
        }
        print(f"🛡️ Browser launch mode [{worker_label}]: sandbox")
        return context, launch_info
    except Exception as sandbox_exc:
        if settings.strict_sandbox or not settings.allow_no_sandbox_fallback:
            mode = "sandbox-required-failed" if settings.strict_sandbox else "sandbox-failed-no-fallback"
            launch_info = {
                "worker": worker_label,
                "mode": mode,
                "args": sandbox_args,
                "error": str(sandbox_exc),
                "window_size": settings.browser_window_size,
                "no_viewport": settings.no_viewport,
                "headless": settings.headless,
                "strict_sandbox": settings.strict_sandbox,
                "allow_no_sandbox_fallback": settings.allow_no_sandbox_fallback,
                "user_data_dir": user_data_dir,
            }
            _local_service_log(
                f"Browser launch failed [{worker_label}] with mode={mode}: {sandbox_exc}",
                settings.output_dir,
            )
            policy_hint = (
                "STRICT_SANDBOX is enabled" if settings.strict_sandbox else "ALLOW_NO_SANDBOX_FALLBACK is disabled"
            )
            raise RuntimeError(
                "Sandbox launch failed and no-sandbox fallback is blocked "
                f"({policy_hint}). Set ALLOW_NO_SANDBOX_FALLBACK=true if you want to permit fallback."
            ) from sandbox_exc

        print(f"⚠️ Sandbox launch failed, retrying with no-sandbox: {sandbox_exc}")
        context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=settings.headless,
            args=no_sandbox_args,
            no_viewport=settings.no_viewport,
        )
        launch_info = {
            "worker": worker_label,
            "mode": "no-sandbox-fallback",
            "args": no_sandbox_args,
            "error": str(sandbox_exc),
            "window_size": settings.browser_window_size,
            "no_viewport": settings.no_viewport,
            "headless": settings.headless,
            "strict_sandbox": settings.strict_sandbox,
            "allow_no_sandbox_fallback": settings.allow_no_sandbox_fallback,
            "user_data_dir": user_data_dir,
        }
        print(f"🔓 Browser launch mode [{worker_label}]: no-sandbox-fallback")
        return context, launch_info


async def handle_dialog(dialog: Dialog) -> None:
    from monkeylm.core.monitor import sanitize_for_storage
    safe_msg = sanitize_for_storage(str(dialog.message), max_len=512)
    print(f"   -> 🚨 Native Dialog Detected: {safe_msg}")
    if random.random() > 0.5:
        await dialog.accept()
        print("   -> Accepted dialog")
    else:
        await dialog.dismiss()
        print("   -> Dismissed dialog")
