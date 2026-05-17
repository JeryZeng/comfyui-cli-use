import logging
from pathlib import Path


class RequestLogLevelFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "httpx" and record.getMessage().startswith("HTTP Request:"):
            record.levelno = logging.DEBUG
            record.levelname = logging.getLevelName(logging.DEBUG)
        return logging.getLogger().isEnabledFor(record.levelno)


def setup_logging() -> None:
    logging.basicConfig(
        filename=Path.cwd() / "comfy-helper.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(RequestLogLevelFilter())
