"""Step 15 — lightweight logging. Loguru instead of the spec's full
Grafana/Loki/Prometheus stack: one dependency, two sinks, zero infrastructure.

- Console: human-readable, colorized — what you watch during development.
- File: `backend/logs/carebridge_YYYY-MM-DD.log`, one JSON object per line
  (grep/jq-able), rotated at 10 MB, kept 7 days. This is the "Loki" of the
  lightweight version: structured fields ride on `logger.bind(...)`, so
  `jq 'select(.record.extra.case_id == "case-A")'` reconstructs one case's
  whole story across agents, router, gate, and API.

Tuning via env, no code changes:
  LOG_LEVEL     (default INFO)  — e.g. DEBUG to see LLM call timings
  LOG_TO_FILE   (default 1)     — set 0 to disable the file sink (tests, CI)
  LOG_DIR       (default backend/logs)

The deeper observability need (per-case spans, durations, the trace
waterfall UI) was already solved by produced_by/duration_ms on events —
this layer is for the operational log stream around it."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

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

    if os.environ.get("LOG_TO_FILE", "1") != "0":
        log_dir = Path(os.environ.get("LOG_DIR", Path(__file__).parents[2] / "logs"))
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


# --- log-file readers (Step 16 — the /logs viewer page) ---------------------


def log_dir() -> Path:
    return Path(os.environ.get("LOG_DIR", Path(__file__).parents[2] / "logs"))


def newest_log_file() -> Path | None:
    files = sorted(log_dir().glob("carebridge_*.log"))
    return files[-1] if files else None


def parse_log_line(raw: str) -> dict[str, Any] | None:
    """One serialized loguru line → the compact shape the viewer renders.
    Non-JSON lines (partial writes, foreign content) are passed through as
    plain INFO messages rather than dropped — a log viewer that silently
    hides lines is worse than one that shows them ugly."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        record = json.loads(raw)["record"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"time": "", "level": "INFO", "component": "raw", "message": raw, "extra": {}}
    extra = dict(record.get("extra", {}))
    return {
        "time": record["time"]["repr"][:23],  # YYYY-MM-DD HH:MM:SS.mmm
        "level": record["level"]["name"],
        "component": extra.pop("component", "app"),
        "message": record["message"],
        "extra": extra,
    }


def tail_records(path: Path, lines: int) -> list[dict[str, Any]]:
    """Last N parsed records from a log file. Reads only the file's tail
    (128 KB per requested-100-lines heuristic), not the whole file — log
    files rotate at 10 MB and this runs inside a request handler."""
    chunk = max(lines * 1280, 65536)
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - chunk))
        raw_lines = f.read().decode("utf-8", errors="replace").splitlines()
    if size > chunk and raw_lines:
        raw_lines = raw_lines[1:]  # first line is likely cut mid-record
    records = [r for r in (parse_log_line(line) for line in raw_lines) if r is not None]
    return records[-lines:]
