@echo off
REM ============================================================
REM  CareBridge AI - one-time setup for Windows.
REM  Run this ONCE before start.bat.
REM
REM  Requires these to be installed and on PATH already:
REM    - Python 3.11+   https://www.python.org/downloads/
REM    - Node.js 20+    https://nodejs.org/
REM    - Docker Desktop https://www.docker.com/products/docker-desktop/
REM    - Ollama         https://ollama.com/download/windows
REM ============================================================
setlocal
cd /d "%~dp0"

echo === Checking prerequisites ===
where python >nul 2>&1 || (echo [ERROR] Python not found. Install Python 3.11+ from https://www.python.org/downloads/ and tick "Add to PATH". & exit /b 1)
where node   >nul 2>&1 || (echo [ERROR] Node.js not found. Install Node.js 20+ from https://nodejs.org/ & exit /b 1)
where npm    >nul 2>&1 || (echo [ERROR] npm not found. It ships with Node.js - reinstall from https://nodejs.org/ & exit /b 1)
where docker >nul 2>&1 || (echo [ERROR] Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ & exit /b 1)
where ollama >nul 2>&1 || (echo [ERROR] Ollama not found. Install from https://ollama.com/download/windows & exit /b 1)
echo All prerequisites found.

echo.
echo === Backend: creating Python venv and installing dependencies ===
cd backend
if not exist venv python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || exit /b 1
pip install -e . || exit /b 1
call venv\Scripts\deactivate.bat
cd ..

echo.
echo === Frontend: installing npm packages ===
cd frontend
call npm install || exit /b 1
cd ..

echo.
echo === Pulling LLM model gemma3:4b (about 3 GB, one-time download) ===
ollama pull gemma3:4b

echo.
echo Setup complete. Now run start.bat
endlocal
