import logging
from pathlib import Path


def setup_logging() -> None:
    logging.basicConfig(
        filename=Path.cwd() / "comfy-helper.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
