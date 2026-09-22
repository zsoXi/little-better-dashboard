# Install Little Better Dashboard (for AI agents)

You are reading this because someone asked you to install this project. Follow the path that matches the situation. Everything here is Windows-first, needs no admin rights and never touches the user's data beyond reading it.

## What this is

A single-file, dependency-free Python dashboard that reads local usage data from OpenCode, Codex, Jev and Antigravity and serves it at `http://127.0.0.1:8765`. It only binds to loopback and requires a per-instance token, which the launcher prints in the URL fragment.

## Requirements

- Windows 10 or 11.
- Python 3.10 or newer on PATH (`py -3` or `python` must work). If it is missing, tell the user to install it from https://www.python.org/downloads/windows/ and stop; do not try to install Python silently.
- No other dependencies. No admin rights. No services, scheduled tasks or registry changes.

## Install from the latest release (no git needed)

```powershell
$zip = Join-Path $env:TEMP "little-better-dashboard.zip"
Invoke-WebRequest -Uri "https://github.com/zsoXi/little-better-dashboard/releases/latest/download/little-better-dashboard.zip" -OutFile $zip
$dir = Join-Path $env:TEMP "little-better-dashboard"
Expand-Archive -Path $zip -DestinationPath $dir -Force
& (Join-Path $dir "install.bat") /quiet
if ($LASTEXITCODE -ne 0) { throw "install.bat failed with exit code $LASTEXITCODE" }
```

The installer copies the app to `%LOCALAPPDATA%\Programs\LittleBetterDashboard` and creates Desktop and Start Menu shortcuts. Exit code 0 means success; a non-zero exit means Python was missing or the files could not be copied.

## Install from a git clone

```powershell
git clone https://github.com/zsoXi/little-better-dashboard.git
Set-Location little-better-dashboard
cmd /c "install.bat /quiet"
if ($LASTEXITCODE -ne 0) { throw "install.bat failed with exit code $LASTEXITCODE" }
```

To run it straight from the clone without installing, launch `start-dashboard.bat` in that folder instead.

## Verify the install

```powershell
& "$env:LOCALAPPDATA\Programs\LittleBetterDashboard\start-dashboard.bat"
```

The console prints two lines: `Dashboard live at http://127.0.0.1:8765` and an address with `#token=...`. That address is the one the user opens; the token is required for all `/api/*` requests and plain bookmarks without it stay empty on purpose. `GET /` returns 200 without a token (public shell), `/api/stats` needs `Authorization: Bearer <token>`.

If the port is busy, the launcher retires its own stale instance first; other services on 8765 are left alone. Ctrl+C in the console stops the dashboard.

## Update

- In the dashboard: the **Update** button in the topbar checks the latest GitHub release, downloads it, verifies the sha256 from the release `checksums.txt`, replaces the installed files in place (keeping `.bak` copies) and restarts on the same port and token.
- Or repeat the install steps above with a fresh zip; the installer overwrites the installed copy.

## Uninstall

```powershell
& "$env:LOCALAPPDATA\Programs\LittleBetterDashboard\uninstall.bat" /quiet
```

Removes the shortcuts, stops the dashboard if it is still listening on 8765 and deletes the app folder. The app stores no data of its own, so nothing else needs cleanup.

## Rules for agents

- Do not request admin rights, do not create services or scheduled tasks, do not edit the registry.
- Do not read, copy or upload the user's databases, logs or session content; the app itself only reads them locally, read-only.
- Do not run the dashboard on a public interface; it is loopback-only by design and adding tunnels or proxies is out of scope.
- When a step fails, report the exact command and its output instead of retrying silently.
