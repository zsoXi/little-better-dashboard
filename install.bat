@echo off
rem Little Better Dashboard - installer (Windows, no admin rights needed).
rem Copies the app into %LOCALAPPDATA%\Programs\LittleBetterDashboard and
rem creates Desktop + Start Menu shortcuts with the mascot icon.
rem Run it again any time to update the installed copy.
rem Agents: run "install.bat /quiet" for an unattended install (exit 0 = ok).
rem Optional overrides: LBD_INSTALL_DIR=<dir> and LBD_NO_SHORTCUTS=1.
setlocal EnableExtensions
set "SRC=%~dp0"
set "DEST=%LOCALAPPDATA%\Programs\LittleBetterDashboard"
if defined LBD_INSTALL_DIR set "DEST=%LBD_INSTALL_DIR%"
set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"
if /i "%~2"=="/quiet" set "QUIET=1"

rem --- Python 3.10 or newer on PATH -----------------------------------------
set "PYEXE="
where py >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo Python 3 was not found on PATH.
  echo Get Python 3.10 or newer from https://www.python.org/downloads/windows/
  echo then run this installer again.
  if not defined QUIET pause
  exit /b 1
)
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  if not defined QUIET pause
  exit /b 1
)

rem --- copy the app files ----------------------------------------------------
if not exist "%DEST%" mkdir "%DEST%"
for %%F in (opencode_dashboard.py start-dashboard.bat uninstall.bat logo.ico logo.png README.md) do (
  if exist "%SRC%%%F" copy /y "%SRC%%%F" "%DEST%\" >nul
)
if not exist "%DEST%\opencode_dashboard.py" (
  echo Could not copy the app files. Run this installer from the extracted zip.
  if not defined QUIET pause
  exit /b 1
)

rem --- shortcuts (Desktop + Start Menu) --------------------------------------
if not defined LBD_NO_SHORTCUTS powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%DEST%';$t=Join-Path $d 'start-dashboard.bat';$i=Join-Path $d 'logo.ico';$s=New-Object -ComObject WScript.Shell;foreach($dir in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){if($dir){$l=$s.CreateShortcut((Join-Path $dir 'Little Better Dashboard.lnk'));$l.TargetPath=$t;$l.WorkingDirectory=$d;$l.IconLocation=$i;$l.Description='Local usage dashboard for OpenCode, Codex, Jev and Antigravity';$l.Save()}}"

echo.
echo Installed to: %DEST%
if not defined LBD_NO_SHORTCUTS echo Shortcuts: Desktop and Start Menu ("Little Better Dashboard").
if defined QUIET exit /b 0
echo.
set /p LAUNCH="Start the dashboard now? [y/N] "
if /i "%LAUNCH%"=="y" start "" "%DEST%\start-dashboard.bat"
exit /b 0
