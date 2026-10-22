"""Command-line argument parsing."""

from __future__ import annotations

import argparse
from typing import Optional

# Namespace captured by the most recent :func:`parse_cli_args` call.  Runtime
# helpers that build their own ``Settings`` (the DI factories, for example)
# would otherwise call ``load_settings()`` with no arguments and silently
# drop every CLI flag — including ``--headless``/``--headed``, which made the
# DI browser provider ignore the operator's explicit choice.  They can now
# recover the namespace the entry point already parsed.
_LAST_CLI_ARGS: "Optional[argparse.Namespace]" = None


def last_parsed_cli_args() -> Optional[argparse.Namespace]:
    """Return the namespace from the last :func:`parse_cli_args` call."""
    return _LAST_CLI_ARGS


def parse_cli_args() -> argparse.Namespace:
    global _LAST_CLI_ARGS
    parser = argparse.ArgumentParser(description="Advanced monkey testing agent")
    parser.add_argument("--target-url", help="Target URL to test")
    parser.add_argument("--target-username", help="Username to use when the target site requires sign-in")
    parser.add_argument("--target-password", help="Password to use when the target site requires sign-in")
    parser.add_argument(
        "--scenario",
        help="Inline natural-language test scenario; bypasses LLM application discovery",
        default=None,
    )
    parser.add_argument(
        "--scenario-file",
        help="Path to a .json or plain-text scenario file; bypasses LLM application discovery",
        default=None,
    )
    parser.add_argument("--scenario-name", help="Display name for the user-provided scenario")
    parser.add_argument("--ollama-model", help="Ollama model name to use")
    parser.add_argument(
        "--ollama-fallback-models",
        help="Comma-separated fallback planning models to try after the primary model fails",
        default=None,
    )
    parser.add_argument(
        "--vision-model",
        help="Vision model name for screenshot annotation (cloud or local)",
        default=None,
    )
    parser.add_argument(
        "--ollama-timeout-seconds",
        type=float,
        default=None,
        help="Hard timeout for each Ollama inference call in seconds (default: 15)",
    )
    parser.add_argument("--max-steps", type=int, help="Maximum monkey steps to execute")
    parser.add_argument(
        "--workers", type=int, default=None, help="Maximum concurrent worker contexts (default: 1)"
    )
    parser.add_argument(
        "--max-steps-per-worker",
        type=int,
        default=None,
        help="Per-worker maximum step cap; must be <= --max-steps",
    )
    parser.add_argument(
        "--worker-navigation-retries", type=int, default=None, help="Retry count for initial worker navigation"
    )
    parser.add_argument(
        "--worker-qdrant-init-retries", type=int, default=None, help="Retry count for worker Qdrant initialization"
    )
    parser.add_argument(
        "--worker-boundary-recovery-retries",
        type=int,
        default=None,
        help="Retry count for worker boundary-recovery navigation",
    )
    parser.add_argument(
        "--retry-base-delay-seconds",
        type=float,
        default=None,
        help="Base retry delay in seconds for worker backoff",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=float,
        default=None,
        help="Per-step timeout for the worker loop before the run fails fast",
    )
    parser.add_argument(
        "--stuck-state-threshold",
        type=int,
        default=None,
        help="Consecutive repeated-state steps before a stuck-state failure is declared",
    )
    parser.add_argument("--seed", type=int, help="Random seed for deterministic test replay")
    parser.add_argument(
        "--inspect-runtime",
        action="store_true",
        help="Inspect optional runtime dependencies and exit without starting the monkey run",
    )
    parser.add_argument(
        "--inspect-runtime-json",
        action="store_true",
        help="Emit runtime dependency inspection as JSON and exit without starting the monkey run",
    )
    parser.add_argument("--window-size", help="Browser window size as WIDTH,HEIGHT or WIDTHxHEIGHT")
    parser.add_argument("--postgres-dsn", help="PostgreSQL connection string")
    parser.add_argument("--redis-url", help="Redis connection URL")
    parser.add_argument("--redis-prefix", help="Optional prefix for all Redis keys (default: empty)")
    parser.add_argument(
        "--redis-path-lock-ttl-seconds",
        type=int,
        default=None,
        help="TTL in seconds for cross-worker action-path Redis locks (default: 45)",
    )
    parser.add_argument(
        "--golden-baseline-mode",
        choices=["preexisting", "auto_upsert"],
        help="Golden baseline strategy: compare only preexisting goldens or auto-seed when missing",
    )
    parser.add_argument("--qdrant-url", help="Qdrant base URL, for example http://127.0.0.1:6333")
    parser.add_argument("--qdrant-collection", help="Qdrant collection name for semantic memory logs")
    parser.add_argument(
        "--qdrant-embedding-provider", choices=["hash", "ollama", "litellm"], help="Embedding backend for Qdrant vectors"
    )
    parser.add_argument("--qdrant-embedding-model", help="Embedding model name, e.g. nomic-embed-text or ollama/nomic-embed-text for litellm")
    parser.add_argument("--qdrant-embedding-litellm-base-url", help="LiteLLM base URL for the litellm embedding provider")
    parser.add_argument("--qdrant-rerank-model", help="Local Ollama model for reranking retrieved memories")
    parser.add_argument(
        "--qdrant-candidate-limit", type=int, help="Number of candidates to fetch from Qdrant before reranking"
    )

    qdrant_rerank_group = parser.add_mutually_exclusive_group()
    qdrant_rerank_group.add_argument(
        "--qdrant-enable-rerank", action="store_true", help="Enable second-stage reranking of Qdrant memory candidates"
    )
    qdrant_rerank_group.add_argument(
        "--qdrant-disable-rerank", action="store_true", help="Disable reranking and use raw vector ranking only"
    )

    qdrant_rw_group = parser.add_mutually_exclusive_group()
    qdrant_rw_group.add_argument(
        "--qdrant-read-only", action="store_true", help="Enable Qdrant reads but disable writes"
    )
    qdrant_rw_group.add_argument(
        "--qdrant-disable-writes", action="store_true", help="Disable writing step memories to Qdrant"
    )
    parser.add_argument("--qdrant-disable-reads", action="store_true", help="Disable semantic search reads from Qdrant")

    qdrant_admin_group = parser.add_mutually_exclusive_group()
    qdrant_admin_group.add_argument(
        "--qdrant-inspect", action="store_true", help="Inspect Qdrant collection status and exit"
    )
    qdrant_admin_group.add_argument(
        "--qdrant-clear", action="store_true", help="Delete and recreate Qdrant collection, then exit"
    )

    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", dest="headless", action="store_true", help="Run in headless mode")
    headless_group.add_argument("--headed", dest="headless", action="store_false", help="Run with UI")
    parser.set_defaults(headless=None)

    viewport_group = parser.add_mutually_exclusive_group()
    viewport_group.add_argument(
        "--no-viewport",
        dest="no_viewport",
        action="store_true",
        help="Use browser window size directly (Playwright no_viewport=True)",
    )
    viewport_group.add_argument(
        "--use-viewport",
        dest="no_viewport",
        action="store_false",
        help="Enable Playwright viewport emulation",
    )
    parser.set_defaults(no_viewport=None)

    cv_group = parser.add_mutually_exclusive_group()
    cv_group.add_argument(
        "--cv-analysis",
        dest="cv_analysis",
        action="store_true",
        help="Enable OpenCV screenshot analysis (SSIM region localization, blank-screen detection)",
    )
    cv_group.add_argument(
        "--no-cv-analysis",
        dest="cv_analysis",
        action="store_false",
        help="Disable OpenCV screenshot analysis",
    )
    parser.set_defaults(cv_analysis=None)
    parser.add_argument("--cv-ssim-threshold", type=float, help="Per-pixel SSIM cutoff (0.5-1.0) below which pixels count as changed")
    parser.add_argument("--cv-blank-screen-stddev", type=float, help="Grayscale stddev below which a frame is treated as blank")
    phash_group = parser.add_mutually_exclusive_group()
    phash_group.add_argument("--cv-phash-dedup", dest="cv_phash_dedup", action="store_true", help="Enable perceptual-hash visual freeze detection in the stall detector")
    phash_group.add_argument("--no-cv-phash-dedup", dest="cv_phash_dedup", action="store_false", help="Disable perceptual-hash visual freeze detection")
    parser.set_defaults(cv_phash_dedup=None)
    template_group = parser.add_mutually_exclusive_group()
    template_group.add_argument("--cv-template-verify", dest="cv_template_verify", action="store_true", help="Enable template-matching verification of expected page chrome (logo/nav)")
    template_group.add_argument("--no-cv-template-verify", dest="cv_template_verify", action="store_false", help="Disable chrome template verification")
    parser.set_defaults(cv_template_verify=None)
    parser.add_argument("--cv-template-min-score", type=float, help="Minimum multi-scale template match score (0.3-1.0) before chrome is reported missing")
    parser.add_argument("--cv-template-scale", type=float, help="Downscale factor (0.25-1.0) for template matching; 0.5 roughly quarters matching cost")
    parser.add_argument("--cv-template-every-n-steps", type=int, help="Verify chrome every N steps instead of every step")
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument("--ocr", dest="ocr_enabled", action="store_true", help="Enable OCR error-text extraction on failed steps (requires rapidocr-onnxruntime)")
    ocr_group.add_argument("--no-ocr", dest="ocr_enabled", action="store_false", help="Disable OCR error-text extraction")
    parser.set_defaults(ocr_enabled=None)
    parser.add_argument("--ocr-contrast", dest="ocr_contrast_enabled", action="store_true", default=None, help="Enable WCAG text-contrast sampling on OCR text regions")
    parser.add_argument("--replay-from", help="Path to a previous results.json; replay cached LLM decisions for matching page states")

    persistence_group = parser.add_mutually_exclusive_group()
    persistence_group.add_argument(
        "--strict-persistence",
        dest="strict_persistence",
        action="store_true",
        help="Fail startup if PostgreSQL or Redis is unavailable",
    )
    persistence_group.add_argument(
        "--lenient-persistence",
        dest="strict_persistence",
        action="store_false",
        help="Continue run if PostgreSQL or Redis is unavailable",
    )
    parser.set_defaults(strict_persistence=None)

    _LAST_CLI_ARGS = parser.parse_args()
    return _LAST_CLI_ARGS


__all__ = ["parse_cli_args", "last_parsed_cli_args"]
