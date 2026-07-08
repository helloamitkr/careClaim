"""Reading the log file back — Step 16, the /logs viewer page.

The write side (setup.py) and the read side (here) share only a filename
convention. Keeping them apart means the viewer's tail heuristics can change
without anyone re-reading how the sinks are configured.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def log_dir() -> Path:
    return Path(os.environ.get("LOG_DIR", Path(__file__).parents[3] / "logs"))


def newest_log_file() -> Path | None:
    files = sorted(log_dir().glob("carebridge_*.log"))
    return files[-1] if files else None


def parse_log_line(raw: str) -> dict[str, Any] | None:
    """One serialized loguru line → the compact shape the viewer renders.

    Non-JSON lines (partial writes, foreign content) are passed through as plain
    INFO messages rather than dropped — a log viewer that silently hides lines is
    worse than one that shows them ugly."""
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
    """Last N parsed records from a log file. Reads only the file's tail (128 KB
    per requested-100-lines heuristic), not the whole file — log files rotate at
    10 MB and this runs inside a request handler."""
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
