"""Standard-library logging → loguru.

Third-party libraries log through `logging`, not loguru. Left alone they write
straight to stderr: their lines miss the JSON file sink entirely (so the /logs
viewer never sees them), they carry no `component` field, and uvicorn's access
log duplicates the request line the middleware already writes.

This module is `carebridge.logging.intercept` and imports the stdlib `logging`.
That is safe: Python 3 imports are absolute, so `import logging` below resolves
to the standard library, never to the package this file lives in.
"""

from __future__ import annotations

import logging
import os

from loguru import logger

# Chatty at INFO and never interesting unless something is broken: httpx prints
# a line per HTTP call (so once per LLM round-trip), and sqlalchemy.engine can
# echo SQL. Raise LIBRARY_LOG_LEVEL=INFO when debugging a vendor SDK.
NOISY_LIBRARIES = (
    "anthropic",
    "google_genai",
    "httpcore",
    "httpx",
    "sqlalchemy.engine",
    "urllib3",
)

# Loggers whose records we re-home into loguru. Anything else keeps its own
# handlers — we are not in the business of hijacking a library nobody asked about.
INTERCEPTED_PREFIXES = NOISY_LIBRARIES + ("asyncio", "fastapi", "uvicorn")


class InterceptHandler(logging.Handler):
    """One stdlib record → one loguru record, preserving level and call site."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno  # a custom level loguru doesn't know

        # Walk out of the logging module so the reported file:line is the
        # library's call site, not logging/__init__.py.
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        component = record.name.split(".")[0]
        logger.bind(component=component).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def intercept_stdlib_logging(level: str) -> None:
    """Idempotent. Called from configure_logging()."""
    logging.root.handlers = [InterceptHandler()]
    logging.root.setLevel(level)

    library_level = os.environ.get("LIBRARY_LOG_LEVEL", "WARNING").upper()

    # Existing loggers already hold their own handlers (uvicorn installs three
    # before the app is imported). Strip them and let records propagate to root.
    for name in list(logging.root.manager.loggerDict):
        if name.startswith(INTERCEPTED_PREFIXES):
            lib_logger = logging.getLogger(name)
            lib_logger.handlers = []
            lib_logger.propagate = True

    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(library_level)

    # The middleware already logs "{method} {path} → {status} in {ms}ms" with a
    # component and a duration. uvicorn.access says the same thing, unstructured.
    if os.environ.get("UVICORN_ACCESS_LOG", "0") == "0":
        logging.getLogger("uvicorn.access").disabled = True
