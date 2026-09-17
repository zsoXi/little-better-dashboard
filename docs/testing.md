# Testing

All tests are standard-library `unittest` suites and use synthetic fixtures
with an isolated HOME/USERPROFILE/LOCALAPPDATA. HTTP tests bind `127.0.0.1`
on ephemeral ports (port 0) only and clean up their servers, threads, timers
and temp directories even on failure. The browser runner is the only dev
dependency; the dashboard itself never needs it.

## Backend suites (Windows: use the installed interpreter, e.g. `py -3.14`)

```powershell
python -m py_compile opencode_dashboard.py
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest tests.test_integration -v
```

## Acceptance report and gates

```powershell
python tools/build_test_report.py --out artifacts/TEST_REPORT.json
python tools/verify_acceptance.py --report artifacts/TEST_REPORT.json --gate core
python tools/verify_acceptance.py --report artifacts/TEST_REPORT.json --gate release
```

`--require` is an alias for `--gate`. The `core` gate verifies the locally
available mandatory set; `release` additionally requires the real CI jobs and
keeps failing with a single `CI_PENDING` entry until the GitHub workflow has
actually run (a workflow file alone never passes a gate).

## Browser tests (dev-only; Playwright)

Install the pinned dev dependency into an isolated venv (never a runtime
dependency):

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # Windows
.venv/bin/pip install -r requirements-dev.txt       # POSIX
```

`requirements-dev.txt` pins `playwright==1.62.0`. Browser provisioning:

- Windows with Edge installed: the runner uses `channel="msedge"` headless.
- Otherwise: `python -m playwright install chromium` (add `--with-deps` on
  Linux CI).

```powershell
python tools/run_browser_tests.py --list
python tools/run_browser_tests.py --log-prefix F6d-browser --cases F6d-T01,F6d-T02,F6d-T04,F6d-T06,F6d-T09
python tools/run_browser_tests.py --log-prefix F8-browser
```

The runner starts the real dashboard server on synthetic sources in-process
and drives a real browser; screenshots and JSON/log evidence land in
`artifacts/` (gitignored). Without a usable browser every case is reported
NOT_RUN and the process exits 2 - it never fakes a pass. CI runs the same
runner headless on Ubuntu (the `browser` job in
`.github/workflows/tests.yml`); real CI execution is still pending
(CI_PENDING) because nothing was pushed.

## Benchmarks

```powershell
python tools/benchmark_dashboard.py --scenario ci      # deterministic small (10k) + large (100k)
python tools/benchmark_dashboard.py --scenario small --repeats 5
python tools/benchmark_dashboard.py --scenario ci --baseline-file path\to\df23258_opencode_dashboard.py
```

The runner keeps two measurement paths apart: `/api/router` driven by a
synthetic router ledger and `/api/router` driven by the built-in Codex
synthesizer (both runtime versions contain a synthesizer; the historical one
has no checkpoint and no incremental read). Fixtures are deterministic (fixed
seed 20260917); the baseline runtime comes from
`git show df23258:opencode_dashboard.py` unless `--baseline-file` points at a
copy - the review package ships that unchanged baseline under
`evidence/performance/baseline/` so the benchmark is reproducible without the
private repository. Ordinary timing stages run at least five repetitions and
report observations, median and maximum (no p95 from five samples). Read
bytes are classified into raw source, derived index and checkpoint scopes, so
every "0 bytes" figure has an explicit meaning; restart is a real new
process, memory is `tracemalloc` tracked allocations (not RSS), rotation and
append totals are checked against an independent oracle. Raw samples and the
summary land in `artifacts/performance/`; the per-ID evidence logs are
`artifacts/PERF-T01..T06.windows.log`. No speed promises are made - the
acceptance is bounded reads, explicit scopes and correctness.
