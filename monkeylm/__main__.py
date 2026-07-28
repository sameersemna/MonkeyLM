"""Enable ``python -m monkeylm`` execution."""

import asyncio

from monkeylm.config import load_settings, parse_cli_args, validate_runtime_configuration
from monkeylm.core import main


def entry() -> None:
    cli_args = parse_cli_args()
    if getattr(cli_args, "inspect_runtime", False) or getattr(cli_args, "inspect_runtime_json", False):
        from monkeylm.config import inspect_optional_runtime_dependencies

        report = inspect_optional_runtime_dependencies()
        if getattr(cli_args, "inspect_runtime_json", False):
            import json

            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("Runtime dependency inspection:")
            for name, payload in sorted(report.items()):
                print(f"- {name}: {payload['status']} -> {payload['detail']}")
        return

    settings = load_settings(cli_args)
    validate_runtime_configuration(settings)
    asyncio.run(main(settings))


if __name__ == "__main__":
    entry()
