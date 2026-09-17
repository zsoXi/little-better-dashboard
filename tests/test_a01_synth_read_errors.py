"""A01 regression: codex synth read failures must stay explicit.

A transient read error on one rollout must never publish fake emptiness:
the last good generation is kept byte-for-byte, the failure is returned and
recorded in ``_codex_last_error``, and the next plain call (no force, no
restart, no source change) retries and reaches the full total. With no
previous generation at all the source is unavailable (RuntimeError), never
a fake zero.

These tests call the public publish entry point only; no test expectation
of the existing suite is changed.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dashboard():
    try:
        import opencode_dashboard  # noqa: F401
        return opencode_dashboard
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("dashboard runtime unavailable: %s" % exc)


def _rollout_lines(n, total=120, ts="2026-09-16T12:00:00", model="muse-spark"):
    lines = [
        json.dumps({"type": "session_meta", "timestamp": ts,
                    "payload": {"model_provider": "codex"}}),
        json.dumps({"type": "turn_context", "timestamp": ts,
                    "payload": {"model": model}}),
    ]
    for _ in range(n):
        lines.append(json.dumps({
            "type": "token_usage_record", "timestamp": ts,
            "payload": {"usage": {
                "input_tokens": 100, "cached_input_tokens": 40,
                "output_tokens": 20, "total_tokens": total,
                "reasoning_output_tokens": 5,
                "cache_write_input_tokens": 0}}}))
    return "\n".join(lines) + "\n"


class A01SynthReadFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a01-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.codex_dir = self.root / "codex"
        self.codex_dir.mkdir()
        self.synth_path = self.root / "synth.jsonl"
        self._env_backup = {key: os.environ.get(key)
                            for key in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        for key in self._env_backup:
            os.environ[key] = str(self.home)
        self.addCleanup(self._restore_env)
        d = _load_dashboard()
        self.d = d
        self._backup = {
            "CODEX_DIR": getattr(d, "CODEX_DIR", None),
            "CODEX_SYNTH": d.CODEX_SYNTH,
            "_codex_ev_cache": d._codex_ev_cache,
            "_codex_ev_sig": d._codex_ev_sig,
            "_codex_last_error": d._codex_last_error,
            "_codex_last_ok": d._codex_last_ok,
        }
        self.addCleanup(self._restore_module)
        d.CODEX_DIR = self.codex_dir
        d.CODEX_SYNTH = self.synth_path
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_last_error = None
        d._codex_last_ok = None
        if hasattr(d, "_codex_ev_state"):
            d._codex_ev_state.clear()
        if hasattr(d, "_codex_read_failures"):
            d._codex_read_failures.clear()
        if hasattr(d, "ROUTER_CACHE"):
            d.ROUTER_CACHE.clear()
        self.rollout = self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl"
        self.rollout.write_text(_rollout_lines(5), encoding="utf-8")

    def _restore_env(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _restore_module(self):
        d = self.d
        d.CODEX_DIR = self._backup["CODEX_DIR"]
        d.CODEX_SYNTH = self._backup["CODEX_SYNTH"]
        d._codex_ev_cache = self._backup["_codex_ev_cache"]
        d._codex_ev_sig = self._backup["_codex_ev_sig"]
        d._codex_last_error = self._backup["_codex_last_error"]
        d._codex_last_ok = self._backup["_codex_last_ok"]

    def _sum_tokens(self):
        total = 0
        for line in self.synth_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                total += int(json.loads(line).get("totalTokens") or 0)
        return total

    def test_a01_read_failure_keeps_last_generation_and_retries_plain(self):
        d = self.d
        import unittest.mock as _mock
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        self.assertEqual(str(path_a), str(d.CODEX_SYNTH))
        bytes_a = self.synth_path.read_bytes()
        before = self._sum_tokens()
        self.assertGreater(before, 0)
        # The source grows while the next read of it is denied.
        with open(self.rollout, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "token_usage_record",
                "timestamp": "2026-09-16T12:00:01",
                "payload": {"usage": {
                    "input_tokens": 30, "cached_input_tokens": 0,
                    "output_tokens": 0, "total_tokens": 30,
                    "reasoning_output_tokens": 0,
                    "cache_write_input_tokens": 0}}}) + "\n")
        with _mock.patch.object(d, "_codex_read_since",
                                side_effect=PermissionError("injected denied")):
            path_b, err_b = d.ensure_codex_synth()
        self.assertEqual(str(path_b), str(d.CODEX_SYNTH))
        self.assertIsNotNone(err_b, "a source read failure must be explicit")
        self.assertIn("injected denied", str(err_b))
        self.assertEqual(self.synth_path.read_bytes(), bytes_a,
                         "the last good generation must be kept")
        self.assertTrue(getattr(d, "_codex_last_error", None))
        # Plain retry after the failure clears: no force, no restart.
        path_c, err_c = d.ensure_codex_synth()
        self.assertIsNone(err_c)
        self.assertEqual(self._sum_tokens(), before + 30)
        self.assertEqual(Path(str(path_c)).read_bytes(),
                         self.synth_path.read_bytes())

    def test_a01_no_previous_snapshot_read_failure_raises_unavailable(self):
        d = self.d
        import unittest.mock as _mock
        self.assertFalse(self.synth_path.exists())
        with _mock.patch.object(d, "_codex_read_since",
                                side_effect=PermissionError("injected denied")):
            with self.assertRaises(RuntimeError) as raised:
                d.ensure_codex_synth(force=True)
        self.assertIn("injected denied", str(raised.exception))
        self.assertFalse(self.synth_path.exists(),
                         "no fake empty generation may appear")


if __name__ == "__main__":
    unittest.main()
