@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "AGENT_MAX_CONCURRENT_TASKS=4"
set "PYTHONPATH=%~dp0src"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python virtual environment was not found.
    echo Please run setup first, then start this file again.
    pause
    exit /b 1
)

echo Starting PRIDE AI-ready Agent Web...
echo URL: http://127.0.0.1:8000
echo Operations mode: persistent queue with 4 review workers.
echo Keep this window open while using the website.
echo Press Ctrl+C to stop.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_platform.ps1" -ListenHost 127.0.0.1 -Port 8000 -WorkerCount 4

echo.
echo Web service stopped.
pause
