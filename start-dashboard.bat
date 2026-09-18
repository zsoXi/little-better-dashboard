@echo off
rem Little Better Dashboard - one-click launcher (Windows)
rem No dependencies: uses the Python standard library only.
cd /d "%~dp0"
set "PYEXE=py -3"
where python >nul 2>nul
if errorlevel 1 goto run
set "PYEXE=python"
:run
%PYEXE% opencode_dashboard.py --open %*
pause
