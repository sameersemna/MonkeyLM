"""Environment loading and Settings construction."""

from __future__ import annotations

import argparse
import os
import random
from typing import Any, Optional
from urllib.parse import urlparse

from monkeylm.types import Settings

from .defaults import (
    DEFAULT_MAX_STEPS_PER_WORKER,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_WINDOW_SIZE,
    PROJECT_ROOT,
)


def _env_to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _env_to_float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return default
    try:
        return float(value)
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except Exception:
        return default


def _normalize_window_size(raw: str, fallback: str = DEFAULT_WINDOW_SIZE) -> str:
    if not raw:
        return fallback
    candidate = raw.strip().lower().replace("x", ",")
    parts = [p.strip() for p in candidate.split(",")]
    if len(parts) != 2:
        return fallback
    try:
        width = int(parts[0])
        height = int(parts[1])
    except Exception:
        return fallback
    if width < 320 or height < 200:
        return fallback
    return f"{width},{height}"


def _ollama_host_base_url(value: Optional[str]) -> str:
    """Translate a legacy ``OLLAMA_HOST`` value into an OpenAI-compatible base URL."""
    host = (value or "").strip()
    if not host:
        return ""
    if "://" not in host:
        host = f"http://{host}"
    host = host.rstrip("/")
    parsed = urlparse(host)
    if parsed.path in ("", "/"):
        host = f"{host}/v1"
    return host


def load_settings(cli_args: Optional[argparse.Namespace] = None) -> Settings:
    from dotenv import dotenv_values

    env_path = PROJECT_ROOT / ".env"
    env_vars = {}
    if env_path.is_file():
        env_vars = dotenv_values(env_path)

    s = Settings()

    raw_target: str = env_vars.get("TARGET_URL") or os.getenv("TARGET_URL") or s.target_url
    s.target_url = raw_target
    s.target_username = _env_str("TARGET_USERNAME", env_vars.get("TARGET_USERNAME") or s.target_username)
    s.target_password = _env_str("TARGET_PASSWORD", env_vars.get("TARGET_PASSWORD") or s.target_password)
    raw_ollama: str = env_vars.get("OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL") or s.ollama_model
    s.ollama_model = raw_ollama
    ollama_timeout = _env_float("OLLAMA_TIMEOUT_SECONDS", s.ollama_timeout_seconds)
    s.ollama_timeout_seconds = max(1.0, _env_to_float(env_vars.get("OLLAMA_TIMEOUT_SECONDS"), ollama_timeout))
    raw_base_url = (
        os.getenv("OPENAI_BASE_URL")
        or env_vars.get("OPENAI_BASE_URL")
        or _ollama_host_base_url(os.getenv("OLLAMA_HOST") or env_vars.get("OLLAMA_HOST"))
        or s.openai_base_url
    )
    s.openai_base_url = (raw_base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    ollama_api_key = os.getenv("OLLAMA_API_KEY") or env_vars.get("OLLAMA_API_KEY") or ""
    s.openai_api_key = _env_str(
        "OPENAI_API_KEY",
        env_vars.get("OPENAI_API_KEY") or ollama_api_key or s.openai_api_key,
    )

    raw_max_steps = env_vars.get("MAX_STEPS")
    s.max_steps = max(1, int(raw_max_steps) if raw_max_steps is not None else s.max_steps)

    raw_workers = env_vars.get("WORKERS")
    s.workers = max(1, int(raw_workers) if raw_workers is not None else s.workers)

    raw_mspw = env_vars.get("MAX_STEPS_PER_WORKER")
    s.max_steps_per_worker = max(1, int(raw_mspw) if raw_mspw is not None else min(s.max_steps, DEFAULT_MAX_STEPS_PER_WORKER))

    raw_wnr = env_vars.get("WORKER_NAVIGATION_RETRIES")
    s.worker_navigation_retries = max(0, int(raw_wnr) if raw_wnr is not None else s.worker_navigation_retries)
    raw_wqi = env_vars.get("WORKER_QDRANT_INIT_RETRIES")
    s.worker_qdrant_init_retries = max(0, int(raw_wqi) if raw_wqi is not None else s.worker_qdrant_init_retries)
    raw_wbr = env_vars.get("WORKER_BOUNDARY_RECOVERY_RETRIES")
    s.worker_boundary_recovery_retries = max(0, int(raw_wbr) if raw_wbr is not None else s.worker_boundary_recovery_retries)

    raw_rbd = env_vars.get("RETRY_BASE_DELAY_SECONDS")
    s.retry_base_delay_seconds = max(0.1, float(raw_rbd) if raw_rbd is not None else s.retry_base_delay_seconds)
    raw_step_timeout = env_vars.get("STEP_TIMEOUT_SECONDS")
    s.step_timeout_seconds = max(1.0, float(raw_step_timeout) if raw_step_timeout is not None else s.step_timeout_seconds)
    raw_stuck_threshold = env_vars.get("STUCK_STATE_THRESHOLD")
    s.stuck_state_threshold = max(2, int(raw_stuck_threshold) if raw_stuck_threshold is not None else s.stuck_state_threshold)
    ev_headless = env_vars.get("HEADLESS")
    s.headless = _env_bool("HEADLESS", default=_env_to_bool(ev_headless, s.headless))
    raw_bws = env_vars.get("BROWSER_WINDOW_SIZE") or os.getenv("BROWSER_WINDOW_SIZE") or s.browser_window_size
    s.browser_window_size = _normalize_window_size(raw_bws)
    ev_nv = env_vars.get("NO_VIEWPORT")
    s.no_viewport = _env_bool("NO_VIEWPORT", default=_env_to_bool(ev_nv, s.no_viewport))
    s.postgres_dsn = _env_str("POSTGRES_DSN", env_vars.get("POSTGRES_DSN") or s.postgres_dsn)
    s.redis_url = _env_str("REDIS_URL", env_vars.get("REDIS_URL") or s.redis_url)
    s.redis_prefix = _env_str("REDIS_PREFIX", env_vars.get("REDIS_PREFIX") or s.redis_prefix)
    ev_rp = env_vars.get("REDIS_PATH_LOCK_TTL_SECONDS")
    s.redis_path_lock_ttl_seconds = max(1, int(ev_rp) if ev_rp is not None else s.redis_path_lock_ttl_seconds)
    s.golden_baseline_mode = _env_str("GOLDEN_BASELINE_MODE", env_vars.get("GOLDEN_BASELINE_MODE") or s.golden_baseline_mode).lower()
    ev_sp = env_vars.get("STRICT_PERSISTENCE")
    s.strict_persistence = _env_bool("STRICT_PERSISTENCE", default=_env_to_bool(ev_sp, s.strict_persistence))
    ev_rsttl = env_vars.get("REDIS_STATE_TTL_SECONDS")
    s.redis_state_ttl_seconds = max(60, int(ev_rsttl) if ev_rsttl is not None else s.redis_state_ttl_seconds)
    s.qdrant_url = _env_str("QDRANT_URL", env_vars.get("QDRANT_URL") or s.qdrant_url)
    s.qdrant_collection = _env_str("QDRANT_COLLECTION", env_vars.get("QDRANT_COLLECTION") or s.qdrant_collection)
    ev_qvs = env_vars.get("QDRANT_VECTOR_SIZE")
    s.qdrant_vector_size = max(64, int(ev_qvs) if ev_qvs is not None else s.qdrant_vector_size)
    ev_ger = env_vars.get("QDRANT_ENABLE_READS")
    s.qdrant_enable_reads = _env_bool("QDRANT_ENABLE_READS", default=_env_to_bool(ev_ger, s.qdrant_enable_reads))
    ev_gew = env_vars.get("QDRANT_ENABLE_WRITES")
    s.qdrant_enable_writes = _env_bool("QDRANT_ENABLE_WRITES", default=_env_to_bool(ev_gew, s.qdrant_enable_writes))
    s.qdrant_embedding_provider = _env_str("QDRANT_EMBEDDING_PROVIDER", env_vars.get("QDRANT_EMBEDDING_PROVIDER") or s.qdrant_embedding_provider).lower()
    s.qdrant_embedding_model = _env_str("QDRANT_EMBEDDING_MODEL", env_vars.get("QDRANT_EMBEDDING_MODEL") or s.qdrant_embedding_model)
    s.qdrant_embedding_litellm_base_url = _env_str("QDRANT_EMBEDDING_LITELLM_BASE_URL", env_vars.get("QDRANT_EMBEDDING_LITELLM_BASE_URL") or s.qdrant_embedding_litellm_base_url).rstrip("/")
    s.qdrant_embedding_litellm_api_key = _env_str("QDRANT_EMBEDDING_LITELLM_API_KEY", env_vars.get("QDRANT_EMBEDDING_LITELLM_API_KEY") or s.qdrant_embedding_litellm_api_key)
    s.qdrant_admin_action = _env_str("QDRANT_ADMIN_ACTION", env_vars.get("QDRANT_ADMIN_ACTION") or "").lower()
    ev_qre = env_vars.get("QDRANT_RERANK_ENABLED")
    s.qdrant_rerank_enabled = _env_bool("QDRANT_RERANK_ENABLED", default=_env_to_bool(ev_qre, s.qdrant_rerank_enabled))
    s.qdrant_rerank_model = _env_str("QDRANT_RERANK_MODEL", env_vars.get("QDRANT_RERANK_MODEL") or s.qdrant_rerank_model)
    ev_qcl = env_vars.get("QDRANT_CANDIDATE_LIMIT")
    s.qdrant_candidate_limit = max(3, int(ev_qcl) if ev_qcl is not None else s.qdrant_candidate_limit)
    ev_pg = env_vars.get("PDF_GENERATE")
    s.pdf_generate = _env_bool("PDF_GENERATE", default=_env_to_bool(ev_pg, s.pdf_generate))
    s.pdf_vision_model = (
        env_vars.get("PDF_VISION_MODEL")
        or os.getenv("PDF_VISION_MODEL")
        or s.pdf_vision_model
    ).strip()
    s.vision_model = (
        env_vars.get("VISION_MODEL")
        or os.getenv("VISION_MODEL")
        or s.vision_model
    ).strip()
    ev_pvt = env_vars.get("PDF_VISION_TIMEOUT_SECONDS")
    s.pdf_vision_timeout_seconds = max(1.0, float(ev_pvt) if ev_pvt is not None else s.pdf_vision_timeout_seconds)
    s.strict_sandbox = _env_bool("STRICT_SANDBOX", default=_env_to_bool(env_vars.get("STRICT_SANDBOX"), s.strict_sandbox))
    s.allow_no_sandbox_fallback = _env_bool("ALLOW_NO_SANDBOX_FALLBACK", default=_env_to_bool(env_vars.get("ALLOW_NO_SANDBOX_FALLBACK"), s.allow_no_sandbox_fallback))
    ev_cv = env_vars.get("CV_ANALYSIS_ENABLED")
    s.cv_analysis_enabled = _env_bool("CV_ANALYSIS_ENABLED", default=_env_to_bool(ev_cv, s.cv_analysis_enabled))
    ev_ssim = env_vars.get("CV_SSIM_THRESHOLD")
    s.cv_ssim_threshold = min(1.0, max(0.5, float(ev_ssim) if ev_ssim is not None else s.cv_ssim_threshold))
    ev_blank = env_vars.get("CV_BLANK_SCREEN_STDDEV")
    s.cv_blank_screen_stddev = max(0.5, float(ev_blank) if ev_blank is not None else s.cv_blank_screen_stddev)
    ev_phash = env_vars.get("CV_PHASH_DEDUP")
    s.cv_phash_dedup = _env_bool("CV_PHASH_DEDUP", default=_env_to_bool(ev_phash, s.cv_phash_dedup))
    ev_tmpl = env_vars.get("CV_TEMPLATE_VERIFY")
    s.cv_template_verify = _env_bool("CV_TEMPLATE_VERIFY", default=_env_to_bool(ev_tmpl, s.cv_template_verify))
    ev_tscore = env_vars.get("CV_TEMPLATE_MIN_SCORE")
    s.cv_template_min_score = min(1.0, max(0.3, float(ev_tscore) if ev_tscore is not None else s.cv_template_min_score))
    ev_tscale = env_vars.get("CV_TEMPLATE_SCALE")
    s.cv_template_scale = min(1.0, max(0.25, float(ev_tscale) if ev_tscale is not None else s.cv_template_scale))
    ev_tn = env_vars.get("CV_TEMPLATE_EVERY_N_STEPS")
    s.cv_template_every_n_steps = max(1, int(ev_tn) if ev_tn is not None else s.cv_template_every_n_steps)
    ev_ocr = env_vars.get("OCR_ENABLED")
    s.ocr_enabled = _env_bool("OCR_ENABLED", default=_env_to_bool(ev_ocr, s.ocr_enabled))
    ev_oc = env_vars.get("OCR_CONTRAST_ENABLED")
    s.ocr_contrast_enabled = _env_bool("OCR_CONTRAST_ENABLED", default=_env_to_bool(ev_oc, s.ocr_contrast_enabled))
    s.replay_from = _env_str("REPLAY_FROM", env_vars.get("REPLAY_FROM") or s.replay_from)

    if cli_args is not None:
        if getattr(cli_args, "target_url", None):
            s.target_url = cli_args.target_url
        if getattr(cli_args, "target_username", None):
            s.target_username = cli_args.target_username.strip()
        if getattr(cli_args, "target_password", None):
            s.target_password = cli_args.target_password.strip()
        if getattr(cli_args, "ollama_model", None):
            s.ollama_model = cli_args.ollama_model
        if getattr(cli_args, "vision_model", None):
            s.vision_model = cli_args.vision_model.strip()
        if getattr(cli_args, "ollama_timeout_seconds", None) is not None:
            s.ollama_timeout_seconds = max(1.0, float(cli_args.ollama_timeout_seconds))
        if getattr(cli_args, "max_steps", None) is not None:
            s.max_steps = max(1, cli_args.max_steps)
        if getattr(cli_args, "workers", None) is not None:
            s.workers = max(1, int(cli_args.workers))
        if getattr(cli_args, "max_steps_per_worker", None) is not None:
            s.max_steps_per_worker = max(1, int(cli_args.max_steps_per_worker))

    if s.max_steps_per_worker > s.max_steps:
        s.max_steps_per_worker = s.max_steps
        if getattr(cli_args, "worker_navigation_retries", None) is not None:
            s.worker_navigation_retries = max(0, int(cli_args.worker_navigation_retries))
        if getattr(cli_args, "worker_qdrant_init_retries", None) is not None:
            s.worker_qdrant_init_retries = max(0, int(cli_args.worker_qdrant_init_retries))
        if getattr(cli_args, "worker_boundary_recovery_retries", None) is not None:
            s.worker_boundary_recovery_retries = max(0, int(cli_args.worker_boundary_recovery_retries))
        if getattr(cli_args, "retry_base_delay_seconds", None) is not None:
            s.retry_base_delay_seconds = max(0.1, float(cli_args.retry_base_delay_seconds))
        if getattr(cli_args, "step_timeout_seconds", None) is not None:
            s.step_timeout_seconds = max(1.0, float(cli_args.step_timeout_seconds))
        if getattr(cli_args, "stuck_state_threshold", None) is not None:
            s.stuck_state_threshold = max(2, int(cli_args.stuck_state_threshold))
        if getattr(cli_args, "headless", None) is not None:
            s.headless = bool(cli_args.headless)
        if getattr(cli_args, "window_size", None):
            s.browser_window_size = _normalize_window_size(
                cli_args.window_size, fallback=s.browser_window_size
            )
        if getattr(cli_args, "no_viewport", None) is not None:
            s.no_viewport = bool(cli_args.no_viewport)
        if getattr(cli_args, "cv_analysis", None) is not None:
            s.cv_analysis_enabled = bool(cli_args.cv_analysis)
        if getattr(cli_args, "cv_ssim_threshold", None) is not None:
            s.cv_ssim_threshold = min(1.0, max(0.5, float(cli_args.cv_ssim_threshold)))
        if getattr(cli_args, "cv_blank_screen_stddev", None) is not None:
            s.cv_blank_screen_stddev = max(0.5, float(cli_args.cv_blank_screen_stddev))
        if getattr(cli_args, "cv_phash_dedup", None) is not None:
            s.cv_phash_dedup = bool(cli_args.cv_phash_dedup)
        if getattr(cli_args, "cv_template_verify", None) is not None:
            s.cv_template_verify = bool(cli_args.cv_template_verify)
        if getattr(cli_args, "cv_template_min_score", None) is not None:
            s.cv_template_min_score = min(1.0, max(0.3, float(cli_args.cv_template_min_score)))
        if getattr(cli_args, "cv_template_scale", None) is not None:
            s.cv_template_scale = min(1.0, max(0.25, float(cli_args.cv_template_scale)))
        if getattr(cli_args, "cv_template_every_n_steps", None) is not None:
            s.cv_template_every_n_steps = max(1, int(cli_args.cv_template_every_n_steps))
        if getattr(cli_args, "ocr_enabled", None) is not None:
            s.ocr_enabled = bool(cli_args.ocr_enabled)
        if getattr(cli_args, "ocr_contrast_enabled", None) is not None:
            s.ocr_contrast_enabled = bool(cli_args.ocr_contrast_enabled)
        if getattr(cli_args, "replay_from", None):
            s.replay_from = cli_args.replay_from.strip()
        if getattr(cli_args, "seed", None) is not None:
            random.seed(cli_args.seed)
            s.active_seed = str(cli_args.seed)
        if getattr(cli_args, "postgres_dsn", None):
            s.postgres_dsn = cli_args.postgres_dsn.strip()
        if getattr(cli_args, "redis_url", None):
            s.redis_url = cli_args.redis_url.strip()
        if getattr(cli_args, "redis_prefix", None) is not None:
            s.redis_prefix = cli_args.redis_prefix
        if getattr(cli_args, "redis_path_lock_ttl_seconds", None) is not None:
            s.redis_path_lock_ttl_seconds = max(1, int(cli_args.redis_path_lock_ttl_seconds))
        if getattr(cli_args, "golden_baseline_mode", None):
            s.golden_baseline_mode = cli_args.golden_baseline_mode.strip().lower()
        if getattr(cli_args, "strict_persistence", None) is not None:
            s.strict_persistence = bool(cli_args.strict_persistence)
        if getattr(cli_args, "qdrant_url", None):
            s.qdrant_url = cli_args.qdrant_url.strip().rstrip("/")
        if getattr(cli_args, "qdrant_collection", None):
            s.qdrant_collection = cli_args.qdrant_collection.strip()
        if getattr(cli_args, "qdrant_embedding_provider", None):
            s.qdrant_embedding_provider = cli_args.qdrant_embedding_provider.strip().lower()
        if getattr(cli_args, "qdrant_embedding_model", None):
            s.qdrant_embedding_model = cli_args.qdrant_embedding_model.strip()
        if getattr(cli_args, "qdrant_embedding_litellm_base_url", None):
            s.qdrant_embedding_litellm_base_url = cli_args.qdrant_embedding_litellm_base_url.strip().rstrip("/")
        if getattr(cli_args, "qdrant_rerank_model", None):
            s.qdrant_rerank_model = cli_args.qdrant_rerank_model.strip()
        if getattr(cli_args, "qdrant_candidate_limit", None) is not None:
            s.qdrant_candidate_limit = max(3, int(cli_args.qdrant_candidate_limit))
        if getattr(cli_args, "qdrant_disable_reads", False):
            s.qdrant_enable_reads = False
        if getattr(cli_args, "qdrant_disable_writes", False):
            s.qdrant_enable_writes = False
        if getattr(cli_args, "qdrant_enable_rerank", False):
            s.qdrant_rerank_enabled = True
        if getattr(cli_args, "qdrant_disable_rerank", False):
            s.qdrant_rerank_enabled = False
        if getattr(cli_args, "qdrant_read_only", False):
            s.qdrant_enable_reads = True
            s.qdrant_enable_writes = False
        if getattr(cli_args, "qdrant_inspect", False):
            s.qdrant_admin_action = "inspect"
        if getattr(cli_args, "qdrant_clear", False):
            s.qdrant_admin_action = "clear"

    os.makedirs(s.output_dir, exist_ok=True)
    os.makedirs(s.user_data_root, exist_ok=True)
    os.makedirs(s.run_user_data_dir, exist_ok=True)

    return s


__all__ = ["load_settings"]
