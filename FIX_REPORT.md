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

---

# FIX_REPORT — F2 access protection (RED → GREEN)

## F2-1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Start of this run: HEAD `7243ea8` ("F1 FIX_REPORT"), `git status --short` clean,
  suite 45 tests OK (F1 DONE).
- F2 code hash tested: `sha256:41c71cf68e2838d5b647db9956592bde85a777d06fc9ad15450953d36adb8e7c`.
- Worktree at test time: dirty (intended F2 patch + tests + matrix + this report).
- Environment: Windows, `py -3.14` = Python 3.14.3. No browser/Playwright.
  Linux/macOS: NOT_RUN. Test servers on `127.0.0.1` with system-allocated ports
  only (port 0; never 8765/8766/8770). Fixtures synthetic in temp dirs, isolated HOME.

## F2-2. Change (TODO → REPRODUCED → PATCHED → VERIFIED → DONE)

- Contract (binding, §6): loopback + per-instance `secrets.token_urlsafe(32)`,
  canonical `127.0.0.1:<real_port>` Host allowlist from `server.server_address`,
  exact `http://127.0.0.1:<port>` Origin, `Authorization: Bearer` on all
  private `/api/*` (incl. GET + POST `/api/close`). Public shell has null
  payloads and no token; `--open` uses `#token=` fragment, never query.
- Changed symbols in `opencode_dashboard.py`:
  - imports: `+re`, `+secrets`; NEW `_TOKEN_RE`, `_new_token`,
    `_expected_host`, `_expected_origin`, `_header_all`, `_is_private_path`.
  - `Handler._check_origin` → strict delegate; NEW `_check_origin_strict`,
    `_check_host` (single Host; missing/multiple 400, mismatch 403),
    `_check_auth` (`compare_digest` after format check; missing/wrong/empty/multi 401),
    `_deny` (generic 400/401/403/405, no private data), `_guard`
    (Host→Origin→auth before any read/synth/`_touch`/close), `_unsupported_method`
    + `__getattr__` guard for HEAD/OPTIONS/unknown methods (405, no effects).
  - `Handler.do_GET`: guard first; shell `/`,`/index.html` returns
    `PAGE` with `null`/`null`, zero DB/synth reads; private routes unchanged
    shape (F1 fields kept, only gated).
  - `Handler.do_POST`: same guard; `/api/close` accepts `?wid=` or
    `X-Window-Id` after auth, else 401/403/400 with no window change.
  - `Handler._send`: `+X-Content-Type-Options: nosniff`,
    `+Referrer-Policy: no-referrer`, `+X-Frame-Options: DENY`; keeps
    `Cache-Control: no-store`; HEAD sends headers only; never
    `Access-Control-Allow-Origin`.
  - `main`: `server.auth_token=_new_token()` (+ class fallback),
    real-port URL + `/#token=` print/open, never `?token=`.
  - Frontend `PAGE`: NEW `#auth-lock` card; token from `#token=` →
    `history.replaceState` + memory + `sessionStorage ocd-token` (never
    `localStorage`); NEW `authHeaders`/`authFetch` (token only to `/api/`),
    `showAuthLock`/`noteAuthFailure` (401 stops auto-refresh, no loop);
    `stopServer` is authed `fetch keepalive` (no `sendBeacon`);
    `load`/`fetchSessions`/`inspect`/`openSession` via `authFetch` with 401
    handling; boot loads only with token.
  - `README.md`: bind-alone claim removed; token/Host/Origin, fragment,
    no-tunnel, authorized-viewer-visible documented.
  - `tests/test_router_tokens.py::TestRouterApiServe`: updated for F2 model
    (authed `/api/router` still 120; shell now `const S0 = null`/`R0 = null`,
    no `tokens_total` embed). F1 contract preserved via API.
- RED evidence (`artifacts/F2-RED.windows.log`, 16 tests: 11 FAIL):
  - `/api/stats` no-token `200 != 401`; foreign Host+Origin `200 != 403`;
    foreign `Origin: http://evil.test/` `200 != 403`; missing Host not 400;
    shell contains canary; HEAD/OPTIONS/unknown bypass; query-token `200 != 401`;
    unauth close `200 != 401`; old-token `200 != 401`; second origin `200 != 403`;
    missing `nosniff`. T02 positive already passed, as expected.
- GREEN evidence:
  - `artifacts/F2-GREEN.windows.log`: **57 tests, OK** (45 prior + 12 new,
    zero regressions; two consecutive runs implied by live + suite).
    `py_compile` OK.
  - `artifacts/F2-http-api.windows.log`: live boot on `127.0.0.1:6439`
    (port 0) with synthetic one-record fixture (100/20/40/5/120, canary model);
    **33/33 PASS** accepted-rejected matrix per route, token REDACTED;
    headline `120.0`, canary only via auth, `GET /` 200 with `auth-lock`,
    `ocd-token`+`replaceState`, `S0/R0 null`; required headers on every case.
- Matrix: `docs/ACCEPTANCE_MATRIX.md` F2-T01..T12 → PASS (windows,
  `artifacts/F2-GREEN.windows.log`); F1-T01..T10 stay PASS (F1-T02 via
  updated authed test); all other rows stay NOT_RUN.
- Limitations: DOM pixel rendering + two-origin browser navigation need the
  F8 browser suite (no Playwright here); T08/T10/T11 browser remainders
  verified via real-HTTP Origin/Host/token + shipped-JS string asserts, not
  mislabeled as full DOM. `sendBeacon` removal documented: unload delivery
  not promised; idle-timeout/monitor is the fallback. Linux pending.
- Retries: 1 PATCHED cycle + 1 test-only correction (T08 `?token=` comment
  literal, T12 generic-UI-word strictness) → GREEN. No BLOCKED.

## F2-3. Not changed (verified)

- F1 router shape/fields kept (additive only); local `day_total`/`dayTotal`
  untouched; `pl` guard, loopback bind, `json_html` guard, parameterized SQL,
  source logs/DBs untouched. `git diff --check` clean. No work outside V3 repo;
  no push/merge/remote; only own PIDs handled.

## F2-4. Verify gate outcome

- `py -3.14 tools/verify_acceptance.py --gate core` → FAIL (expected):
  F1+F2 PASS on windows, but F3–F8/PUB/INT remain NOT_RUN by design, and
  `TEST_REPORT.json` still carries the F1-only hash. Full log in
  `artifacts/F2-verify-core.windows.log`. This is the honest core-gate state,
  not a regression.
## F4-1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Start of this run: HEAD `2759dc1` ("F2 access protection + RED-GREEN tests");
  worktree DIRTY (`M opencode_dashboard.py`, `M tests/test_router_outcomes.py`)
  = genuine partial F4 work from an interrupted predecessor run (kept after
  diff review: parse-level `_router_outcome`/`usage_known`/`outcome` field,
  `SYNTH_SCHEMA` 3, `synth_context`, plus `TestF4UnknownOutcomes` RED tests;
  no foreign code).
- F4 code hash tested: `sha256:797120f4bef2f9fd83a9290898cef8dc12aa1a651f937f5ed6350258d7e07df2`.
- Worktree at test time: dirty (F4 patch + tests + matrix + this report).
- Environment: Windows, `py -3.14` = Python 3.14.3. No browser/Playwright.
  Linux/macOS: NOT_RUN. Test servers on `127.0.0.1` with system-allocated ports
  only (port 0; never 8765/8766/8770). Fixtures synthetic in temp dirs, isolated HOME.

## F4-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- RED evidence (`artifacts/F4-RED.windows.log`, predecessor run on the same
  partial tree): Ran 13 tests, FAILED failures=10. Failure modes: half-patched
  `query_router_stats` crashed on None status (err500/day paths), writer still
  stamped status 200, UI untouched; F1-T10 regressed via the same crash.
- PATCH (my `f4-patch.py`, 26/26 replacements OK, plus `f4-t07-fix.py`, 3/3):
  synth status null + outcome unknown; normalizer 200-299 success /
  400-599 error / missing-null-0-bad-1xx-3xx unknown; `usage_known` separate
  from outcome (explicit-zero True, missing False); totals `unmetered` =
  not `usage_known` (T07 root cause was the totals counter, not the event
  classifier); per-aggregation success/error/unknown counters + coverage +
  `success_rate` over known only (null when 0 known); averages over metered
  events; real 401/429/500 preserved; `durationMs` null + `latency_na`;
  `SYNTH_SCHEMA` 3 + cache invalidation; synth records labeled
  'Usage events'; UI Outcome-unavailable cell + unknown chips + hint + export;
  JS syntax fix (spurious leading quote on the Outcome cell line, found by
  `node --check` on TRUE blocks, fixed at L3552).
- GREEN evidence:
  - `artifacts/F4-GREEN.windows.log`: **Ran 67 tests in 8.625s, OK**
    (57 prior + 10 new F4, zero regressions). `py_compile` OK.
  - `node --check` on both TRUE JS blocks: EXIT 0/0 (extraction by PAGE
    template, not naive script regex).
  - Live authed boot on `127.0.0.1:1281` (real user data): unauth
    `/api/router` 401 (F2 intact); authed requests=61734 total=9583121584
    ok=0 errors=0 unknown=61734 known_outcomes=0 success_rate=null
    coverage 0/61734 unmetered=0 invalid_records=0 total_conflicts=0
    tokens_reasoning=14523651 tokens_cache_write=0 avg_ms=null
    latency_na=True req_word='Usage events' trunc=False src=codex-synth
    cache_rate=97. Test server (own PID) killed after evidence.
- Matrix: `docs/ACCEPTANCE_MATRIX.md` F4-T01..T10 -> PASS (windows,
  `artifacts/F4-GREEN.windows.log`); all other rows unchanged.
- Retries: 1 PATCHED cycle + 1 test-only correction (T07 totals formula) +
  1 JS syntax fix -> GREEN. No BLOCKED.

## F4-3. Not changed (verified)

- F1 router normalization intact (F1-T01..T10 green in the same 67-run);
  local `day_total`/`dayTotal`/`outMerged` untouched (F1-T10
  `day_total==165` still passes; diff scan zero hits on the local path).
- F2 token model intact (live unauth 401 plus all F2 tests green).
- `pl` isinstance guard kept; new `rec` guard added (F5c partial hardening
  noted, full F5c stays in its own finding). `git diff --check` clean.
  No work outside V3 repo; no push/merge/remote; only own PIDs handled.

## F4-4. Verify gate outcome

- `py -3.14 tools/verify_acceptance.py --gate core` -> FAIL (expected):
  F1+F2+F4 PASS on windows, but F5-F8/PUB/INT remain NOT_RUN by design, and
  `TEST_REPORT.json` still carries the F1-only hash. Full log in
  `artifacts/F4-verify-core.windows.log`. This is the honest core-gate state,
  not a regression.
