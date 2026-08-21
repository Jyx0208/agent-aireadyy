@echo off
setlocal
set TASK_NAME=PrideBatchStart
set ACTION=cmd.exe /c E:\pride_processing\run_pride_batch.cmd
schtasks /Create /TN %TASK_NAME% /TR "%ACTION%" /SC ONCE /ST 00:00 /RU PC /IT /F
if errorlevel 1 exit /b %errorlevel%
schtasks /Run /TN %TASK_NAME%
exit /b %errorlevel%
