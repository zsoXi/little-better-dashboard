@echo off
rem Little Better Dashboard - one-click launcher (Windows)
rem No dependencies: uses the Python standard library only.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 opencode_dashboard.py --open %*
) else (
  python opencode_dashboard.py --open %*
)
pause
