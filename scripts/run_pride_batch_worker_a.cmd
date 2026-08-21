@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File E:\pride_processing\prepare_pride_batch_on_tower3.ps1 -NasRoot N:\members\jiangyuxuan\PRIDE_benchmark_20260817 -WorkRoot C:\Users\PC\pride_processing -PwizExecutable E:\pride_processing\tools\pwiz\msconvert.exe -ProjectFilter PXD062150,PXD067311,PXD071784,PXD079014 -WorkerId worker_a -Resume
exit /b %errorlevel%
