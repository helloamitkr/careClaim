#!/usr/bin/env bash
# Starts everything CareBridge AI needs: Postgres, Ollama, the FastAPI
# backend, and the Next.js frontend. Safe to re-run — anything already
# listening on its port is left alone.
#
# Ports are non-default (8010/3010) because this machine already has other,
# unrelated services on 8000/8001/3000. See stop.sh to shut it all back down.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
RUN_DIR="$ROOT_DIR/.run"
mkdir -p "$RUN_DIR"

API_PORT=8010
FRONTEND_PORT=3010
OLLAMA_PORT=11434

log() { echo "[start] $*"; }

port_in_use() {
  lsof -i ":$1" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_http() {
  local url="$1" label="$2" tries=0
  until curl -sf "$url" >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -gt 60 ]; then
      log "Timed out waiting for $label at $url — check its log in $RUN_DIR/"
      return 1
    fi
    sleep 1
  done
  log "$label is up ($url)"
}

# 1. Postgres, via Docker Compose
log "Starting Postgres..."
(cd "$BACKEND_DIR" && docker compose up -d)

# 2. Ollama (local LLM server)
if port_in_use "$OLLAMA_PORT"; then
  log "Ollama already running on :$OLLAMA_PORT"
else
  log "Starting Ollama..."
  nohup ollama serve >"$RUN_DIR/ollama.log" 2>&1 &
  echo $! >"$RUN_DIR/ollama.pid"
fi
wait_for_http "http://localhost:$OLLAMA_PORT/api/tags" "Ollama"

# 3. Backend API (FastAPI via uvicorn)
if port_in_use "$API_PORT"; then
  log "Backend API already running on :$API_PORT"
else
  log "Starting backend API..."
  (
    cd "$BACKEND_DIR"
    source venv/bin/activate
    # --timeout-graceful-shutdown: open SSE connections (/api/logs/stream)
    # otherwise block --reload forever; EventSource clients auto-reconnect.
    nohup uvicorn carebridge.api.main:app --reload --timeout-graceful-shutdown 3 --port "$API_PORT" >"$RUN_DIR/api.log" 2>&1 &
    echo $! >"$RUN_DIR/api.pid"
  )
fi
wait_for_http "http://localhost:$API_PORT/api/health" "Backend API"

# 4. Frontend (Next.js dev server)
if port_in_use "$FRONTEND_PORT"; then
  log "Frontend already running on :$FRONTEND_PORT"
else
  log "Starting frontend..."
  (
    cd "$FRONTEND_DIR"
    nohup npm run dev -- -p "$FRONTEND_PORT" >"$RUN_DIR/frontend.log" 2>&1 &
    echo $! >"$RUN_DIR/frontend.pid"
  )
fi
wait_for_http "http://localhost:$FRONTEND_PORT" "Frontend"

echo
log "All services up:"
log "  Postgres  -> localhost:5432"
log "  Ollama    -> http://localhost:$OLLAMA_PORT"
log "  API       -> http://localhost:$API_PORT  (docs at /docs)"
log "  Frontend  -> http://localhost:$FRONTEND_PORT"
echo
log "Logs: $RUN_DIR/*.log   Stop everything: ./stop.sh"
