@echo off
REM ============================================================
REM  CareBridge AI - Windows equivalent of start.sh.
REM  Starts Postgres (Docker), Ollama, the FastAPI backend and
REM  the Next.js frontend. Safe to re-run - anything already
REM  listening on its port is left alone.
REM
REM  Backend and frontend each open in their own console window
REM  so you can watch the logs. Run stop.bat to shut them down.
REM ============================================================
setlocal
cd /d "%~dp0"

set API_PORT=8010
set FRONTEND_PORT=3010
set OLLAMA_PORT=11434

REM ---- 1. Postgres ------------------------------------------
REM Reuse a locally installed Postgres if one is already listening
REM (point DATABASE_URL at it in .env); otherwise start the Docker one.
call :port_in_use 5432
if not errorlevel 1 (
    echo [start] Postgres already running on :5432 - skipping docker compose
) else (
    echo [start] Starting Postgres via Docker...
    cd /d "%~dp0backend"
    docker compose up -d
    if errorlevel 1 (
        echo [start] docker compose failed - is Docker Desktop running?
        exit /b 1
    )
    cd /d "%~dp0"
)

REM ---- 2. Ollama (local LLM server) --------------------------
call :port_in_use %OLLAMA_PORT%
if not errorlevel 1 (
    echo [start] Ollama already running on :%OLLAMA_PORT%
) else (
    echo [start] Starting Ollama...
    start "Ollama" /min cmd /c "ollama serve"
)
call :wait_http http://localhost:%OLLAMA_PORT%/api/tags Ollama || exit /b 1

REM ---- 3. Backend API (FastAPI via uvicorn) ------------------
call :port_in_use %API_PORT%
if not errorlevel 1 (
    echo [start] Backend API already running on :%API_PORT%
) else (
    echo [start] Starting backend API...
    cd /d "%~dp0backend"
    start "CareBridge API :%API_PORT%" cmd /k "call venv\Scripts\activate.bat && uvicorn carebridge.api.main:app --reload --timeout-graceful-shutdown 3 --port %API_PORT%"
    cd /d "%~dp0"
)
call :wait_http http://localhost:%API_PORT%/api/health "Backend API" || exit /b 1

REM ---- 4. Frontend (Next.js dev server) -----------------------
call :port_in_use %FRONTEND_PORT%
if not errorlevel 1 (
    echo [start] Frontend already running on :%FRONTEND_PORT%
) else (
    echo [start] Starting frontend...
    cd /d "%~dp0frontend"
    start "CareBridge Frontend :%FRONTEND_PORT%" cmd /k "npm run dev -- -p %FRONTEND_PORT%"
    cd /d "%~dp0"
)
call :wait_http http://localhost:%FRONTEND_PORT% Frontend || exit /b 1

echo.
echo [start] All services up:
echo [start]   Postgres  -^> localhost:5432
echo [start]   Ollama    -^> http://localhost:%OLLAMA_PORT%
echo [start]   API       -^> http://localhost:%API_PORT%  ^(docs at /docs^)
echo [start]   Frontend  -^> http://localhost:%FRONTEND_PORT%
echo.
echo [start] Opening the app in your browser. Stop everything: stop.bat
start http://localhost:%FRONTEND_PORT%
endlocal
exit /b 0

REM ---- helpers ------------------------------------------------

:port_in_use
REM errorlevel 0 = in use, 1 = free. Trailing space avoids :8010 matching :80100.
netstat -ano | findstr /r /c:":%1 .*LISTENING" >nul 2>&1
exit /b %errorlevel%

:wait_http
setlocal
set /a TRIES=0
:wait_loop
curl -sf %1 >nul 2>&1
if not errorlevel 1 (
    echo [start] %~2 is up at %1
    endlocal
    exit /b 0
)
set /a TRIES+=1
if %TRIES% gtr 60 (
    echo [start] Timed out waiting for %~2 at %1 - check its console window.
    endlocal
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait_loop
