#!/usr/bin/env bash
# Stops the backend API and frontend dev server started by start.sh.
# Leaves Postgres and Ollama running by default, since those behave more
# like shared local services than this app's own processes — pass
# --all to also stop them (Postgres via docker compose down, Ollama by PID
# if this script started it).

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
RUN_DIR="$ROOT_DIR/.run"

log() { echo "[stop] $*"; }

stop_pid_file() {
  local pid_file="$1" label="$2"
  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file")"
    if kill "$pid" 2>/dev/null; then
      log "Stopped $label (pid $pid)"
    else
      log "$label (pid $pid) was not running"
    fi
    rm -f "$pid_file"
  else
    log "No pid file for $label — nothing to stop"
  fi
}

stop_pid_file "$RUN_DIR/frontend.pid" "frontend"
stop_pid_file "$RUN_DIR/api.pid" "backend API"

if [ "${1:-}" = "--all" ]; then
  stop_pid_file "$RUN_DIR/ollama.pid" "Ollama"
  log "Stopping Postgres (docker compose down)..."
  (cd "$BACKEND_DIR" && docker compose down)
else
  log "Postgres and Ollama left running (pass --all to stop those too)"
fi
