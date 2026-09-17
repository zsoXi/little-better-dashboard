"""A02 regression: the release gate must derive F8-T04 from a completed CI
job set bound to the tested snapshot.

The verifier previously accepted a report with an empty ``ci_runs`` and a
hand-flipped F8-T04 PASS row pointing at an unrelated existing log. These
tests synthesize a fully passing matrix/report pair in a temporary directory
and check the release gate only reacts to the recorded CI executions:
empty/short/wrong-snapshot/skipped job sets block the release, while the
approved five-job set passes. The core gate ignores F8-T04 entirely.
"""

import hashlib
import importlib.util
import io
import json
import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "verify_acceptance", REPO_ROOT / "tools" / "verify_acceptance.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)

TESTED_COMMIT = "a" * 40

CI_JOBS = [
    "python 3.10 on ubuntu-latest",
    "python 3.14 on ubuntu-latest",
    "python 3.10 on windows-latest",
    "python 3.14 on windows-latest",
    "browser tests on ubuntu-latest",
]


def _ci_run(name, conclusion="success", head_sha=TESTED_COMMIT,
            status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion,
            "head_sha": head_sha}


class A02GateCiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a02-tests-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.root = root
        self.code = root / "runtime.py"
        self.code.write_text("VALUE = 1\n", encoding="utf-8")
        self.code_hash = "sha256:" + hashlib.sha256(
            self.code.read_bytes()).hexdigest()
        # A03 rule (10) fixtures: identity entries must point at real
        # repository files with their real hashes (the verifier resolves
        # them against its own REPO_ROOT).
        self.tests_rel = "tests/test_a02_gate_ci.py"
        self.tests_digest = hashlib.sha256(
            (REPO_ROOT / self.tests_rel).read_bytes()).hexdigest()
        self.tools_rel = "tools/verify_acceptance.py"
        self.tools_digest = hashlib.sha256(
            (REPO_ROOT / self.tools_rel).read_bytes()).hexdigest()
        # One existing evidence file is enough: every PASS row points at it,
        # and it carries the PUB-T02 executed-launcher marker.
        self.evidence = root / "evidence.txt"
        self.evidence.write_text("launcher-run=gitbash-executed\n",
                                 encoding="utf-8")
        self.matrix = root / "matrix.md"
        rows = ["| acceptance_id | finding | case | env | mandatory | result | evidence |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        for aid in VERIFIER.canonical_ids():
            mandatory = "release" if aid == "F8-T04" else "core+release"
            rows.append(f"| {aid} | F | case | windows | {mandatory} | PASS | ev |")
        self.matrix.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.report = {
            "tested_commit": TESTED_COMMIT,
            "code_hash": self.code_hash,
            "results": [
                {"acceptance_id": aid, "result": "PASS",
                 "environment": "windows", "evidence": str(self.evidence)}
                for aid in VERIFIER.canonical_ids()
            ],
        }

    def _run(self, gate="release", mutate=None):
        report = json.loads(json.dumps(self.report))
        # A03 rule (10): every synthetic report carries an explicit, synthetic
        # execution block (honest fixture: real files, real hashes, marked
        # commands) and marks its PASS rows as executed.
        report["execution"] = {
            "records_file": "artifacts/execution-records.json",
            "identity": {
                "runtime_sha256": self.code_hash.split(":", 1)[1],
                "tests": {self.tests_rel: self.tests_digest},
                "tools": {self.tools_rel: self.tools_digest},
            },
            "commands": [{"name": "import_check", "exit_code": 0}, {"name": "py_compile", "exit_code": 0},
                         {"name": "unittest_discover", "exit_code": 0}],
        }
        for entry in report.get("results", []):
            entry["executed"] = True
            entry["execution_exit_code"] = 0
        if mutate is not None:
            mutate(report)
        report_path = self.root / ("report-%s.json" % gate)
        report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = VERIFIER.main(["--matrix", str(self.matrix),
                                  "--report", str(report_path),
                                  "--gate", gate,
                                  "--code", str(self.code)])
        return code, out.getvalue()

    def test_a02_release_rejects_missing_ci_runs(self):
        code, out = self._run(mutate=lambda r: r.update(ci_runs=[]))
        self.assertNotEqual(code, 0)
        self.assertIn("(9)", out)
        self.assertIn("ci_runs", out)

    def test_a02_release_rejects_missing_tested_commit(self):
        def mutate(r):
            r["ci_runs"] = [_ci_run(name) for name in CI_JOBS]
            r["tested_commit"] = ""
        code, out = self._run(mutate=mutate)
        self.assertNotEqual(code, 0)
        self.assertIn("tested_commit", out)

    def test_a02_release_accepts_complete_ci_set(self):
        code, out = self._run(
            mutate=lambda r: r.update(ci_runs=[_ci_run(n) for n in CI_JOBS]))
        self.assertEqual(code, 0, out)
        self.assertIn("(9) OK", out)

    def test_a02_release_rejects_wrong_snapshot(self):
        def mutate(r):
            runs = [_ci_run(n) for n in CI_JOBS]
            runs[2]["head_sha"] = "b" * 40
            r["ci_runs"] = runs
        code, out = self._run(mutate=mutate)
        self.assertNotEqual(code, 0)
        self.assertIn("windows-latest", out)

    def test_a02_release_rejects_cancelled_or_skipped_job(self):
        for conclusion in ("cancelled", "skipped", "failure"):
            def mutate(r, conclusion=conclusion):
                runs = [_ci_run(n) for n in CI_JOBS]
                runs[4]["conclusion"] = conclusion
                r["ci_runs"] = runs
            code, out = self._run(mutate=mutate)
            self.assertNotEqual(code, 0, conclusion)
            self.assertIn("browser", out)

    def test_a02_release_rejects_incomplete_platform_set(self):
        code, out = self._run(
            mutate=lambda r: r.update(ci_runs=[_ci_run(n) for n in CI_JOBS[:3]]))
        self.assertNotEqual(code, 0)
        self.assertIn("ubuntu-latest", out)

    def test_a02_core_gate_ignores_f8_t04_ci(self):
        code, out = self._run(gate="core", mutate=lambda r: r.update(ci_runs=[]))
        self.assertEqual(code, 0, out)
        self.assertIn("(9) OK: F8-T04 not mandatory for this gate.", out)


if __name__ == "__main__":
    unittest.main()
