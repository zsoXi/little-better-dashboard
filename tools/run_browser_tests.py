"""Stub browser-test runner (honest skeleton).

Policy: never fake a pass, never install anything.
If no usable browser automation is importable (Playwright), every
browser case is reported as NOT_RUN with a reason and the process
exits with code 2. Even when Playwright is importable, this skeleton
has no browser scenarios implemented, so it still reports
NOT_IMPLEMENTED / NOT_RUN and exits 2.

Usage:
    py -3.14 tools/run_browser_tests.py --help
    py -3.14 tools/run_browser_tests.py --list
    py -3.14 tools/run_browser_tests.py
"""
import argparse
import sys

# Skeleton browser-case IDs (acceptance IDs requiring a real browser).
BROWSER_CASES = [
    "F7-T01",
    "F7-T02",
    "F7-T03",
    "F7-T04",
    "F7-T05",
    "F7-T06",
]


def _check_playwright():
    try:
        import playwright  # noqa: F401
        return True, "playwright importable"
    except Exception as e:
        return False, f"playwright not importable ({e})"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Stub browser-test runner: reports NOT_RUN until browser scenarios exist."
    )
    parser.add_argument("--list", action="store_true", help="list known browser case IDs and exit")
    parser.add_argument(
        "--json", action="store_true", help="emit NOT_RUN report as JSON lines"
    )
    args = parser.parse_args(argv)

    if args.list:
        for case in BROWSER_CASES:
            print(case)
        return 0

    ok, reason = _check_playwright()
    if args.json:
        import json
        for case in BROWSER_CASES:
            print(json.dumps({"acceptance_id": case, "result": "NOT_RUN", "reason": reason}))
    else:
        print(f"browser check: {reason}")
        for case in BROWSER_CASES:
            print(f"{case}: NOT_RUN ({reason})")
        if ok:
            print("NOT_IMPLEMENTED: playwright is importable but no browser scenarios exist yet.")
        else:
            print("NOT_RUN: no usable browser; refusing to fake a pass. Nothing installed.")
    # Never exit 0 from the skeleton: no browser evidence was collected.
    return 2


if __name__ == "__main__":
    sys.exit(main())
