# FIX_REPORT — F1 router token normalization (RED → GREEN)

## 1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Audit baseline commits: `d39c7fc`, `1a345a1` (README screenshot only).
- F8-skeleton commit (start of this run, tree clean): `3bb95d9`.
- This run starts from `3bb95d9`; HEAD at start confirmed `3bb95d9`, `git status --short` clean.
- Dashboard blob in local history (`d39c7fc`, `1a345a1`, `3bb95d9` all identical):
  `f0ee89ed709e916617959d64e59aba22687580bd`.
  NOTE: the repair spec cites blob `fee1ded…` from its public-main read; the local
  checkout carries `f0ee89ed…`. All F1 anchors used below were re-verified by symbol
  in the local file before editing (never by line number alone).
- F1 code commit: `84f4d05` ("F1 router token normalization + RED-GREEN tests").
  Report published in a second commit per §23.3 (no self-referential hash loop).
- Code content hash tested: `sha256:ce11d08fee32f92ef9a0e6a4cea6f97fc3714fa9566bc2ec8feab0585fc65e95`.
- Worktree at test time: dirty (intended F1 patch + tests + matrix); committed at end.
- Environment: Windows, `py -3.14` = Python 3.14.3, node v24.14.0 (used to execute
  shipped router JS). No browser/Playwright available. Linux/macOS: NOT_RUN.

## 2. Findings

### F8 (skeleton, pre-existing this run)
- Change: none by this run. Skeleton tests/tools/docs/matrix from `3bb95d9` reused.
- Baseline suite before patch: **34 tests, OK** (`py -3.14 -m unittest discover -s tests`).

### F1 — router token normalization (TODO → REPRODUCED → PATCHED → VERIFIED → DONE)
- Contract (binding): router-path total = `ti + to` everywhere. Cached input is a
  subset of input, reasoning a subset of output — never additive. Payload field
  names/shapes kept (purely additive: `total_reported`, `usage_partial` per event;
  `invalid_records`, `total_conflicts` in `totals`).
- Changed symbols in `opencode_dashboard.py`:
  - `_router_day_tokens` — dropped `+ tr` (now `ti + to`).
  - NEW `_router_token(value, present)` — router-only sanitizer: only real JSON
    numbers are measurements; bools, NaN/Infinity, negatives, strings/objects/lists
    sanitize to `0.0` + count; missing/null is absent, not invalid. (`num()` for the
    local path untouched.)
  - `parse_router_events` — normalization stage + returns `(events, problems)`:
    complete components → `total = ti + to` (declared conflict counted in
    `total_conflicts`, source kept in `total_reported`); total-only → reported sum
    kept with `usage_partial=True`; otherwise `0.0` + partial. `ms` sanitized
    finite/non-negative. Only callers are the two `query_router_stats` sites.
  - `query_router_stats` — day buckets accumulate normalized `e["total"]`;
    `day_entries.total` from bucket total; activity heatmap reuses authoritative day
    total (empty window days fall back to `ti + to = 0`); headline `tokens_total`
    and `avg_per_session` from normalized event totals; `totals` gains
    `invalid_records`, `total_conflicts`.
  - `blank_router_stats` — same two `totals` keys (`0`) for shape parity.
  - Frontend `rDayTokenTotal(d)` — dropped `+ (d.tr||0)` (now `(d.ti||0)+(d.to||0)`).
    All consumers (`rSumDays`, `rDayTip`, `rActDayTotal`, summary cards, KPIs,
    providers/models tips, CSV export) flow through it; `rStackChart` decomposition
    already summed to `ti + to` in both split modes — untouched.
  - `tests/test_router_tokens.py` — 11 new F1 tests (T01–T10 + server case);
    skeleton expectation `_router_day_tokens({10,5,2})` corrected `17 → 15`
    (the skeleton had encoded the bug; the binding contract is `ti + to`).
- RED evidence (`artifacts/F1-RED.windows.log`, 14 tests: 8 FAIL + 1 ERROR):
  - node executing shipped JS: `rDayTokenTotal must be ti+to: 125 !== 120`.
  - activity cell `125.0 != 120`; served `/api/router` cell `125.0 != 120`;
    `tr` present in `rDayTokenTotal` source; T07 totals `120.0 != 170`
    (total-only 50 dropped, conflict 125 kept in models vs 120 in days);
    T08 `KeyError: 'invalid_records'` (no sanitizer/counter).
  - T04 (cache rate 40%) and T09 (local guard) already passed on baseline, as expected.
- GREEN evidence:
  - `artifacts/F1-GREEN.windows.log`: **45 tests, OK**, two consecutive runs
    (no cross-test state leakage). `py_compile` OK. Baseline was 34; +11 new, 0 regressions.
  - Manual boot (`artifacts/F1-router-api.windows.log`): dashboard booted on
    `127.0.0.1:1277` (port 0 → system-allocated) with a synthetic one-record
    fixture (100/20/40/5/120) in an isolated temp HOME; `curl exit=0`;
    headline/per-model/day/activity all `120.0`, `cache_rate 40.0`;
    `GET /` embeds `"tokens_total": 120`. Server shut down cleanly.
- Matrix: `docs/ACCEPTANCE_MATRIX.md` F1-T01..T10 → PASS (windows evidence paths);
  every other row stays NOT_RUN.
- Limitations: full-DOM card/tooltip pixel rendering needs the F8 browser suite
  (no Playwright here); F1-T02 verified instead via real HTTP serving + embedded
  payload + executed shipped JS (node asserts on `rDayTokenTotal`/`rSumDays`/`rDayTip`
  and wiring asserts for `rActDayTotal`/CSV export). Linux results pending.

### F2, F3, F4, F5a–c, F6a–f, F7, F8, PUB, INT
- NOT_RUN by this run (out of scope). Deliberately NOT touched:
  `ensure_codex_synth` `"status": 200` (F4), `ok` classification (F4),
  auth/Host/token (F2), tail/incremental/commit-window work (F6/F7).

## 3. Not changed (verified)

- Local OpenCode semantics byte-identical: `day_total` (ti+to+tr+cache additive),
  `dayTotal`, `outMerged`, local `cache_rate`, local SQL SUMs, local totals
  derivations, local KPI, local sumDays/periodTotal/charts/CSV.
- Proof: `git diff -U0` hunk list touches only the import line, `_router_day_tokens`,
  `_router_token`+`parse_router_events`, `query_router_stats`, `blank_router_stats`,
  and the `rDayTokenTotal` JS line; a targeted scan of removed/added lines for
  `day_total(`/`dayTotal`/`outMerged`/`cache_rate(`/local SQL shows zero local-path
  changes (the only `day_total` substring hit is the new router-local variable
  `day_total_by_date` plus a code comment — not the local function).
- Runtime guard: `test_f1_09_local_path_unchanged` passes before and after
  (`day_total({100,20,5,40}) == 165`, `cache_rate(80,20) == 20.0`).
- Untouched: `pl` guard in `_parse_codex_file`, loopback bind, `json_html` script
  guard, parameterized SQL, source logs/DBs. No work outside the V3 repo
  (UglyDashboard / live Dashboard / Downloads never touched).

## 4. Integration and browser results

- Integration matrix: NOT_RUN (requires F2–F7). F1-relevant slice covered:
  cold-serve with synthetic fixture over real HTTP (see GREEN evidence).
- Browser suite (F8-T03, `tools/run_browser_tests.py`): NOT_RUN — no browser here;
  shipped-JS execution via node is documented above, not mislabeled as DOM testing.

## 5. Platform results

- windows: F1-T01..T10 PASS (evidence in `artifacts/`).
- linux: NOT_RUN (no runner in this run). macOS: NOT_RUN / not claimed.

## 6. CI

- `CI_PENDING`. Workflow is F8 skeleton only; local commits only, no push/merge/remote
  per run constraints. No CI run exists for the F1 commit yet.

## 7. Performance

- No benchmark run for F1 (arithmetic-only change; no I/O, tail, or cache path altered).
  Full suite: 45 tests in ~1.16 s (see GREEN log header). No perf claims made.

## 8. Repo hygiene and screenshot

- `git diff --check`: clean. `artifacts/` (gitignored) holds RED/GREEN/API/verifier logs
  + `TEST_REPORT.json`; no fixtures, secrets, tokens, or user data in tracked files.
- Tracked changes: `opencode_dashboard.py`, `tests/test_router_tokens.py`,
  `docs/ACCEPTANCE_MATRIX.md`, this report. Nothing else.
- Screenshot: untouched (PUB scope, NOT_RUN).

## 9. Blocked items

- None for F1. Zero patch retries needed (single PATCHED cycle → GREEN).
  No row faked: F1-T02's browser-DOM remainder is disclosed in §2/§4 and stays
  with the F8 browser suite as NOT_RUN.

## 10. Profile / UglyDashboard proposals

- None (separate scope, no changes made or proposed here).

## 11. Remote operations

- None. No push, merge, pull, fetch, release, or cross-repo edits. Local commits only.

## 12. Processes / sessions cleaned up

- Unittest servers: started per-test on `127.0.0.1:0`, shut down + joined in `tearDown`.
- Manual boot server: foreground process, `shutdown()` + `server_close()` + thread join,
  process exited (PID-file host check confirms no stray process).
- No process killed by name; only own PIDs/threads handled. Temp HOME dirs removed
  via `TemporaryDirectory` cleanup; repo `artifacts/` holds only logs + report JSON.
