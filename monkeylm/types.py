"""Core dataclasses and type definitions for MonkeyLM.

All structured data types live here so that every module can import them
without creating circular dependencies. No runtime logic or I/O belongs in
this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from monkeylm.defect_types import DefectTicket


# ── Testing strategy types ────────────────────────────────────────────────────


@dataclass
class PersonaGoal:
    """A single testing persona with intent and expected reactions."""

    name: str
    description: str
    behaviors: List[str]


@dataclass
class CriticalFlow:
    """A critical user flow to test with persona-driven actions."""

    name: str
    description: str
    steps: List[str]


@dataclass
class TestingStrategy:
    """Application discovery output — LLM-generated testing strategy."""

    app_domain: str
    primary_personas: List[PersonaGoal]
    critical_flows: List[CriticalFlow]
    edge_cases_to_test: List[str]
    security_focus: List[str]
    strategy_summary: str = ""


# ── Runtime configuration ─────────────────────────────────────────────────────


@dataclass
class Settings:
    """Single source of truth for all MonkeyLM runtime configuration."""

    target_url: str = "https://noblequran-85hu2yge.manus.space/"
    target_username: str = ""
    target_password: str = ""
    ollama_model: str = "minimax-m3:cloud"
    ollama_timeout_seconds: float = 15.0
    openai_base_url: str = "http://localhost:11434"
    openai_api_key: str = ""
    vision_model: str = "gemini-3-flash-preview"
    pdf_vision_model: str = "llama3.2-vision"
    pdf_vision_timeout_seconds: float = 30.0

    max_steps: int = 10
    workers: int = 1
    max_steps_per_worker: int = 10
    worker_navigation_retries: int = 2
    worker_qdrant_init_retries: int = 1
    worker_boundary_recovery_retries: int = 1
    retry_base_delay_seconds: float = 0.75
    step_timeout_seconds: float = 30.0
    stuck_state_threshold: int = 6

    headless: bool = True
    browser_window_size: str = "1920,1080"
    no_viewport: bool = True
    strict_sandbox: bool = False
    allow_no_sandbox_fallback: bool = False

    postgres_dsn: str = "postgresql://postgres:postgres@latitude:5432/monkeylm"
    redis_url: str = "redis://:LatitudeRedis1407@latitude:6379/0"
    redis_prefix: str = "monkey:"
    redis_path_lock_ttl_seconds: int = 45
    redis_state_ttl_seconds: int = 86400
    strict_persistence: bool = False
    golden_baseline_mode: str = "preexisting"

    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "monkeylm_semantic_memory"
    qdrant_vector_size: int = 256
    qdrant_enable_reads: bool = True
    qdrant_enable_writes: bool = True
    qdrant_embedding_provider: str = "hash"
    qdrant_embedding_model: str = "nomic-embed-text"
    qdrant_embedding_litellm_base_url: str = "http://localhost:11435"
    qdrant_embedding_litellm_api_key: str = ""
    qdrant_rerank_enabled: bool = False
    qdrant_rerank_model: str = "qwen2.5:3b"
    qdrant_candidate_limit: int = 20
    qdrant_admin_action: str = ""

    pdf_generate: bool = False

    cv_analysis_enabled: bool = True
    cv_ssim_threshold: float = 0.95
    cv_blank_screen_stddev: float = 8.0
    cv_phash_dedup: bool = True
    cv_template_verify: bool = True
    cv_template_min_score: float = 0.75
    cv_template_scale: float = 0.5
    cv_template_every_n_steps: int = 2
    ocr_enabled: bool = False
    ocr_contrast_enabled: bool = False
    replay_from: str = ""

    active_seed: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))

    @property
    def output_dir(self) -> str:
        import os
        override = getattr(self, "_output_dir_override", None)
        if override is not None:
            return override
        return os.path.abspath(f"reports/testrun_{self.timestamp}")

    @property
    def user_data_root(self) -> str:
        import os
        return os.path.abspath("./playwright_user_data")

    @property
    def run_user_data_dir(self) -> str:
        import os
        return os.path.join(self.user_data_root, f"session_{self.timestamp}")


# ── DOM / page state types ────────────────────────────────────────────────────


@dataclass
class FormControlRecord:
    """Structured metadata for a single form control extracted from the DOM."""

    control_id: int
    form_id: Optional[str]
    tag_name: str
    input_type: str
    name_attr: str
    id_attr: str
    placeholder: str
    aria_label: str
    aria_labelledby: str
    required: bool
    disabled: bool
    readonly: bool
    minlength: Optional[int]
    maxlength: Optional[int]
    pattern: str
    min_value: str
    max_value: str
    step: str
    resolved_label: str
    label_confidence: float
    semantic_kind: str
    visible: bool = True
    options: List[str] = field(default_factory=list)


@dataclass
class FormRecord:
    """Structured metadata for a single form and its associated controls."""

    form_id: str
    action: str
    method: str
    control_ids: List[int] = field(default_factory=list)
    submit_candidate_id: Optional[int] = None


@dataclass
class PageSnapshot:
    """Normalized, lightweight representation of page state for diffing and planning."""

    url: str
    title: str
    dom_hash: str
    structure_hash: str
    elements: List[str] = field(default_factory=list)
    layout_anchors: Dict[str, Dict[str, float]] = field(default_factory=dict)
    modal_count: int = 0
    spinner_count: int = 0
    disabled_controls: int = 0
    screenshot_path: str = ""
    timestamp: float = 0.0
    form_controls: List[FormControlRecord] = field(default_factory=list)
    forms: List[FormRecord] = field(default_factory=list)
    is_empty_capture: bool = False


# ── Worker / run result types ─────────────────────────────────────────────────


@dataclass
class WorkerRunResult:
    """Result from a single worker's execution."""

    worker_id: int
    allocated_steps: int
    completed_steps: int
    logs: List[Dict[str, Any]]
    defects: Any
    network_injections: List[Dict[str, Any]]
    launch_info: Dict[str, Any]
    failure_reason: Optional[str] = None
    failure_artifact: Optional[str] = None
    failure_context: Optional[Dict[str, Any]] = None
    discovery_strategy: Optional[Any] = None
    # state_key -> action plan, recorded for --replay-from deterministic reruns
    decision_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)




__all__ = [
    "PersonaGoal",
    "CriticalFlow",
    "TestingStrategy",
    "Settings",
    "FormControlRecord",
    "FormRecord",
    "PageSnapshot",
    "WorkerRunResult",
    "DefectTicket",
]
