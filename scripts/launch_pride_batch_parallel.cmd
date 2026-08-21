@echo off
setlocal
schtasks /End /TN PrideBatchStart >nul 2>&1
schtasks /Create /TN PrideBatchWorkerA /TR "cmd.exe /c E:\pride_processing\run_pride_batch_worker_a.cmd" /SC ONCE /ST 00:00 /RU PC /IT /F
if errorlevel 1 exit /b %errorlevel%
schtasks /Create /TN PrideBatchWorkerB /TR "cmd.exe /c E:\pride_processing\run_pride_batch_worker_b.cmd" /SC ONCE /ST 00:00 /RU PC /IT /F
if errorlevel 1 exit /b %errorlevel%
schtasks /Run /TN PrideBatchWorkerA
schtasks /Run /TN PrideBatchWorkerB
exit /b 0
