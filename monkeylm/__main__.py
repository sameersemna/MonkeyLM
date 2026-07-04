"""Enable ``python -m monkeylm`` execution."""

import asyncio

from monkeylm.config import load_settings, parse_cli_args, validate_runtime_configuration
from monkeylm.core import main


def entry() -> None:
    cli_args = parse_cli_args()
    settings = load_settings(cli_args)
    validate_runtime_configuration(settings)
    asyncio.run(main(settings))


if __name__ == "__main__":
    entry()
