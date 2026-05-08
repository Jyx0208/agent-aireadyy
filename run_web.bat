@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "AGENT_MAX_CONCURRENT_TASKS=1"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python virtual environment was not found.
    echo Please run setup first, then start this file again.
    pause
    exit /b 1
)

echo Starting PRIDE AI-ready Agent Web...
echo URL: http://127.0.0.1:8000
echo Small-server mode: only 1 task runs at a time; extra tasks stay queued.
echo Keep this window open while using the website.
echo Press Ctrl+C to stop.
echo.

"%PYTHON_EXE%" -m uvicorn agent.web.app:app --host 127.0.0.1 --port 8000

echo.
echo Web service stopped.
pause
