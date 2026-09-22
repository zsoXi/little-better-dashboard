@echo off
rem Little Better Dashboard - uninstaller (Windows).
rem Removes the shortcuts, stops a dashboard still running from this copy
rem and deletes the app folder. The app stores no user data of its own.
rem Agents: run "uninstall.bat /quiet" (exit 0 = ok).
rem Optional overrides: LBD_INSTALL_DIR=<dir> and LBD_NO_SHORTCUTS=1.
setlocal EnableExtensions
set "DEST=%~dp0"
if "%DEST:~-1%"=="\" set "DEST=%DEST:~0,-1%"
if defined LBD_INSTALL_DIR set "DEST=%LBD_INSTALL_DIR%"
set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"

if not defined LBD_NO_SHORTCUTS powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach($dir in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){if($dir){$p=Join-Path $dir 'Little Better Dashboard.lnk';if(Test-Path $p){Remove-Item -LiteralPath $p -Force}}}"

rem Stop our own dashboard if it is still listening on 8765. Skipped for
rem custom install dirs so a test copy never touches a real instance.
if not defined LBD_INSTALL_DIR powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop | Select-Object -First 1; $w = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $c.OwningProcess) -ErrorAction Stop; if ($w.CommandLine -like '*opencode_dashboard.py*') { Stop-Process -Id $w.ProcessId -Force } } catch { }"

echo Removing %DEST%
rem A running script cannot delete its own folder; a detached PowerShell
rem waits for this window to close and then removes everything.
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 2; Remove-Item -LiteralPath '%DEST%' -Recurse -Force -ErrorAction SilentlyContinue"
if not defined QUIET echo Done. You can close this window.
exit /b 0
