"""Record the real acceptance execution (audit A03).

Runs the acceptance commands for real and stores, per command: the actual
exit code, duration, start/finish timestamps and output tails - plus an
identity block hashing every file that influences the run (runtime, tests,
tools, matrix, this runner).

`tools/build_test_report.py` refuses to build a report unless the records
match the current files, and `tools/verify_acceptance.py` rule (10)
re-checks the same identity at gate time. A report therefore can never
carry fresh hashes on top of stale executions.

The browser suite is intentionally not part of this recorder: it needs a
working browser and is executed by tools/run_browser_tests.py, whose logs
carry their own timestamps.

Usage:
    python tools/run_acceptance_records.py
    python tools/run_acceptance_records.py --only py_compile,unittest_discover
"""
import argparse
import datetime
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

COMMANDS = [
    ("import_check",
     ["python", "-c", "import opencode_dashboard"]),
    ("py_compile",
     ["python", "-m", "py_compile", "opencode_dashboard.py"]),
    ("unittest_discover",
     ["python", "-m", "unittest", "discover", "-s", "tests",
      "-p", "test_*.py"]),
    ("publication_checks",
     ["python", "-m", "unittest", "tests.test_publication_checks"]),
    ("integration",
     ["python", "-m", "unittest", "tests.test_integration"]),
    ("browser_f6d",
     ["python", "tools/run_browser_tests.py", "--log-prefix", "F6d-browser",
      "--cases", "F6d-T01,F6d-T02,F6d-T04,F6d-T06,F6d-T09"]),
    ("browser_f8",
     ["python", "tools/run_browser_tests.py", "--log-prefix", "F8-browser"]),
    ("benchmark",
     ["python", "tools/benchmark_dashboard.py", "--scenario", "ci"]),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_sha256(path):
    # EOL-normalised identity: the package ships raw git blob bytes (LF) while
    # an autocrlf worktree may hold CRLF for the same committed file; the
    # identity must bind the file content, not the checkout style.
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def identity():
    def group(pattern):
        out = {}
        for p in sorted(REPO_ROOT.glob(pattern)):
            if p.is_file():
                out[str(p.relative_to(REPO_ROOT)).replace("\\", "/")] = \
                    norm_sha256(p)
        return out

    return {
        "runtime_sha256": sha256(REPO_ROOT / "opencode_dashboard.py"),
        "tests": group("tests/*.py"),
        "tools": group("tools/*.py"),
        "matrix_sha256": norm_sha256(
            REPO_ROOT / "docs" / "ACCEPTANCE_MATRIX.md"),
        "runner_sha256": sha256(Path(__file__)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out",
                    default=str(REPO_ROOT / "artifacts"
                                / "execution-records.json"))
    ap.add_argument("--only", default="",
                    help="comma-separated subset of command names to run")
    args = ap.parse_args(argv)
    only = {part.strip() for part in args.only.split(",") if part.strip()}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    all_ok = True
    for name, argv_list in COMMANDS:
        if only and name not in only:
            continue
        started = datetime.datetime.now().isoformat(timespec="seconds")
        t0 = time.time()
        cp = subprocess.run([sys.executable] + argv_list[1:],
                            cwd=str(REPO_ROOT), capture_output=True,
                            text=True, timeout=3600)
        seconds = round(time.time() - t0, 3)
        finished = datetime.datetime.now().isoformat(timespec="seconds")
        records.append({
            "name": name,
            "command": " ".join(argv_list),
            "exit_code": cp.returncode,
            "seconds": seconds,
            "started_at": started,
            "finished_at": finished,
            "stdout_tail": (cp.stdout or "")[-1500:],
            "stderr_tail": (cp.stderr or "")[-800:],
        })
        print("[%s] exit=%d %.1fs" % (name, cp.returncode, seconds),
              flush=True)
        all_ok = all_ok and cp.returncode == 0
    doc = {
        "schema": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "environment": {
            "os": "windows" if sys.platform.startswith("win") else "linux",
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "identity": identity(),
        "commands": records,
    }
    out_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print("wrote %s (%d command(s), all_ok=%s)"
          % (out_path, len(records), all_ok))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
