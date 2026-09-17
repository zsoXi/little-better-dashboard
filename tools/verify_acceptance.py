"""Acceptance-gate verifier (skeleton, stdlib only).

Checks for a given gate (core|release):
  (1) all mandatory IDs for the gate exist in the matrix,
  (2) no failures/errors/mandatory NOT_RUN-or-SKIPPED in TEST_REPORT.json,
  (3) evidence files exist,
  (4) tested code hash matches current files,
  (5) environments marked per-result (a Linux result must not fill a Windows field),
  (6) CI presence is not CI execution.

Exit 0 when the gate passes, non-zero when it fails.
Exit 2 on usage/file errors (missing matrix/report, unparsable input).

Expected TEST_REPORT.json schema (JSON object):
  {
    "code_hash": "sha256:<hex of opencode_dashboard.py>",
    "results": [
      {"acceptance_id": "F1-T01", "result": "PASS",
       "environment": "windows", "evidence": "artifacts/F1-T01.windows.log",
       "code_hash": "sha256:... (optional per-result override)"}
    ]
  }
  - result: PASS is the only passing value. FAIL/ERROR/NOT_RUN/SKIPPED/
    CI_PENDING/NOT_IMPLEMENTED all fail a mandatory ID.
  - environment: must be exactly "windows" or "linux" (one per row).
    Combined values such as "windows+linux"/"both"/"any" are rejected.
  - evidence: path relative to the repo root; must exist for every PASS.
    Pointing at .github/workflows/tests.yml alone is insufficient (see 6).
  - code_hash: top-level (preferred) or per-result; must equal the
    current sha256 of opencode_dashboard.py.

Gate semantics (skeleton):
  - core gate mandatory = matrix rows whose mandatory column contains
    "core" or equals "yes" (case-insensitive).
  - release gate mandatory = every row not marked "no"/"optional"/"-".
  - The skeleton matrix marks every ID "core+release", so both gates
    initially require the full canonical ID set below.

Usage:
    py -3.14 tools/verify_acceptance.py --help
    py -3.14 tools/verify_acceptance.py --gate release --report TEST_REPORT.json
    py -3.14 tools/verify_acceptance.py --gate core --matrix docs/ACCEPTANCE_MATRIX.md --report artifacts/TEST_REPORT.json
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = REPO_ROOT / "docs" / "ACCEPTANCE_MATRIX.md"
DEFAULT_CODE = REPO_ROOT / "opencode_dashboard.py"

ID_RE = re.compile(r"^(F\d+[a-f]?|PUB|INT)-T\d+$")


def canonical_ids():
    """Full skeleton ID set per spec section 19A."""
    families = [
        ("F1", 10), ("F2", 12), ("F3", 6), ("F4", 10),
        ("F5a", 6), ("F5b", 5), ("F5c", 5),
        ("F6a", 8), ("F6b", 8), ("F6c", 10), ("F6d", 10),
        ("F6e", 6), ("F6f", 4),
        ("F7", 6), ("F8", 6),
        ("PUB", 7), ("INT", 12),
    ]
    ids = []
    for fam, n in families:
        for i in range(1, n + 1):
            ids.append(f"{fam}-T{i:02d}")
    return ids


def parse_matrix(path):
    """Parse the acceptance matrix markdown table.

    Returns dict acceptance_id -> row dict with keys:
    acceptance_id, finding_id, case, required_environment, mandatory, result, evidence.
    """
    text = Path(path).read_text(encoding="utf-8")
    rows = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        if cells[0].lower() in ("acceptance_id", "---", " acceptance_id"):
            continue
        if set(cells[0]) <= set("-: "):
            continue
        aid = cells[0]
        if not ID_RE.match(aid):
            continue
        rows[aid] = {
            "acceptance_id": aid,
            "finding_id": cells[1],
            "case": cells[2],
            "required_environment": cells[3],
            "mandatory": cells[4],
            "result": cells[5],
            "evidence": cells[6],
        }
    return rows


def is_mandatory_for_gate(mandatory_value, gate):
    v = (mandatory_value or "").strip().lower()
    if gate == "core":
        return ("core" in v) or (v == "yes")
    # release gate: everything except explicitly optional
    return v not in ("", "no", "optional", "-", "n/a", "na", "tbd")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def normalize_hash(s):
    s = (s or "").strip()
    if not s:
        return ""
    # Accept raw 64-hex or sha256:-prefixed.
    if s.lower().startswith("sha256:"):
        return s.lower()
    if re.fullmatch(r"[0-9a-fA-F]{64}", s):
        return "sha256:" + s.lower()
    return s


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify an acceptance gate against the matrix and a TEST_REPORT.json."
    )
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX), help="path to ACCEPTANCE_MATRIX.md")
    parser.add_argument("--report", default="TEST_REPORT.json", help="path to TEST_REPORT.json")
    parser.add_argument("--gate", "--require", dest="gate",
                        choices=["core", "release"], default="release",
                        help="gate to verify (alias: --require)")
    parser.add_argument(
        "--code",
        default=str(DEFAULT_CODE),
        help="code file whose hash must match the report (default opencode_dashboard.py)",
    )
    args = parser.parse_args(argv)

    failures = []

    # Load matrix.
    try:
        matrix = parse_matrix(args.matrix)
    except FileNotFoundError:
        print(f"ERROR: matrix not found: {args.matrix}")
        return 2
    except OSError as e:
        print(f"ERROR: cannot read matrix: {e}")
        return 2

    # (1) All mandatory IDs for the gate exist in the matrix.
    canonical = canonical_ids()
    if args.gate == "core":
        expected = [i for i in canonical if True]  # skeleton: core requires full set only where marked core
        # Restrict to rows marked core; but canonical coverage still required for marked rows.
        expected_marked = [i for i in canonical if i in matrix and is_mandatory_for_gate(matrix[i]["mandatory"], "core")]
        missing = [i for i in canonical if i not in matrix]
        if missing:
            failures.append(f"(1) matrix missing canonical IDs: {missing[:5]}{'...' if len(missing) > 5 else ''} ({len(missing)} total)")
        else:
            print(f"(1) OK: all {len(canonical)} canonical IDs present in matrix.")
        mandatory_ids = [i for i in canonical if i in matrix and is_mandatory_for_gate(matrix[i]["mandatory"], "core")]
        if not mandatory_ids:
            failures.append("(1) no core-mandatory IDs marked in matrix.")
        else:
            print(f"(1) OK: {len(mandatory_ids)} core-mandatory IDs marked.")
    else:
        missing = [i for i in canonical if i not in matrix]
        if missing:
            failures.append(f"(1) matrix missing canonical IDs: {missing[:5]}{'...' if len(missing) > 5 else ''} ({len(missing)} total)")
        else:
            print(f"(1) OK: all {len(canonical)} canonical IDs present in matrix.")
        mandatory_ids = [i for i in canonical if i in matrix and is_mandatory_for_gate(matrix[i]["mandatory"], "release")]
        if len(mandatory_ids) != len(canonical):
            failures.append(
                f"(1) release gate expects all {len(canonical)} canonical IDs mandatory; marked {len(mandatory_ids)}."
            )
        else:
            print(f"(1) OK: {len(mandatory_ids)} release-mandatory IDs marked.")

    # Load report.
    try:
        report_text = Path(args.report).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"ERROR: report not found: {args.report}")
        print("(2) FAIL: no report means the gate cannot pass; mandatory IDs are NOT_RUN.")
        return 1
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: report is not valid JSON: {e}")
        return 2

    results = report.get("results", None)
    if not isinstance(results, list):
        print("ERROR: report must contain a 'results' list.")
        return 2
    by_id = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        aid = str(entry.get("acceptance_id", "")).strip()
        if ID_RE.match(aid):
            by_id.setdefault(aid, []).append(entry)

    top_hash = normalize_hash(report.get("code_hash", ""))

    # (4) Current code hash (computed once).
    try:
        current_hash = sha256_of(args.code)
    except FileNotFoundError:
        print(f"ERROR: code file not found: {args.code}")
        return 2
    print(f"code hash ({Path(args.code).name}): {current_hash}")

    # (2) No failures/errors/mandatory NOT_RUN-or-SKIPPED.
    PASS = {"PASS"}
    FAIL_VALUES = {"FAIL", "ERROR"}
    NON_PASS_MANDATORY = {"NOT_RUN", "SKIPPED", "CI_PENDING", "NOT_IMPLEMENTED", "PENDING", "TODO", ""}
    gate_ok = True
    for aid in mandatory_ids:
        entries = by_id.get(aid, [])
        if not entries:
            failures.append(f"(2) {aid}: missing from report (treated as NOT_RUN).")
            gate_ok = False
            continue
        for e in entries:
            res = str(e.get("result", "")).strip().upper()
            if res in FAIL_VALUES:
                failures.append(f"(2) {aid}: result {res} fails the gate.")
                gate_ok = False
            elif res not in PASS:
                if res in NON_PASS_MANDATORY or res not in PASS:
                    failures.append(f"(2) {aid}: mandatory result {res or '(empty)'} is not PASS.")
                    gate_ok = False
        # At least one PASS required per mandatory ID.
        if not any(str(e.get("result", "")).strip().upper() in PASS for e in entries):
            gate_ok = False  # already recorded above
    if gate_ok:
        print("(2) OK: every mandatory ID has a PASS and no FAIL/ERROR.")
    else:
        print("(2) FAIL: see failures below.")

    # (3) Evidence files exist (for every PASS row).
    ev_ok = True
    for aid in mandatory_ids:
        for e in by_id.get(aid, []):
            if str(e.get("result", "")).strip().upper() != "PASS":
                continue
            ev = str(e.get("evidence", "")).strip()
            if not ev or ev in ("-", "TBD", "N/A"):
                failures.append(f"(3) {aid}: PASS without an evidence path.")
                ev_ok = False
                continue
            ev_path = (REPO_ROOT / ev) if not Path(ev).is_absolute() else Path(ev)
            if not ev_path.exists():
                failures.append(f"(3) {aid}: evidence not found: {ev}")
                ev_ok = False
    if ev_ok:
        print("(3) OK: all PASS evidence files exist (or no PASS rows to check).")
    else:
        print("(3) FAIL: missing evidence files.")

    # (4) Tested code hash matches current files.
    hash_ok = True
    if not top_hash and not any(normalize_hash(e.get("code_hash", "")) for entries in by_id.values() for e in entries):
        failures.append("(4) no code_hash in report; cannot prove what was tested.")
        hash_ok = False
    else:
        check_hash = top_hash or current_hash
        if normalize_hash(check_hash) != normalize_hash(current_hash):
            failures.append(f"(4) report code_hash {check_hash} != current {current_hash}.")
            hash_ok = False
        # Per-result overrides must also match when present.
        for aid in mandatory_ids:
            for e in by_id.get(aid, []):
                if str(e.get("result", "")).strip().upper() != "PASS":
                    continue
                per = normalize_hash(e.get("code_hash", "") or top_hash)
                if per and per != normalize_hash(current_hash):
                    failures.append(f"(4) {aid}: per-result hash {per} != current {current_hash}.")
                    hash_ok = False
    if hash_ok:
        print("(4) OK: tested code hash matches current files.")
    else:
        print("(4) FAIL: code hash mismatch or missing.")

    # (5) Environments marked per-result.
    env_ok = True
    for aid in mandatory_ids:
        for e in by_id.get(aid, []):
            env = str(e.get("environment", "")).strip().lower()
            if env not in ("windows", "linux"):
                failures.append(
                    f"(5) {aid}: environment must be exactly 'windows' or 'linux' (got '{e.get('environment', '')}'). "
                    "A Linux result must not fill a Windows field."
                )
                env_ok = False
    if env_ok:
        print("(5) OK: all mandatory results carry a single environment (windows|linux).")
    else:
        print("(5) FAIL: environment marking invalid.")

    # (6) CI presence is not CI execution.
    print("(6) NOTE: .github/workflows/tests.yml presence is not execution; file presence alone never passes a gate.")
    ci_ok = True
    for aid in mandatory_ids:
        for e in by_id.get(aid, []):
            if str(e.get("result", "")).strip().upper() != "PASS":
                continue
            ev = str(e.get("evidence", "")).strip().replace("\\", "/")
            if ev.endswith(".yml") or ".github/workflows" in ev:
                # Allow only when accompanied by a real artifact; a lone workflow pointer is insufficient.
                failures.append(f"(6) {aid}: evidence '{ev}' is CI presence, not CI execution.")
                ci_ok = False
            if str(e.get("result", "")).strip().upper() == "CI_PENDING":
                failures.append(f"(6) {aid}: CI_PENDING is not execution.")
                ci_ok = False
    # CI_PENDING rows are already failed under (2); this is the explicit presence-vs-execution guard.
    if ci_ok:
        print("(6) OK: no PASS rests on workflow-file presence alone.")
    else:
        print("(6) FAIL: CI presence used as execution.")

    if failures:
        print(f"\nGATE {args.gate.upper()} FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nGATE {args.gate.upper()} PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
