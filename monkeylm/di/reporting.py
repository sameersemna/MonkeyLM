"""Report generator DI adapter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from monkeylm.config import Settings, load_settings
from monkeylm.interfaces import IReportGenerator


def create_report_generator(
    format: str = "markdown",
    settings: Optional[Settings] = None,
) -> IReportGenerator:
    """Factory function to create report generator instance.

    Args:
        format: Report format ("markdown", "pdf", or "json").
        settings: Runtime configuration. Defaults to load_settings() if None.

    Returns:
        IReportGenerator implementation for specified format.

    Raises:
        ValueError: If format is not supported.
        NotImplementedError: generate() always raises this for a supported
            format - see the Note below.

    Example:
        >>> from monkeylm import create_report_generator
        >>> generator = create_report_generator("markdown")

    Note:
        Currently returns adapter wrapping reporting.py generators.
        Will be replaced with proper generator classes in Phase 2 refactoring.
        generate() raises NotImplementedError for every supported format:
        IReportGenerator's (results, output_dir, settings) -> str contract
        has no correspondence to the real generate_*_report(settings,
        defects, test_logs, browser_launch_info, start_time, end_time) ->
        None functions, which need data this method is never given and
        write files directly rather than returning a path. Call
        monkeylm.reporting functions directly instead. See
        IMPROVEMENT_LOG.md Cycle 5.
    """
    if settings is None:
        settings = load_settings()

    class ReportGeneratorAdapter:
        """Adapter that wraps reporting functions to implement IReportGenerator."""

        def __init__(self, format: str, settings: Settings):
            self._format = format.lower()
            self._settings = settings

        async def generate(
            self,
            results: List[Dict[str, Any]],
            output_dir: str,
            settings: Any,
        ) -> str:
            # IReportGenerator.generate(results, output_dir, settings) -> str has
            # no correspondence to the real generate_*_report(settings, defects,
            # test_logs, browser_launch_info, start_time, end_time) -> None
            # functions: they take a `defects` object and `test_logs` (not a
            # `results` list), also need browser_launch_info/start_time/end_time
            # this method is never given, write files directly using
            # settings.output_dir (ignoring the `output_dir` param entirely), and
            # return None, not a path string. See IMPROVEMENT_LOG.md Cycle 5.
            if self._format not in {"markdown", "pdf", "json"}:
                raise ValueError(f"Unsupported format: {self._format}")
            raise NotImplementedError(
                f"ReportGeneratorAdapter.generate({self._format}): IReportGenerator's "
                "(results, output_dir, settings) -> str contract does not match the real "
                f"generate_{self._format}_report(settings, defects, test_logs, "
                "browser_launch_info, start_time, end_time) -> None signature. Call "
                "monkeylm.reporting functions directly with the run's actual defects/"
                "test_logs/timing data instead."
            )

    return ReportGeneratorAdapter(format, settings)
