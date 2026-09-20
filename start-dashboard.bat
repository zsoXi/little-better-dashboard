@echo off
rem Little Better Dashboard - one-click launcher (Windows)
rem No dependencies: uses the Python standard library only.
cd /d "%~dp0"
rem Fresh token on every start: a stale server still holding the port would
rem reject the new browser tab, leaving an empty page. Retire our own
rem listener first (exact command-line match, so foreign services are safe).
rem Skipped for --help so read-only runs never touch a live instance.
echo %* | findstr /i /c:"--help" >nul
if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | Select-Object -First 1; $w = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $c.OwningProcess) -ErrorAction Stop; if ($w.CommandLine -like '*opencode_dashboard.py*') { Stop-Process -Id $w.ProcessId -Force; for ($i=0; $i -lt 50 -and (Get-Process -Id $w.ProcessId -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 100 } } } catch { }"
set "PYEXE=py -3"
where python >nul 2>nul
if errorlevel 1 goto run
set "PYEXE=python"
:run
%PYEXE% opencode_dashboard.py --open %*
pause
