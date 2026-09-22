# Changelog

## [1.0.0] — audit repair F1-F8

### Added
- In-app update: the topbar Update button checks the latest GitHub release, downloads the zip, verifies the sha256 from the release `checksums.txt`, replaces the installed files in place (keeping `.bak` copies) and restarts the dashboard on the same port and token.
- Windows installer `install.bat` (Desktop and Start Menu shortcuts, no admin rights), `uninstall.bat`, and `AGENT_INSTALL.md` with exact commands for agents installing the app for someone.
- Release workflow: pushing a `vX.Y.Z` tag checks that the tag matches `DASHBOARD_VERSION`, builds `little-better-dashboard.zip` plus `checksums.txt` and publishes them as a GitHub release.

### Fixed
- F1: router token normalization — the shared scope shows 100/20/40/5 as 120.
- F2: loopback-only bind plus per-instance bearer token and strict Host/Origin; public shell carries no private data.
- F3: session activity is a history signal, never execution status.
- F4: unknown outcomes stay unknown (no synthetic successes, tokens kept).
- F5a/F6a: synthesis publish is atomic; a failed publish never commits cache.
- F5b/F5c: readers see the same file set; non-object JSON records are rejected.
- F6b: bounded tail reader for huge ledgers (no whole-file readlines) while synthesis keeps the full history.
- F6c: incremental Codex parsing with per-file fingerprints and a derived restart checkpoint (`.cache/`, safe to delete).
- F6d: independent per-source refresh with a deadline covering the body, honest partial status, generation guards and a single codex-synthesis build.
- F6e: bounded inspector — paged messages with cursors, batched parts, ~1 MiB response budget with explicit truncation markers.
- F6f: per-worktree git TTL cache (10 s), timeouts with stale fallback, argv-only calls, no disk scans, no remote fetch.
- F7: commit windows are global, overlapping and non-additive ("usage in the 24h before each commit" is not commit cost).
- F8: real-browser coverage (F6d + F8 cases), integration suite (INT), publication checks (PUB), acceptance report builder and verifier gates.

### Known limitations
- Real CI runs are pending: the workflow exists in the repository, but nothing was pushed and no CI job has executed (CI_PENDING). A workflow file alone is not CI evidence.
- Only Windows with Python 3.14.3 was verified on this machine; Linux and macOS runs are still pending.
- Performance measurements from spec section 22 are recorded for Windows:
  deterministic small (10k) and large (100k) scenarios, baseline vs final,
  with bytes-read, parser, git-subprocess and tracemalloc data under
  `artifacts/performance/` and `artifacts/PERF-T01..T06.windows.log`.
