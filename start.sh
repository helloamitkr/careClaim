#!/usr/bin/env bash
# Starts everything CareBridge AI needs: Ollama, the FastAPI backend, and the
# Next.js frontend. Postgres is expected to be a locally installed server
# already running on :5432 (no Docker) — see DATABASE_URL in .env.
# Safe to re-run — anything already listening on its port is left alone.
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

# Which LLM backend the agents use — the environment wins, then .env.
if [ -z "${LLM_PROVIDER:-}" ]; then
  for env_file in "$ROOT_DIR/.env" "$BACKEND_DIR/.env"; do
    if [ -f "$env_file" ]; then
      LLM_PROVIDER="$(grep -E '^LLM_PROVIDER=' "$env_file" | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
      [ -n "$LLM_PROVIDER" ] && break
    fi
  done
fi
LLM_PROVIDER="${LLM_PROVIDER:-local}"

log() { echo "[start] $*"; }

port_in_use() {
  # ss sees sockets owned by any user (lsof only shows your own without root,
  # which made the Postgres check fail).
  ss -ltn "( sport = :$1 )" 2>/dev/null | grep -q LISTEN
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

# 1. Postgres — locally installed server, no Docker. Point DATABASE_URL at it
# in .env (e.g. postgresql+psycopg2://postgres:postgres@localhost:5432/careai).
if port_in_use 5432; then
  log "Postgres is running on :5432"
else
  log "ERROR: no Postgres listening on :5432."
  log "Start your local server first, e.g.:  sudo systemctl start postgresql"
  exit 1
fi

# 2. Ollama (local LLM server) — only needed when LLM_PROVIDER=local.
if [ "$LLM_PROVIDER" = "anthropic" ]; then
  log "LLM_PROVIDER=anthropic — skipping Ollama (agents use the Claude API)"
elif port_in_use "$OLLAMA_PORT"; then
  log "Ollama already running on :$OLLAMA_PORT"
elif ! command -v ollama >/dev/null 2>&1; then
  log "ERROR: LLM_PROVIDER=$LLM_PROVIDER but ollama is not installed."
  log "Either install it (https://ollama.com) and run 'ollama pull gemma3:4b',"
  log "or set LLM_PROVIDER=anthropic (+ ANTHROPIC_API_KEY) in .env."
  exit 1
else
  log "Starting Ollama..."
  nohup ollama serve >"$RUN_DIR/ollama.log" 2>&1 &
  echo $! >"$RUN_DIR/ollama.pid"
fi
if [ "$LLM_PROVIDER" != "anthropic" ]; then
  wait_for_http "http://localhost:$OLLAMA_PORT/api/tags" "Ollama"
fi

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
log "  Postgres  -> localhost:5432 (local server)"
if [ "$LLM_PROVIDER" = "anthropic" ]; then
  log "  LLM       -> Claude API (LLM_PROVIDER=anthropic)"
else
  log "  Ollama    -> http://localhost:$OLLAMA_PORT"
fi
log "  API       -> http://localhost:$API_PORT  (docs at /docs)"
log "  Frontend  -> http://localhost:$FRONTEND_PORT"
echo
log "Logs: $RUN_DIR/*.log   Stop everything: ./stop.sh"
