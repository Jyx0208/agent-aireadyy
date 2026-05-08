# 启动 PRIDE AI-ready Agent Web 服务
# 用法: .\scripts\run_web.ps1 [-Port 8000] [-Host "0.0.0.0"]

param(
    [int]$Port = 8000,
    [string]$Host = "0.0.0.0"
)

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "错误：未找到 .venv，请先运行 .\scripts\setup.ps1" -ForegroundColor Red
    exit 1
}

Write-Host "启动 PRIDE AI-ready Agent Web 服务..." -ForegroundColor Green
Write-Host "访问地址: http://localhost:$Port" -ForegroundColor Cyan
Write-Host "按 Ctrl+C 停止" -ForegroundColor Yellow
Write-Host ""

& $venvPython -m uvicorn agent.web.app:app --host $Host --port $Port --reload
