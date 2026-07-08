"""Step 15 — lightweight logging. Loguru instead of the spec's full
Grafana/Loki/Prometheus stack: one dependency, two sinks, zero infrastructure.

    logging/
      setup.py       the console + rotating JSON file sinks
      intercept.py   stdlib logging (uvicorn, httpx, SDKs) re-homed into loguru
      reader.py      reading the log file back, for the /logs viewer page

Tuning via env, no code changes:
  LOG_LEVEL           (default INFO)     — e.g. DEBUG to see LLM call timings
  LOG_TO_FILE         (default 1)        — set 0 to disable the file sink (tests, CI)
  LOG_DIR             (default backend/logs)
  LIBRARY_LOG_LEVEL   (default WARNING)  — floor for httpx, vendor SDKs, sqlalchemy
  UVICORN_ACCESS_LOG  (default 0)        — 1 to re-enable uvicorn's own access log

The deeper observability need (per-case spans, durations, the trace waterfall UI)
was already solved by produced_by/duration_ms on events — this layer is for the
operational log stream around it.

Named `carebridge.logging`, which shadows nothing: Python 3 imports are absolute,
so a module in here writing `import logging` still gets the standard library.

This package replaced the single-file `logging_setup.py`.
"""

from carebridge.logging.reader import log_dir, newest_log_file, parse_log_line, tail_records
from carebridge.logging.setup import configure_logging

__all__ = [
    "configure_logging",
    "log_dir",
    "newest_log_file",
    "parse_log_line",
    "tail_records",
]
