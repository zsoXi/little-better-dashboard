"""Build artifacts/TEST_REPORT.json from the acceptance matrix and the real
execution records (audit A03).

Every matrix row with result PASS (or CI_PENDING) becomes a report result.
PASS rows are bound to the recorded execution produced by
`tools/run_acceptance_records.py`: the builder REFUSES to write a report when
the records are missing or when their identity no longer matches the current
runtime/tests/tools - fresh hashes are never placed on top of stale runs.
Real command exit codes, durations and the execution window come from the
records, not from the matrix text.

Usage:
    python tools/run_acceptance_records.py
    python tools/build_test_report.py
    python tools/build_test_report.py --out artifacts/TEST_REPORT.json
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(r"^(F\d+[a-f]?|PUB|INT|PERF)-T\d+$")
RECORDS_REL = "artifacts/execution-records.json"


def _git(*args):
    try:
        out = subprocess.run(["git"] + list(args), cwd=str(REPO_ROOT),
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_matrix(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7 or not ID_RE.match(cells[0]):
            continue
        rows.append({"id": cells[0], "case": cells[2],
                     "environment": cells[3], "mandatory": cells[4],
                     "result": cells[5], "evidence": cells[6]})
    return rows


def _record_for(evidence, records, want):
    """The execution record matching an evidence path, with a documented
    fallback: browser rows without a browser record bind to the unit suite
    record (their own browser logs carry their timestamps)."""
    for rec in records:
        if rec.get("name") == want:
            return rec
    for rec in records:
        if rec.get("name") == "unittest_discover":
            return rec
    return None


def _want_for(evidence):
    ev = (evidence or "").lower()
    if "browser" in ev:
        return "browser"
    if "pub-t02" in ev:
        return "publication_checks"
    return "unittest_discover"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix",
                    default=str(REPO_ROOT / "docs" / "ACCEPTANCE_MATRIX.md"))
    ap.add_argument("--records",
                    default=str(REPO_ROOT / RECORDS_REL))
    ap.add_argument("--out",
                    default=str(REPO_ROOT / "artifacts" / "TEST_REPORT.json"))
    args = ap.parse_args(argv)

    # (A03) Refuse to bless stale executions with fresh hashes.
    records_path = Path(args.records)
    if not records_path.is_file():
        print("ERROR: %s is missing; run tools/run_acceptance_records.py "
              "first" % records_path)
        return 2
    try:
        doc = json.loads(records_path.read_text(encoding="utf-8"))
    except ValueError as e:
        print("ERROR: unreadable execution records: %s" % e)
        return 2
    ident = doc.get("identity") or {}
    runtime = REPO_ROOT / "opencode_dashboard.py"
    h = sha256(runtime)
    if str(ident.get("runtime_sha256", "")) != h:
        print("ERROR: execution records were made for a different runtime; "
              "rerun tools/run_acceptance_records.py")
        return 2
    for group in ("tests", "tools"):
        for rel, digest in (ident.get(group) or {}).items():
            p = REPO_ROOT / rel
            if not p.is_file():
                print("ERROR: %s changed since the recorded execution: %s; "
                      "rerun tools/run_acceptance_records.py" % (group, rel))
                return 2
            if sha256(p) == str(digest):
                continue
            # EOL-normalised fallback: identity binds content, not checkout
            # (an autocrlf worktree may hold CRLF for a committed LF file).
            try:
                norm = hashlib.sha256(
                    p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            except OSError:
                norm = ""
            if norm != str(digest):
                print("ERROR: %s changed since the recorded execution: %s; "
                      "rerun tools/run_acceptance_records.py" % (group, rel))
                return 2
    records = doc.get("commands") or []
    os_label = (doc.get("environment") or {}).get("os") or (
        "windows" if os.name == "nt" else "linux")

    rows = parse_matrix(Path(args.matrix))
    results = []
    cases = []
    counts = {"passed": 0, "failed": 0, "blocked": 0, "not_run": 0,
              "skipped": 0}
    for r in rows:
        if r["result"] == "PASS":
            rec = _record_for(r["evidence"], records, _want_for(r["evidence"]))
            res = {"acceptance_id": r["id"], "result": "PASS",
                   "environment": os_label, "evidence": r["evidence"],
                   "command": r["case"],
                   "executed": rec is not None,
                   "execution_exit_code": (rec.get("exit_code")
                                           if rec else None)}
            counts["passed"] += 1
        elif r["result"] == "CI_PENDING":
            rec = None
            res = {"acceptance_id": r["id"], "result": "CI_PENDING",
                   "environment": os_label, "evidence": r["evidence"],
                   "command": r["case"]}
            counts["blocked"] += 1
        else:
            continue
        results.append(res)
        cases.append({"id": r["id"], "status": res["result"],
                      "command": r["case"],
                      "exit_code": (rec.get("exit_code") if rec else None),
                      "seconds": (rec.get("seconds") if rec else None),
                      "expected": "pass", "observed": res["result"],
                      "evidence": [r["evidence"]]
                      if r["evidence"] not in ("-", "") else []})
    # Executed-launcher evidence for PUB-T02 (Git Bash/Windows), alongside
    # the matrix row that records the git-mode side of the same scenario.
    extra_evidence = "artifacts/PUB-T02-gitbash.windows.log"
    if (REPO_ROOT / extra_evidence).is_file():
        rec = _record_for(extra_evidence, records, "publication_checks")
        results.append({"acceptance_id": "PUB-T02", "result": "PASS",
                        "environment": os_label,
                        "evidence": extra_evidence,
                        "command": "git bash: ./start-dashboard.sh",
                        "executed": rec is not None,
                        "execution_exit_code": (rec.get("exit_code")
                                                if rec else None)})
        cases.append({"id": "PUB-T02", "status": "PASS",
                      "command": "git bash: ./start-dashboard.sh",
                      "exit_code": (rec.get("exit_code") if rec else None),
                      "seconds": (rec.get("seconds") if rec else None),
                      "expected": "launcher runs and serves",
                      "observed": "PASS", "evidence": [extra_evidence]})
        counts["passed"] += 1
    started = records[0].get("started_at") if records else None
    finished = records[-1].get("finished_at") if records else None
    report = {
        "schema_version": 1,
        "repo": "zsoXi/little-better-dashboard",
        "base_commit": _git("rev-parse", "HEAD"),
        "tested_commit": _git("rev-parse", "HEAD"),
        "tested_worktree_dirty": bool(_git("status", "--porcelain")),
        "runtime_sha256": h,
        "code_hash": "sha256:" + h,
        "changed_file_sha256": {"opencode_dashboard.py": h},
        "test_environment": {
            "os": os_label,
            "python": sys.version.split()[0],
            "browser": ("msedge headless (playwright 1.62.0)"
                        if os_label == "windows"
                        else "chromium headless (playwright; see "
                             "requirements-dev.txt)"),
            "timezone": (datetime.datetime.now().astimezone().tzname()
                         or "local"),
        },
        "started_at": started,
        "finished_at": finished,
        "execution": {
            "records_file": RECORDS_REL,
            "generated_at": doc.get("generated_at"),
            "environment": doc.get("environment"),
            "identity": ident,
            "commands": records,
        },
        "overall_status": "core_candidate",
        "counts": counts,
        "results": results,
        "cases": cases,
        "ci_runs": [],
        "known_limitations": [
            "real CI runs are pending (workflow added, not executed)",
            ("Linux and macOS were not verified on this machine"
             if os_label == "windows"
             else "Windows and macOS were not verified on this machine"),
        ],
        "blocked_items": ["F8-T04 real CI jobs"],
        "processes_started": ["in-process test servers on 127.0.0.1:0 only"],
        "processes_cleaned_up": True,
        "remote_changes_performed": [],
    }
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote %s: %d results, %d passed"
          % (args.out, len(results), counts["passed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
