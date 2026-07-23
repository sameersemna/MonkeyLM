"""Worker helpers - retry logic, step allocation, and utility functions."""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, _local_service_log


CURRENT_GLOBAL_STEP: int = 0


def build_worker_user_data_dir(settings: Settings, worker_id: int) -> str:
    worker_label = f"worker-{worker_id:02d}"
    worker_data_dir = os.path.join(settings.run_user_data_dir, worker_label)
    os.makedirs(worker_data_dir, exist_ok=True)
    return worker_data_dir


async def with_retry_backoff(
    operation_name: str,
    operation,
    *,
    retries: int = 2,
    initial_delay_seconds: float = 0.75,
) -> Any:
    attempts = max(1, retries + 1)
    delay = max(0.1, float(initial_delay_seconds))
    last_exc: Optional[Exception] = None

    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            jitter = random.uniform(0.0, 0.2)
            sleep_for = delay + jitter
            _local_service_log(f"{operation_name} failed on attempt {attempt}/{attempts}; retrying in {sleep_for:.2f}s: {exc}")
            await asyncio.sleep(sleep_for)
            delay *= 2.0

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{operation_name} failed without exception details")


def allocate_worker_steps(total_steps: int, worker_count: int, per_worker_cap: int) -> List[int]:
    worker_count = max(1, worker_count)
    remaining = max(0, total_steps)
    cap = max(1, per_worker_cap)
    allocations = [0 for _ in range(worker_count)]

    while remaining > 0:
        progressed = False
        for idx in range(worker_count):
            if remaining <= 0:
                break
            if allocations[idx] >= cap:
                continue
            allocations[idx] += 1
            remaining -= 1
            progressed = True
        if not progressed:
            break

    if remaining > 0:
        _local_service_log(f"Step allocation exhausted per-worker caps. Unallocated steps={remaining}, workers={worker_count}, cap={cap}.", output_dir="")
    return allocations
