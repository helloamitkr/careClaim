@echo off
REM ============================================================
REM  CareBridge AI - Windows equivalent of stop.sh.
REM  Stops the backend API (:8010) and frontend (:3010) by port.
REM  Postgres and Ollama are left running by default, since they
REM  behave like shared local services.
REM
REM    stop.bat --all   also stops Ollama and Postgres
REM ============================================================
setlocal
cd /d "%~dp0"

call :kill_port 3010 "frontend"
call :kill_port 8010 "backend API"

if /i "%~1"=="--all" (
    call :kill_port 11434 "Ollama"
    echo [stop] Stopping Postgres ^(docker compose down^)...
    cd /d "%~dp0backend"
    docker compose down
    cd /d "%~dp0"
) else (
    echo [stop] Postgres and Ollama left running ^(pass --all to stop those too^)
)
endlocal
exit /b 0

:kill_port
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
    taskkill /pid %%p /t /f >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo [stop] Stopped %~2 on port %1
) else (
    echo [stop] Nothing listening on port %1 - %~2 was not running
)
exit /b 0
