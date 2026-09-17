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

---

# FIX_REPORT — F5a/F6a synth publish durability (RED → GREEN)

## F5a-F6a-1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Start of this run: HEAD `47854a5` ("F4 FIX_REPORT"). Worktree DIRTY at start:
  `M opencode_dashboard.py`, `M tests/test_router_outcomes.py`,
  `?? tests/test_synth_publish.py` = the F5a/F6a patch + tests from an
  interrupted predecessor run. Kept after byte-level diff review (contract
  matches §9/§12 exactly; no foreign code; local session-stats path untouched).
- Code hashes tested (current worktree bytes):
  - `opencode_dashboard.py`: `sha256:7bb1209eb0cb50e1b628f94a664d23cded1f0e7cac444acfb4debb21712373c4`
  - `tests/test_synth_publish.py`: `sha256:7e5d0b1cf66773f74e9f21ffe2793aa062d4e20b218572285118c98d92d017b0`
  - `tests/test_router_outcomes.py`: `sha256:33f6b19a0d5b9178b615264715b2f27f43d258daa44eef48b813649bb91e3ad6`
- Worktree at test time: dirty (intended patch + tests + matrix + this report).
- Environment: Windows, `python` = Python 3.14.3. No browser/Playwright.
  Linux/macOS: NOT_RUN. No servers started by these tests (pure unit/thread
  tests); fixtures synthetic in temp dirs with isolated HOME. Pure-LF files:
  no `git stash`/`checkout` revert was used (would rewrite EOLs); the RED
  baseline was executed from a throwaway copy instead.

## F5a-F6a-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- Contract (§9 F5a, §12 F6a, binding):
  - `ensure_codex_synth()` returns `(path, error)`; `error` is `None` on
    success. A failed publish with a usable previous generation keeps serving
    the OLD file (bytes + committed signature unchanged) and reports the
    error text via the return value and `_codex_last_error`; the next call
    retries on its own (no restart, no data added, no `force=True` needed).
    With NO usable previous generation the failure is explicit
    (`RuntimeError`), never a path to a nonexistent "ready" file.
  - Publish = unique candidate tmp file in the SAME directory/filesystem,
    written and flushed outside the lock, then `os.replace` under a short
    `_SYNTH_LOCK` held only for the swap + state commit. Cache, signature and
    `_codex_last_ok` are committed only AFTER a successful swap. On error only
    the own tmp is deleted; the previous snapshot stays. Windows
    replace-refusal counts as failed publish (no in-place fallback).
  - Readers stay lock-free: complete generation A or complete B, never empty,
    torn, or mixed. Lock scope is thread-level within this one instance
    (documented at the state block); atomic replace carries no power-loss
    promise (documentation says so).
  - Fast path still stats the output file (deleted rebuildable index
    regenerates); a schema bump (`SYNTH_SCHEMA`) forces a rebuild; exactly one
    publish attempt per call (no internal retry loop).
- Changed symbols in `opencode_dashboard.py`:
  - NEW state: `_codex_last_error`, `_codex_last_ok`, `_SYNTH_LOCK`,
    `_synth_tmp_active`, `_synth_tmp_seq` (+ scope/atomicity comment).
  - NEW helpers: `_synth_sig_of(files)` (schema+count+size/mtime sum),
    `_cleanup_synth_tmps()` (own pattern only, skips registered in-flight
    tmps, spare foreign files), `_synth_next_seq()`.
  - `ensure_codex_synth` rewritten: cleanup -> signature -> fast path ->
    parse outside lock (per-file cache reuse) -> tmp write + flush -> under
    lock: supersede check (a newer committed signature wins; older candidate
    discarded) -> `os.replace` -> commit cache/sig/last_ok. OSError in either
    stage deletes only own tmp, records `codex synth publish failed: ...` and
    returns the old path or raises when nothing usable exists. The old
    `except OSError: pass` around the publish is gone.
  - `router_events_path`: unpacks the new tuple (`path, _err`); behavior of
    `/api/router` unchanged (errors surface via `blank_router_stats(error)`
    through the existing handler catch).
  - `tests/test_router_outcomes.py`: 3 F4 call sites unpack the tuple and
    assert `synth_err is None` (lines 214-215, 326-327, 410-411).
- RED evidence (`artifacts/F5a-F6a-RED.windows.log`, exit 1): the final test
  bytes executed against the unpatched HEAD dashboard in a throwaway copy:
  Ran 14, FAILED failures=2 errors=8 — t01 "first failing publish must surface
  error" got a bare path; t03 no `RuntimeError`; 8 unpack ValueErrors
  (t02/t04/t05/t06, f6a-t01/t04/t06/t08); 4 guard tests already pass on old
  code (f6a-t02/t03/t05/t07). Genuine contract-level RED, not fixture noise.
- GREEN evidence:
  - `artifacts/F5a-F6a-GREEN.windows.log`: Ran 14 tests, OK (12.6 s).
  - `artifacts/F5a-F6a-suite.windows.log`: Ran 81 tests, OK (67 prior + 14;
    zero regressions; one pre-existing 401 ResourceWarning noted).
- Typesafe/Jev second-opinion gate (run BEFORE committing, on the real
  `git diff`; jev-1.13.0, DIFF_CHARS=9071, no truncation): `touches_local_path`
  0.09 (low — router/synth path only), `touches_router_path` 0.96,
  `silent_failure_removed` 0.84, `atomic_publish_present` 0.96. Consistent
  with the diff and tests.
- Matrix: F5a-T01..T06 + F6a-T01..T08 -> PASS (windows,
  `artifacts/F5a-F6a-GREEN.windows.log`); the placeholder file names were
  corrected to `tests/test_synth_publish.py`.
- Retries: 0 PATCHED cycles this run (patch inherited from the interrupted
  predecessor run, re-reviewed byte-level; RED/GREEN re-established from
  scratch). No report row faked; every claim above maps to a log.

## F5a-F6a-3. Not changed (verified)

- Local session-stats path untouched: F1-T10 `day_total == 165` green in the
  same 81-run; grep over the diff shows zero local-path edits.
- F1 router normalization and F2 auth model intact (their tests green in the
  same run); F4 outcome-unknown semantics intact (F4-T01..T10 green); `pl`
  and `rec` guards kept.
- No new `/api/router` payload keys; publish errors reach callers without
  changing response shape.
- Known remaining (honest): `query_router_stats` still counts lines by a
  separate open before the parsing open, so a reader can stat one generation
  and parse the next. Deliberately NOT fixed here (no F5a/F6a test forces it;
  §13/F6b reworks exactly those reads). `_cleanup_synth_tmps` runs without
  the lock by design (register-before-open ordering makes the scan safe
  under CPython). Thread-level scope only, as specified — no cross-process
  claim.

## F5a-F6a-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): all 131 canonical IDs present and 131
  core-mandatory marked; PASS evidence files exist; but `artifacts/TEST_REPORT.json`
  is still F1-era — F2+ rows missing and the report's code hash mismatches the
  computed `sha256:7bb1209e...` -> "GATE CORE FAILED (132 problems)". The
  F8-close step rebuilds `TEST_REPORT.json`; log in
  `artifacts/F5a-F6a-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Process hygiene: no servers
  started by this scope; suite servers bind `127.0.0.1:0` only; only own
  PIDs/threads handled; no repo files left dirty beyond the intended set.

# FIX_REPORT — F5b/F5c codex enumeration + rec guard (RED → GREEN)

## F5b-F5c-1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Start of this run: HEAD `8834c63` ("F5a/F6a FIX_REPORT + acceptance matrix").
  Worktree DIRTY at start: `M opencode_dashboard.py`,
  `M tests/test_codex_incremental.py`, `M tests/test_codex_publication.py`
  = the F5b/F5c patch + tests from an interrupted predecessor run; kept after
  byte-level review (contract matches §10/§11 exactly; local session-stats path
  untouched).
- Incident, disclosed for honesty: `tests/test_synth_publish.py` was reported
  deleted by an external actor mid-run (likely a foreign process in this
  workstation; no evidence it was this scope or Claude). Restored by hardlink;
  content proven byte-identical to HEAD: `git diff` empty for that path and
  filtered hash `fbfee71e4e24687d4fb4fd2b6d943348b0d3732c` ==
  `HEAD:tests/test_synth_publish.py`. It carries a cosmetic stat-dirty flag
  only; it is NOT part of this change and is not staged.
- Code hashes tested (current worktree bytes):
  - `opencode_dashboard.py`: `sha256:ffe64b48bb675c10c38999c7bc5ebd397e102c07a3b87a741784e41ca0576309`
  - `tests/test_codex_publication.py`: `sha256:65a8057164300c4678e52be3bf07c36dbb30d67bde7183b73ac22e9440198321`
  - `tests/test_codex_incremental.py`: `sha256:79665da6709735dfb3338146e51fd9c181383c7084825baae5fd3b2aabf2b57a`
- Environment: Windows, `python` = Python 3.14.3. Tests are pure unit/thread
  plus loopback HTTP; server tests bind `127.0.0.1:0` only (never
  8765/8766/8770); fixtures synthetic in temp dirs with isolated HOME.

## F5b-F5c-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- Contract (§10 F5b, §11 F5c, binding):
  - ONE shared enumeration of real Codex rollout files (`rollout-*.jsonl`,
    recursive under `CODEX_DIR`) used by diagnostics (`/api/signals`), session
    list (`/api/codex`) and synth publish — no third parallel rglob.
  - Honest distinct states: dir missing / unreadable / exists-but-empty /
    files present / partial read (skipped entries). A file vanishing between
    enumeration and `stat()` never aborts; skips are counted and surfaced.
    Foreign `.jsonl` without the rollout pattern never inflates counts; counts
    and newest-mtime come from the same set the parser reads; cache freshness
    stays signature-based (size/mtime) so new files are visible once present.
  - `_parse_codex_file`: after successful `json.loads`, a non-dict record
    increments a rejection counter and is skipped; the existing `pl` guard is
    kept untouched; malformed JSON keeps the previous silent skip; empty lines
    are not "invalid"; rejected contents are never logged; the counter is
    surfaced in diagnostic metadata (`invalid_records` aggregate; `_invalid`
    per cached row). Exactly one new guard, as bound ("F5c wyłącznie brakujący
    guard rec").
- Changed symbols in `opencode_dashboard.py`:
  - NEW: `CODEX_ROLLOUT_GLOB = "rollout-*.jsonl"`, `_codex_rollout_files()` ->
    `(files, state, skipped)` with `missing|unreadable|empty|partial|ok`, and
    `_codex_rollout_stats(files)` -> `(ok_count, newest_mtime, skipped)`.
  - `query_signals`: codex block rewritten on the shared enumeration; texts
    distinguish missing dir / unreadable dir / rollout files unreadable
    (skipped N) / empty / `N files, newest X.Xh ago` (+ ` (partial: N
    skipped)`); level `ok` only when fresh and nothing skipped; obsolete
    `import os as _os` alias dropped.
  - `_parse_codex_file`: row gains `_invalid` / `_err`; open OSError ->
    `_err = True` + return; non-dict parsed record -> `_invalid += 1;
    continue` placed after `json.loads` and before the first `rec.get`
    (`pl` guard untouched).
  - `query_codex`: uses the shared enumeration; stat-loop OSError counts as
    skipped; response adds additive diagnostics `invalid_records` (sum over
    cache) and `partial` (bool; skips + `_err` rows).
  - `ensure_codex_synth`: the same shared enumeration replaces its private
    try/rglob.
  - Tests: `tests/test_codex_publication.py` gains `TestF5bEnumeration`
    (t01..t05); `tests/test_codex_incremental.py` gains `TestF5cRecGuard`
    (t01..t05, incl. real `/api/codex` HTTP case); pre-existing skeleton tests
    kept.
- RED evidence (`artifacts/F5b-F5c-RED.windows.log`, exit 1): the new tests
  executed against the unpatched dashboard: `Ran 16, FAILED failures=9
  errors=1` — all 10 new tests red; t01 `AttributeError: 'list' object has no
  attribute 'get'`; t05 the handler swallowed the same error into `ok:false`
  (the actual flaw); F5b failures show old diagnostics reporting "Codex
  sessions dir empty" for a nested rollout and lacking `unreadable`/`partial`
  states. Genuine contract-level RED. Test-side fix during capture: the bearer
  secret must be >=20 chars (`secrets.token_urlsafe(32)`).
- GREEN evidence:
  - `artifacts/F5b-F5c-GREEN.windows.log`: target files verbose
    `Ran 16 tests, OK` (0.63 s) + three full-suite runs `Ran 91 tests, OK`
    (81 prior + 10 new; zero regressions).
  - `python -m py_compile opencode_dashboard.py` OK. Frontend untouched ->
    `node --check` N/A for this scope.
- Extra, disclosed outside this family's scope: F2-T09 close-race
  stabilization. `/api/close` popped the window AFTER sending `200`, so a
  client could still observe the window registered right after the response
  (thread-scheduling race). Discovered by GREEN verification as an
  intermittent full-suite failure; proven pre-existing on pristine HEAD
  `8834c63` (organic: 1/5 suite runs; controlled probe with widened switch
  interval: 9/150 hits) and absent after the fix (0/150). Fix: pop + "others"
  computation under `LOCK` BEFORE `_send(200)`; `_arm_close()` still runs
  after the send. No test was changed. Evidence pair in
  `artifacts/F2-T09-race-evidence.windows.log`. Reason folded into this commit:
  it blocks honest 91/91 evidence and is a 3-line ordering fix in the same
  file.
- Typesafe/Jev second-opinion gate (run BEFORE committing, on the real
  `git diff`; jev-1.13.0, DIFF_CHARS=25314, first 24000 sent):
  `touches_local_path` 0.07 (low — codex/router path only),
  `touches_codex_reader_path` 0.99, `shared_enumeration_present` 0.97,
  `diagnostics_states_distinguished` 0.97, `rec_guard_present` 0.98.
  Consistent with the diff and tests.
- Matrix: F5b-T01..T05 + F5c-T01..T05 -> PASS (windows,
  `artifacts/F5b-F5c-GREEN.windows.log`); placeholder file names replaced with
  the real test IDs.
- Retries: one RED-to-GREEN cycle for the patch itself (5 edits, single pass,
  no follow-up fixes); one test-side token-length fix during RED capture; no
  report row faked — every claim maps to a log.

## F5b-F5c-3. Not changed (verified)

- Local session-stats path untouched (Jev `touches_local_path` 0.07; F1/F3
  scopes); `day_total`/`dayTotal`/`outMerged` and local OpenCode SQL semantics
  untouched.
- `pl` payload guard untouched (F5c-T03 green proves it still protects);
  `_parse_codex_events` rec/pay validation untouched; `token_usage_record` and
  nested `item`/`usage` field semantics unchanged (F5c-T04 asserts pre/post
  equivalence; only the diagnostic counter is new).
- Malformed-JSON lines are still skipped without counting — deliberate minimal
  scope of §11 (binding: "wyłącznie brakujący guard rec").
- `/api/codex` response only gains additive diagnostic keys (`invalid_records`,
  `partial`); no shape break (F5c-T05, F5c-T04 green).
- Known remaining (honest): enumeration walk repeats per request (no new cache
  layer); evidence for this scope is Windows-only (Linux NOT_RUN by design);
  F5b's own freshness contract relies on the pre-existing signature check
  (verified by F5b-T05).

## F5b-F5c-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): all 131 canonical IDs present and 131
  core-mandatory marked; F5b/F5c now PASS in the matrix with existing evidence
  files; but `artifacts/TEST_REPORT.json` is still F1-era — F5b/F5c (and all
  F2..INT) result rows missing and report hash
  `sha256:ce11d08f...` != current `sha256:ffe64b48...` ->
  "GATE CORE FAILED (132 problem(s))" (same honest count as the F5a/F6a step;
  the report is rebuilt at the F8-close step). Log in
  `artifacts/F5b-F5c-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Process hygiene: all servers
  `127.0.0.1:0`; only own PIDs/threads handled; no repo files left dirty
  beyond the intended set (plus the cosmetic stat-dirty flag on the restored
  `tests/test_synth_publish.py`, content proven equal).

# FIX_REPORT — F3 session activity separation (RED -> GREEN)

## F3-1. Repo, base, tested SHA, worktree, environment

- Repo: `D:\TESTY!\V3\little-better-dashboard`, branch `fix/audit-f1-f8`,
  start HEAD `fa729c7` (F5b/F5c report-only commit).
- Worktree during test: three intended modified files — `opencode_dashboard.py`,
  `README.md`, `tests/test_local_regressions.py` (`git diff --stat`:
  178 insertions / 34 deletions) — plus the pre-existing cosmetic stat-dirty
  flag on `tests/test_synth_publish.py` (content proven equal, not touched).
- Tested code hashes (SHA-256): `opencode_dashboard.py`
  `sha256:ad5368d738f7f13c4e4c33a6a366f0ff15380f11a4271485327a35b433cb717c`,
  `tests/test_local_regressions.py`
  `sha256:28d97371676f84914222d53e3268cbb0cda27ff78fd760ab7b1257e9a45be8d8`,
  `README.md`
  `sha256:7e4409dd3bce4d920d945c1d6874e5cae47c08db445a101e421b3e2d7edd9c19`.
- Environment: Windows, Python 3.14.3 via `python`. All test servers bound
  `127.0.0.1:0` only; never 8765/8766/8770 (the user's live dashboard on 8770
  was not touched). Fixtures: per-test temp dirs with isolated
  HOME/USERPROFILE/LOCALAPPDATA; tests close their own servers/threads.

## F3-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

Contract: spec §7 (`F3: aktywnosc historyczna nie jest stanem wykonania agenta`).

- REPRODUCED (RED): new evidence tests
  `tests/test_local_regressions.py::TestF3ActivityStates` (6 cases, T01..T06)
  -> `artifacts/F3-RED.windows.log`: `Ran 9 tests ... FAILED (failures=2,
  errors=4)`; the 3 skeleton `TestLocalRegressions` tests stayed green.
  Concrete old-code failures: T01/T04 `KeyError: 'activity_state'`; T02
  `TypeError: unsupported operand type(s) for -: 'int' and 'str'`
  (old `opencode_dashboard.py:472`); T03 `'bulk-000' != 'veteran'` (old
  ORDER BY time_created); T05 `KeyError: 'runtime_status'`; T06 source scan
  found no activity labels.
- PATCHED (single pass, no follow-up fixes):
  - Backend `query_agents`: `activity_state` in recent/quiet/stale/unknown
    with exact boundaries (0..120 s recent, >120..900 s quiet, >900 s stale;
    missing/invalid/zero/future timestamps -> unknown, never clamped to
    recent); `runtime_status` kept `'unknown'`; `relation: 'child_session'`;
    SQL `ORDER BY s.time_updated DESC, s.id DESC LIMIT 100` with the old
    Python re-sort by `time_created` removed; new counters `listed_count`,
    `total_child_sessions`, `limit`, `truncated`, `activity_counts`;
    `status_counts`/`running_sec`/`idle_sec` removed.
  - `/api/agents` error fallback extended with the new keys (0/100/False/zeros).
  - Frontend: exact labels `Recent activity` / `No recent activity` /
    `Older activity` / `Unknown` + adjacent `Based on session updates; not
    execution status`; dot classes `.dot.recent/.dot.quiet/.dot.stale/
    .dot.unknown`; pulsing green live dot and `running NOW` hint removed;
    expanded row shows `Activity:` instead of `Status:`; table headers
    `Activity` / `Child session`.
  - README: "What it shows" now says session activity (recent, no recent
    activity, older or unknown — based on session updates, not execution
    status; auto-refreshed); Notes bullet rewritten (recency boundaries,
    unknown case, not execution status, parent_id = session relation).
  - Test-side: none needed after RED capture (all RED failures were product
    defects the contract demands fixing).
- VERIFIED (GREEN): `python -m py_compile opencode_dashboard.py` OK;
  `python -m unittest tests.test_local_regressions -v` -> `Ran 9 tests` `OK`;
  full suite `python -m unittest discover -s tests` -> `Ran 97 tests` `OK`
  (was 91; +6 new). Evidence log `artifacts/F3-GREEN.windows.log`
  (target-file verbose run + full discover). Skeleton
  `TestLocalRegressions` tests untouched and still green.
- Jev gate (before commit, on `git diff` of `opencode_dashboard.py` +
  `tests/test_local_regressions.py` + `README.md`; jev-1.13.0,
  DIFF_CHARS=22317): `touches_local_path` 0.12 (low — required),
  `activity_state_separation` 0.99, `boundary_contract_exact` 0.96,
  `ui_language_updated` 0.96, `limit_counters_present` 0.96.
- Matrix: F3-T01..T06 -> PASS (windows,
  `artifacts/F3-GREEN.windows.log`); placeholder names replaced with real
  test IDs.
- `node --check` not applicable: no separate frontend file changed (the JS
  lives inside `opencode_dashboard.py`); the embedded JS is covered by the
  source-scan test F3-T06.
- Retries: one RED-to-GREEN cycle (single patch pass); no report row faked —
  every claim maps to a log.

## F3-3. Not changed (verified)

- Local session-stats token path untouched (Jev `touches_local_path` 0.12):
  `day_total`/`dayTotal`/`outMerged`, token SUM SQL, cache-rate and KPI
  derivation unchanged; skeleton regression tests still assert them.
- Session relation tree untouched: `parent_id` semantics and joins unchanged;
  no session deletion, no agent restart/kill, no new worker-monitoring
  promise; the view is described as child & related sessions.
- View limit stays 100 (not raised); internal `child_runs`/`child_agents`
  names kept for compatibility; config tiles (`subagents | click to expand`)
  untouched.
- Known remaining (honest): activity horizon constants (120/900 s) remain
  fixed in code; Linux evidence NOT_RUN by design (matrix rows env
  `windows`); age is recomputed per request from `time_updated` (no caching
  freeze) — covered by F3-T04.
- F2-T09 close-window race fix from the F5b/F5c step stays in place; the F2
  matrix row is unchanged (test untouched, still green).

## F3-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): all 131 canonical IDs present in the matrix and
  131 core-mandatory marked; F3 now PASS with existing evidence files; but
  `artifacts/TEST_REPORT.json` is still F1-era (F2..INT result rows missing,
  report hash `sha256:ce11d08f...` != current `sha256:ad5368d7...`) ->
  "GATE CORE FAILED (132 problem(s))" (same honest count as the F5a/F6a and
  F5b/F5c steps; the report is rebuilt at the F8-close step).
  Log: `artifacts/F3-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Servers `127.0.0.1:0` only;
  only own PIDs/threads handled; no repo files left dirty beyond the intended
  set.

# FIX_REPORT — F7 commit-window attribution honesty (RED -> GREEN)

## F7-1. Repo, base, tested SHA, worktree, environment

- Repo: `V3/little-better-dashboard`, branch `fix/audit-f1-f8`.
- Base for this step: HEAD `17da6a6` ("F3 FIX_REPORT + acceptance matrix
  (report-only)"); F3 code commit `25e76d0` precedes it.
- Worktree during testing (3 files, uncommitted until the F7 commits):
  `README.md`, `opencode_dashboard.py`,
  `tests/test_commit_attribution.py` (new, intent-to-add for diffing).
  `tests/test_synth_publish.py` carries only a cosmetic stat-dirty flag
  (content proven equal to HEAD; never staged).
- Tested SHA-256:
  - `opencode_dashboard.py` =
    `sha256:f36940bc43a0f9ca0a72e6c30b7137e7fe8df623c86834ffaa631b982643c787`
  - `tests/test_commit_attribution.py` =
    `sha256:41a3a690f2b7573dab0969eb6fbf92f6303b4a89c64d1dcbd128dd7ff03306b8`
  - `README.md` =
    `sha256:8604d252a38d9bbe1560e670773104e32a2ed2290ad14001a9956a9e74174fd7`
- Environment: Windows, Python 3.14.3 (`python`); test servers bind
  `127.0.0.1` port `0` only (never 8765/8766/8770); fixtures are temp git
  repos under isolated `HOME`/`USERPROFILE`/`LOCALAPPDATA`.

## F7-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- Contract (spec §18): commit rows must present usage "in the 24h before this
  commit" as an overlapping global window, never as commit cost; warnings must
  be visible in UI, tooltip and export; no cwd/project inference.
- RED: `python -m unittest tests.test_commit_attribution -v` ->
  Ran 6, FAILED (failures=6) — all six F7 tests failed pre-patch (missing
  `commit_windows` metadata, old "prior 24h" labels, no visible warnings, no
  commit CSV). Log: `artifacts/F7-RED.windows.log`.
- PATCHED (single pass, 15 edits in `opencode_dashboard.py` + README):
  local and Codex commit panels renamed to `Usage in the 24h before each
  commit`; visible hints added directly at both charts — Codex:
  `Global time window; may include other projects. Windows overlap. Rows must
  not be summed.`; local: `Project-matched time window; windows overlap; rows
  must not be summed; not commit cost.`; tooltip label
  `Usage in the 24h before commit` (+ Scope row on the Codex tooltip); local
  insight reworded to `Most usage before a commit: ..., N tokens in the 24h
  window (not commit cost).`; payloads gained `commit_windows` metadata
  (`method: time_window`, `window_hours: 24`, `scope: global` for router and
  blank stats / `project_time_window` for local, `overlap_possible: true`,
  `additive: false`, note = the visible sentence); export menu gained
  `CSV (commit windows · not additive)` writing
  `codex-tokens-commits.csv` / `opencode-tokens-commits.csv` with explicit
  `method/scope/overlap_possible/additive/window_hours/note` columns; README
  Notes gained the inclusive-both-ends + global-window caveat.
- Fixture note: the test helper initially declared a constant
  `inputTokens=100/outputTokens=20` per event while the dashboard derives the
  effective total as `input + output` (declared `totalTokens` is only
  cross-checked via `total_conflicts`); fixed `_events` to emit per-row
  consistent component fields — one RED-to-GREEN cycle.
- VERIFIED: `python -m unittest tests.test_commit_attribution -v` -> Ran 6,
  OK; full `python -m unittest discover -s tests` -> Ran 103, OK (97 + 6).
  Log: `artifacts/F7-GREEN.windows.log`. `py_compile` OK. `node --check` N/A
  (no frontend files; the embedded JS is covered by the source assertions of
  F7-T05/F7-T06).
- Trust gate (typesafe Jev `jev-1.13.0`, diff 19455 chars, pre-commit):
  `touches_local_path` 0.44 -> 0.53 -> 0.19 after narrowing the question to
  the SQL/arithmetic producing local token totals (earlier rounds conflated
  local-tab UI wording with accounting); `commit_window_meta` 0.97;
  `boundaries_and_tz` 0.96; `ui_and_export_warnings` 0.93;
  `no_inferred_attribution` 0.96. All rounds reported honestly.
- Matrix: F7-T01..T06 rows updated to the real test IDs with env `windows`,
  PASS, evidence `artifacts/F7-GREEN.windows.log`.

## F7-3. Not changed (verified)

- No cwd/project filtering implemented now (spec): the Codex window stays
  global; a commit never claims precise project attribution; no
  title/folder/subsystem inference anywhere in the router commit block
  (asserted by F7-T06).
- 24h window semantics kept as before; boundaries documented and tested as
  inclusive on both ends (F7-T03); timestamps normalized via
  `_router_time_key` so equal instants with different offsets match (F7-T04).
- Commit rows are never summed to global cost: global totals come from source
  events (F7-T01 proves 120 vs 240 in overlapping windows).
- Local accounting untouched (`day_total`/`dayTotal`/`outMerged`, SQL sums,
  cache-rate): Jev `touches_local_path` 0.19 with the narrowed question;
  skeleton regression tests still pass.
- `equal-split` file description kept honest (not a measure of edit cost);
  commit history untouched (no deletion/rewrite).
- Known remaining (honest): rows still expose only the 24h window intent, not
  a per-project adapter (`cwd` absent upstream), per spec.

## F7-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): all 131 canonical IDs present and 131
  core-mandatory marked; PASS evidence files exist; but
  `artifacts/TEST_REPORT.json` is still F1-era (F2..INT rows missing, report
  hash `sha256:ce11d08f...` != current `sha256:f36940bc...`) ->
  "GATE CORE FAILED (132 problem(s))" (same honest count as the prior steps;
  the report is rebuilt at the F8-close step).
  Log: `artifacts/F7-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Servers `127.0.0.1:0` only;
  only own PIDs/threads handled; no repo files left dirty beyond the intended
  set.

# FIX_REPORT — F6b bounded router tail, line-count cache (RED -> GREEN)

## F6b-1. Repo, base, tested SHA, worktree, environment

- Repo: `D:\TESTY!\V3\little-better-dashboard`, branch `fix/audit-f1-f8`;
  start HEAD `f36e26a` (F7 reports; F7 code `b911b11`).
- Worktree: 2 files — `opencode_dashboard.py` (modified),
  `tests/test_router_events.py` (new, untracked). A cosmetic stat-dirty flag
  on `tests/test_synth_publish.py` (external deletion incident, content
  proven equal) is never staged.
- Tested hashes: `opencode_dashboard.py`
  `sha256:9e61ca69a7f732ef6d4e22859ff2c3f7aef51272e4286e7f020e195b37988b3e`;
  `tests/test_router_events.py`
  `sha256:c5c632cea66bad5fdbe179aa05eb158095eb0164f51f06999180e01b505e2de1`.
- Environment: Windows, `python` 3.14.3. Test servers `127.0.0.1:0` only
  (never 8765/8766/8770); fixtures in temp dirs with isolated
  HOME/USERPROFILE/LOCALAPPDATA.

## F6b-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- Contract: spec §13 — bounded tail, full synthesis history, no
  `readlines()` of the whole file.
- REPRODUCED (RED): `artifacts/F6b-RED.windows.log` —
  `python -m unittest tests.test_router_events -v` -> Ran 8, FAILED
  (failures=1, errors=7).
- PATCHED (single pass): constants `MAX_ROUTER_RECORD_BYTES = 8 MiB`,
  `_ROUTER_READ_BLOCK = 65536`, `_ROUTER_COUNT_CACHE = {}`; helpers
  `_router_count_lines` (fingerprint size/mtime/ino; warm reuse; incremental
  append; cold recount on truncate/rotation; never `readlines`),
  `_router_read_tail` (backward byte-block reads; last N complete lines;
  leading fragment dropped as `partial_head`; per-line byte assembly before
  utf-8 decode; CRLF strip), `_router_stream_lines` (streaming full read;
  `_OVERLONG` sentinel with discard-to-newline), plus `_Unterminated` /
  `_OverlongLine` markers. `parse_router_events` returns `(events, problems)`
  with new problems keys `candidate_lines`, `valid_records`,
  `oversize_records`, `pending_tail_line`, `partial_head`, `window_mode`
  (`full` | `physical_tail`), `lines_total`, `count_cached` (kept in problems
  only — see note). `query_router_stats` parses first, `total_lines` comes
  from the cached counter, `truncated = (not full_scan) and window_mode ==
  "physical_tail"`; payload adds `window_mode`, `candidate_lines`,
  `pending_tail_line`, `oversize_records`; `blank_router_stats` mirrors the
  keys zeroed. `full_scan=True` streams the whole file (no `readlines`, no
  silent 30 000 cap). Test-side fixes during GREEN: t01/t08 got the missing
  `MAX_ROUTER_EVENTS` patches; t05 cap raised to 600 bytes (record ~268 B);
  t08 `_ISO` switched to naive local timestamps (the `hour` field is local
  time). One payload key was removed during verification: `count_cached`
  (kept in `problems`) because it flipped between calls on an unchanged file
  and broke the F6a-T07 no-shared-payload-mutation test.
- VERIFIED: `python -m unittest tests.test_router_events -v` -> Ran 8, OK;
  `python -m unittest discover -s tests` -> Ran 111, OK (30.3 s);
  `py_compile` OK; `node --check` N/A (JS embedded; covered by source-scan
  assertions). Logs: `artifacts/F6b-GREEN.windows.log`. Jev gate
  (jev-1.13.0, diff 12 468 chars): round 1 `touches_local_path` 0.35 /
  `bounded_tail_no_readlines` 0.96 / `count_cache_honest_metadata` 0.94 /
  `oversize_and_pending_reported` 0.92; round 2 (changed-lines-only wording)
  `touches_local_path` 0.21 LOW, others 0.95/0.94/0.92.
- Matrix: F6b-T01..T08 rows -> real test IDs, env `windows`, PASS, evidence
  `artifacts/F6b-GREEN.windows.log`.
- Retries: one RED-to-GREEN cycle; one payload-key fix (`count_cached`);
  three test-side fixture fixes.

## F6b-3. Not changed (verified)

- Local session-stats path untouched: `day_total`/`dayTotal`/`outMerged`,
  token SUM SQL, cache-rate math unchanged (Jev 0.21 changed-lines-only;
  F1/F3/F4 suites unchanged). A `LOCAL_CACHE` line appears only as diff
  context in the constants hunk; no local code line changed.
- Small-file behavior identical: `scanned == len(events)`, same
  `total_lines`/`truncated` values (F1/F4/F7 suites unchanged).
- Synthesis history not cut (no 30 000 cap in full scan); recent table still
  capped by `MAX_ROUTER_ROWS` and sorted by parsed time.
- Commit attribution (F7) untouched; no new dependencies (stdlib only).

## F6b-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): `artifacts/TEST_REPORT.json` is still F1-era
  (F2..INT rows missing; report hash `sha256:ce11d08f...` != current
  dashboard hash) -> same honest "GATE CORE FAILED (132 problem(s))" count as
  the prior steps; the report is rebuilt at the F8-close step.
  Log: `artifacts/F6b-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Servers `127.0.0.1:0` only;
  only own PIDs/threads handled; no repo files left dirty beyond the intended
  set.

# FIX_REPORT — F6c incremental Codex read, per-file fingerprints, restart checkpoint (RED -> GREEN)

## F6c-1. Repo, base, tested SHA, worktree, environment

- Repo: `D:\TESTY!\V3\little-better-dashboard`, branch `fix/audit-f1-f8`;
  start HEAD `b791d5c` (F6b reports; F6b code `51b14f4`).
- Worktree: 4 files — `opencode_dashboard.py` (modified),
  `tests/test_codex_incremental.py` (modified), `.gitignore` (modified),
  `docs/data-contracts.md` (modified). A cosmetic stat-dirty flag on
  `tests/test_synth_publish.py` (external deletion incident, content proven
  equal) is never staged.
- Tested hashes: `opencode_dashboard.py`
  `sha256:b62e53e8b5f1b9575979dc6ea9fb47d4da5b38ff031b4cffcc397ea9b8592bf2`;
  `tests/test_codex_incremental.py`
  `sha256:702e0049c4cf5b40f35fbf43af05f2d1a3f82375090a0c8510b87ff9f830ab0f`.
- Environment: Windows, `python` 3.14.3. Test servers `127.0.0.1:0` only
  (never 8765/8766/8770); fixtures in temp dirs with isolated
  HOME/USERPROFILE/LOCALAPPDATA.

## F6c-2. Change (TODO -> REPRODUCED -> PATCHED -> VERIFIED -> DONE)

- Contract: spec §14 — incremental Codex read (reuse per-file offsets across
  refreshes), non-additive per-file fingerprints, restart checkpoint; local
  accounting untouched.
- REPRODUCED (RED): `artifacts/F6c-RED.windows.log` —
  `python -m unittest tests.test_codex_incremental -v` -> Ran 18, FAILED
  (failures=6, errors=4).
- PATCHED (three passes): constants `CODEX_PARSER_VERSION = 1`,
  `CODEX_CACHE_DIR = <repo>/.cache`, `CODEX_INDEX = codex_index.json`,
  `_codex_file_state = {}`, `_codex_ev_state = {}`, `_codex_index_note = None`,
  `_CODEX_ANCHOR_LEN = 64`; `_codex_consume` keeps original per-line semantics
  (unparseable lines still consume the file); `_codex_files_sig` is
  deliberately non-additive — sorted `(path, size, mtime_ns, ino)` hashed with
  schema + version so pure appends still change identity; `_codex_split`
  splits bytes into complete lines only (no trailing partial);
  `_codex_read_since` seeks to the stored offset and reads only the delta,
  comparing a 64-byte anchor tail stored per file; rebuilds when size
  regresses, inode changes, `mtime_ns` moves backwards, or (same-size guard)
  the fingerprint changed while the offset did not; rebuild cannot undercount.
  Per-file state `{fp, offset, (row), tail}` and per-(model, provider) event
  state `{fp, offset, model, provider, events, tail}` memoize parsed output.
  `_write_codex_index` atomically writes `.cache/codex_index.json`
  `{version, schema, generation, files{path:{size, mtime_ns, ino, offset,
  model, provider, tail (hex), events}}}`; `_load_codex_index(files)` validates
  version/schema/generation and adopts matching entries wholesale (never
  partially), rejecting missing/corrupt/mismatched files (t09). `query_codex`
  includes a session row when `row.get("n") or row.get("tok")` so token-only
  sessions still surface. `ensure_codex_synth` loads the checkpoint after
  rollout enumeration, fills `rows[key] = (size, mtime, _parse_codex_events(p))`
  and writes the index before the success return (a failed checkpoint write
  never fails publish). `_synth_sig_of` now uses
  `_codex_files_sig(files, schema=SYNTH_SCHEMA)`. `docs/data-contracts.md`
  gained "## Codex read cache and restart checkpoint" (derived cache, tied to
  generation sha256 of `codex_router_events.jsonl`, rejected wholesale when
  missing/corrupt/mismatched, safe to delete, never source of truth).
  `.gitignore` gained `artifacts/`, `checkpoints/`, `.cache/` (plus the
  earlier `__pycache__/`, `*.py[cod]`, `*.log`, `codex_router_events.jsonl`).
  Test-side fix during GREEN: t10 originally used an equal-length in-place
  rewrite buried inside constant trailer boilerplate, which the 64-byte
  anchor window could not see; the fixture now performs equal-length rewrites
  with a distinguishing byte inside the anchor-visible region.
- VERIFIED: `python -m unittest tests.test_codex_incremental -v` -> Ran 18,
  OK; `python -m unittest discover -s tests` -> Ran 121, OK (112.7 s);
  `py_compile` OK; `node --check` N/A (JS embedded; covered by source-scan
  assertions). Logs: `artifacts/F6c-GREEN.windows.log`. Jev gate (jev-1.13.0,
  diff 38 762 chars): `touches_local_path` 0.19 LOW /
  `incremental_offsets_correct` 0.78 /
  `fingerprint_not_additive` 0.97 / `checkpoint_never_truth` 0.95.
- Matrix: F6c-T01..T10 rows -> real test IDs, env `windows`, PASS, evidence
  `artifacts/F6c-GREEN.windows.log`.
- Retries: one RED-to-GREEN cycle with three patch passes; one test-side
  fixture fix (t10 anchor visibility).

## F6c-3. Not changed (verified)

- Local session-stats path untouched: `day_total`/`dayTotal`/`outMerged`,
  token SUM SQL, cache-rate math unchanged (Jev `touches_local_path` 0.19
  LOW, changed-lines-only wording; F1/F3/F4 suites unchanged).
- Session output keys and rounding unchanged; the only behavioral difference
  is the deliberate token-only session inclusion in `query_codex`
  (`row.get("n") or row.get("tok")`), covered by the existing suite.
- Checkpoint file is a derived cache: never source of truth, never blocks
  publish (write failure swallowed), rejected wholesale on any mismatch;
  deleting `.cache/` only costs a cold rebuild.
- No new dependencies (stdlib only); no schema or payload breakage —
  full discovery suite 121 tests green.

## F6c-4. Verify gate outcome

- `python tools/verify_acceptance.py --gate core --report artifacts/TEST_REPORT.json`
  -> FAIL (expected/honest): `artifacts/TEST_REPORT.json` is still F1-era
  (F2..INT rows missing; report hash `sha256:ce11d08f...` != current
  dashboard hash) -> same honest "GATE CORE FAILED (132 problem(s))" count as
  the prior steps; the report is rebuilt at the F8-close step.
  Log: `artifacts/F6c-verify-core.windows.log`.
- Remote operations: none (no push/merge/fetch). Servers `127.0.0.1:0` only;
  only own PIDs/threads handled; no repo files left dirty beyond the intended
  set.
