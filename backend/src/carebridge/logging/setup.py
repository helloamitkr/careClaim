"""The two sinks.

- Console: human-readable, colorized — what you watch during development.
- File: `backend/logs/carebridge_YYYY-MM-DD.log`, one JSON object per line
  (grep/jq-able), rotated at 10 MB, kept 7 days. This is the "Loki" of the
  lightweight version: structured fields ride on `logger.bind(...)`, so
  `jq 'select(.record.extra.case_id == "case-A")'` reconstructs one case's whole
  story across agents, router, gate, and API.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger

from carebridge.logging.intercept import intercept_stdlib_logging

_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
    "<cyan>{extra[component]}</cyan> — {message}"
)


def configure_logging() -> None:
    """Idempotent — safe to call on every app startup."""
    logger.remove()
    logger.configure(extra={"component": "app"})  # default for unbound logs
    level = os.environ.get("LOG_LEVEL", "INFO").upper()

    logger.add(sys.stderr, level=level, format=_CONSOLE_FORMAT)
    intercept_stdlib_logging(level)

    if os.environ.get("LOG_TO_FILE", "1") != "0":
        log_dir = Path(os.environ.get("LOG_DIR", Path(__file__).parents[3] / "logs"))
        logger.add(
            log_dir / "carebridge_{time:YYYY-MM-DD}.log",
            level=level,
            serialize=True,  # one JSON object per line
            rotation="10 MB",
            retention="7 days",
            enqueue=True,  # non-blocking writes off the event loop
        )

    logger.bind(component="logging").info(
        "logging configured (level={level}, file={file})",
        level=level,
        file=os.environ.get("LOG_TO_FILE", "1") != "0",
    )
