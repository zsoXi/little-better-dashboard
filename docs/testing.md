# Testing (skeleton)

All tests use `unittest` from the standard library only. No new runtime
or dev dependencies are installed. `requirements-dev.txt` stays
comment-only (Playwright is allowed as a dev-only dependency later, but
do not install anything for the skeleton).

## Windows commands (exact)

Use `py -3.14` on Windows. Do not assume a bare `python` on PATH.

```bat
py -3.14 -m py_compile opencode_dashboard.py
py -3.14 -m unittest discover -s tests -p "test_*.py"
py -3.14 tools/verify_acceptance.py --help
py -3.14 tools/run_browser_tests.py --list
py -3.14 tools/benchmark_dashboard.py --scenario ci
```

## How to run the skeleton

1. `py -3.14 -m py_compile opencode_dashboard.py` must still pass
   (the dashboard file itself is untouched).
2. `py -3.14 -m unittest discover -s tests -p "test_*.py"` runs the
   skeleton suites. Each `test_*.py` contains a discovery smoke test
   plus at least one real pure-helper check that imports from
   `opencode_dashboard.py` without starting a server. Some tests may
   pass; none fake results.
3. Fixtures are synthetic only: every test builds its files in
   `tempfile.TemporaryDirectory` with an isolated `HOME` /
   `USERPROFILE` / `LOCALAPPDATA` (saved in `setUp`, restored in
   `tearDown`). Tests never read the real user HOME, never touch
   `D:\TESTY!\Dashboard`, and never bind fixed ports (HTTP helpers use
   `127.0.0.1` with port `0` and clean up servers/threads/dirs via
   `tearDown`/`addCleanup`).
4. `py -3.14 tools/verify_acceptance.py --help` explains gate checks.
   Until a real `TEST_REPORT.json` with PASS rows, existing evidence
   files, and a matching code hash exists, every gate fails (honest
   `NOT_RUN`, non-zero exit).

## Browser-test policy

Browser tests require a real, usable browser. The stub runner
`tools/run_browser_tests.py` checks for an importable Playwright and
otherwise marks every browser case `NOT_RUN` with a reason and exits
`2`. It never fakes a pass and never installs anything. Actual CI runs
are `CI_PENDING` until a workflow run with evidence exists; workflow
file presence alone is not execution (see check 6 in
`tools/verify_acceptance.py`).

## Benchmark policy

`tools/benchmark_dashboard.py --scenario ci` reports `NOT_IMPLEMENTED`
and exits `2` until benchmark scenarios exist. No numbers are
fabricated.
