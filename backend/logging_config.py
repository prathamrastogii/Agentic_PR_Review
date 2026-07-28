import logging
import sys

from backend.config import LOG_LEVEL

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"

_configured = False


def setup_logging(level: str | None = None) -> None:
    """Attach a stdout handler to the `backend` logger tree.

    Scoped to `backend` rather than the root logger so uvicorn's own handlers
    keep working and records are not emitted twice.
    """
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    app_logger = logging.getLogger("backend")
    app_logger.setLevel(level or LOG_LEVEL)
    app_logger.addHandler(handler)
    app_logger.propagate = False

    _configured = True
