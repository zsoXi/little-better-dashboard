"""A01b regression: the session list (query_codex) must keep the last good
contribution through a transient read error and retry on the next plain call.

The old path returned an empty row with a fresh fingerprint and committed the
signature, so the same source stayed at 0 until a forced refresh. Now the
last good row is kept (flagged partial) and the failed signature is not
blessed, so the next plain call re-reads the unchanged file.
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as _mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dashboard():
    try:
        import opencode_dashboard as d
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("opencode_dashboard import failed: %s" % (exc,))
    return d


def _record(total):
    return json.dumps({
        "type": "token_usage_record",
        "payload": {"usage": {
            "input_tokens": total, "cached_input_tokens": 0,
            "output_tokens": 0, "total_tokens": total,
            "reasoning_output_tokens": 0,
            "cache_write_input_tokens": 0}}})


def _rollout_text(total):
    lines = [
        json.dumps({"type": "session_meta",
                    "payload": {"model_provider": "codex"}}),
        json.dumps({"type": "turn_context",
                    "payload": {"model": "muse-spark"}}),
        _record(total),
    ]
    return "\n".join(lines) + "\n"


class A01bSessionListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a01b-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self._env_backup = {key: os.environ.get(key)
                            for key in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        for key in self._env_backup:
            os.environ[key] = str(self.home)
        self.addCleanup(self._restore_env)
        d = _load_dashboard()
        self.d = d
        self._backup = {"CODEX_DIR": getattr(d, "CODEX_DIR", None),
                        "_codex_file_state": d._codex_file_state,
                        "_codex_sig": d._codex_sig}
        self.addCleanup(self._restore_module)
        self.codex = self.root / "codex"
        self.codex.mkdir()
        d.CODEX_DIR = self.codex
        d._codex_file_state = {}
        d._codex_sig = None
        self.rollout = self.codex / "rollout-2026-01-01T00-00-00-a01b.jsonl"
        self.rollout.write_text(_rollout_text(120), encoding="utf-8")

    def _restore_env(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _restore_module(self):
        d = self.d
        d.CODEX_DIR = self._backup["CODEX_DIR"]
        d._codex_file_state = self._backup["_codex_file_state"]
        d._codex_sig = self._backup["_codex_sig"]

    def test_a01b_transient_read_error_keeps_last_good_and_retries(self):
        d = self.d
        res = d.query_codex(force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["tokens"], 120)
        self.assertFalse(res["partial"])
        with open(self.rollout, "a", encoding="utf-8") as handle:
            handle.write(_record(30) + "\n")
        with _mock.patch.object(d, "_codex_read_since",
                                side_effect=PermissionError("injected denied")):
            res2 = d.query_codex()
        self.assertTrue(res2["partial"],
                        "a failed read must be flagged as partial")
        self.assertEqual(res2["tokens"], 120,
                         "the last good contribution must be kept")
        # Plain retry after the failure clears: no force, no restart.
        res3 = d.query_codex()
        self.assertFalse(res3["partial"])
        self.assertEqual(res3["tokens"], 150)


if __name__ == "__main__":
    unittest.main()
