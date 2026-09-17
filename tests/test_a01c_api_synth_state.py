"""A01c: the /api/router payload must state a failed synth refresh.

During a transient read error the HTTP payload keeps the last good snapshot
but must say that the refresh failed (synth_stale/synth_error); after the
error clears, a plain request re-reads and returns the full total.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import unittest.mock as _mock
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_integration import _make_db  # noqa: E402


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


class A01cApiSynthStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a01c-tests-")
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
        self._handler_backup = {
            name: getattr(d.Handler, name, None)
            for name in ("db_path", "router_events", "router_limits",
                         "quiet", "auth_token")}
        self._globals_backup = {
            "CODEX_DIR": getattr(d, "CODEX_DIR", None),
            "CODEX_SYNTH": d.CODEX_SYNTH,
            "CODEX_INDEX": d.CODEX_INDEX,
            "_codex_ev_cache": d._codex_ev_cache,
            "_codex_ev_sig": d._codex_ev_sig,
            "_codex_last_error": d._codex_last_error,
        }
        self.addCleanup(self._restore_globals)
        codex = self.root / "codex"
        codex.mkdir()
        d.CODEX_DIR = codex
        d.CODEX_SYNTH = self.root / "synth.jsonl"
        d.CODEX_INDEX = self.root / "codex_index.json"
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_last_error = None
        d._codex_ev_state.clear()
        if hasattr(d, "_codex_read_failures"):
            d._codex_read_failures.clear()
        self.rollout = codex / "rollout-2026-01-01T00-00-00-a01c.jsonl"
        self.rollout.write_text(_rollout_text(120), encoding="utf-8")
        db = self.root / "db.sqlite3"
        _make_db(db)
        token = "audit-token-" + "x" * 24
        d.Handler.db_path = str(db)
        d.Handler.router_events = None
        d.Handler.router_limits = None
        d.Handler.quiet = True
        d.Handler.auth_token = token
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
        self.server.auth_token = token
        self.token = token
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _restore_env(self):
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _restore_globals(self):
        d = self.d
        for name, value in self._handler_backup.items():
            setattr(d.Handler, name, value)
        d.CODEX_DIR = self._globals_backup["CODEX_DIR"]
        d.CODEX_SYNTH = self._globals_backup["CODEX_SYNTH"]
        d.CODEX_INDEX = self._globals_backup["CODEX_INDEX"]
        d._codex_ev_cache = self._globals_backup["_codex_ev_cache"]
        d._codex_ev_sig = self._globals_backup["_codex_ev_sig"]
        d._codex_last_error = self._globals_backup["_codex_last_error"]

    def _get(self, path="/api/router"):
        req = Request("http://127.0.0.1:%d%s" % (self.port, path),
                      headers={"Authorization": "Bearer " + self.token})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_a01c_router_payload_reports_failed_refresh(self):
        res = self._get()
        self.assertTrue(res.get("is_synth"))
        self.assertNotIn("synth_error", res)
        t1 = res["totals"]["tokens_total"]
        self.assertGreater(t1, 0)
        with open(self.rollout, "a", encoding="utf-8") as handle:
            handle.write(_record(30) + "\n")
        with _mock.patch.object(self.d, "_codex_read_since",
                                side_effect=PermissionError("injected denied")):
            res2 = self._get()
        self.assertTrue(res2.get("synth_stale"),
                        "the payload must state the failed refresh")
        self.assertIn("injected denied", res2.get("synth_error") or "")
        self.assertEqual(res2["totals"]["tokens_total"], t1)
        res3 = self._get()
        self.assertNotIn("synth_error", res3)
        self.assertEqual(res3["totals"]["tokens_total"], t1 + 30)


if __name__ == "__main__":
    unittest.main()
