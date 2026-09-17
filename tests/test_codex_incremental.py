"""Codex incremental tests (F5c) + original skeleton smoke checks.

F5c (spec INSTRUKCJA ch.11): _parse_codex_file must reject a JSON value
that is not an object before touching .get - previously an AttributeError
aborted the whole file and /api/codex hid the entire source behind
ok:false. Rejected records are counted in diagnostic metadata; empty
lines are not invalid records; the existing payload (pl) guard stays.
All fixtures synthetic in temp dirs; isolated HOME; stdlib only.
"""
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
        import opencode_dashboard as d
        return d
    except Exception as e:
        raise unittest.SkipTest(f"opencode_dashboard import failed: {e}")


class TestCodexIncremental(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-codex-incr-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_discovery_smoke(self):
        self.assertTrue(True)
        self.assertEqual(os.environ.get("HOME"), self._tmp.name)
        self.assertTrue(Path(self._tmp.name).is_dir())

    def test_import_dashboard(self):
        d = _load_dashboard()
        self.assertTrue(hasattr(d, "zero_days_window"))
        self.assertTrue(hasattr(d, "_fingerprint_paths"))

    def test_window_and_fingerprint_pure(self):
        d = _load_dashboard()
        window = d.zero_days_window()
        self.assertEqual(len(window), d.ACTIVITY_DAYS)
        self.assertIn("date", window[0])
        # Fingerprint helper works on synthetic temp files only.
        probe = Path(self._tmp.name) / "probe.db"
        probe.write_text("synthetic", encoding="utf-8")
        fp = d._fingerprint_paths([probe])
        self.assertTrue(len(fp) >= 1)
        self.assertEqual(fp[0][0], str(probe))


# ---------------------------------------------------------------------------
# F5c (spec INSTRUKCJA ch.11): _parse_codex_file must reject a JSON value
# that is not an object before touching .get - previously an AttributeError
# aborted the whole file and /api/codex hid the entire source behind
# ok:false. The existing payload (pl) type guard stays untouched; rejected
# records are counted in diagnostic metadata; empty lines are not invalid
# records; rejected record contents are never echoed into logs.
# ---------------------------------------------------------------------------
import http.client as _http_client
import json as _json
import secrets as _secrets
import threading as _threading
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer


def _f5c_standard_lines(ts="2026-09-16T12:00:00"):
    return [
        {"type": "session_meta", "timestamp": ts,
         "payload": {"id": "sid-1", "cwd": "C:/x",
                     "model_provider": "codex"}},
        {"type": "turn_context", "timestamp": ts,
         "payload": {"model": "muse-spark"}},
        {"type": "event_msg", "timestamp": ts,
         "payload": {"item": {"content": [{"text": "hello world"}]}}},
        {"type": "response_item", "timestamp": ts,
         "payload": {"name": "shell",
                     "content": [{"text": "ls output"}]}},
        {"type": "token_usage_record", "timestamp": ts,
         "payload": {"usage": {"input_tokens": 100,
                               "cached_input_tokens": 40,
                               "output_tokens": 20,
                               "total_tokens": 120,
                               "reasoning_output_tokens": 5,
                               "cache_write_input_tokens": 0}}},
    ]


class TestF5cRecGuard(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k)
                         for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-codex-incr-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        d = self.d
        self._saved = {}
        for k in ("CODEX_DIR", "_codex_cache", "_codex_sig"):
            self._saved[k] = getattr(d, k, "--missing--")
        self.addCleanup(self._restore)
        self.sessions = Path(self._tmp.name) / "codex" / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        d.CODEX_DIR = self.sessions
        d._codex_cache = {}
        d._codex_sig = None

    def _restore(self):
        d = self.d
        for k, v in self._saved.items():
            if v == "--missing--":
                try:
                    delattr(d, k)
                except AttributeError:
                    pass
            else:
                setattr(d, k, v)

    def _parse(self, name, text):
        p = self.sessions / name
        p.write_text(text, encoding="utf-8")
        return self.d._parse_codex_file(p, p.stat())

    def test_f5c_t01_non_object_json_lines_rejected(self):
        row = self._parse("mixed-types.jsonl",
                          '[]\n[1]\nnull\ntrue\n123\n"text"\n')
        self.assertEqual(row.get("_invalid"), 6)
        self.assertEqual(row["n"], 0)
        self.assertEqual(row["tok"], 0)

    def test_f5c_t02_bad_json_empty_line_and_usable_record(self):
        ts = "2026-09-16T12:00:00"
        usable = {"type": "event_msg", "timestamp": ts,
                  "payload": {"item": {"content": [{"text": "tail"}]}}}
        row = self._parse("tail.jsonl", "{oops\n\n" + _json.dumps(usable) + "\n")
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["last"], ts)
        # The empty line is not an invalid record; the bad JSON line is
        # skipped exactly as before the fix.
        self.assertEqual(row.get("_invalid"), 0)

    def test_f5c_t03_payload_guard_still_handles_wrong_payload(self):
        ts = "2026-09-16T12:00:00"
        rec = {"type": "event_msg", "timestamp": ts, "payload": [1, 2, 3]}
        row = self._parse("bad-payload.jsonl", _json.dumps(rec) + "\n")
        self.assertEqual(row.get("_invalid"), 0)
        self.assertEqual(row["n"], 1)

    def test_f5c_t04_standard_file_semantics_unchanged(self):
        ts = "2026-09-16T12:00:00"
        text = "\n".join(_json.dumps(r)
                         for r in _f5c_standard_lines(ts)) + "\n"
        row = self._parse("rollout-standard.jsonl", text)
        self.assertEqual(row.get("_invalid"), 0)
        self.assertEqual(row["sid"], "sid-1")
        self.assertEqual(row["cwd"], "C:/x")
        self.assertEqual(row["provider"], "codex")
        self.assertEqual(row["model"], "muse-spark")
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["tok"], 120)
        self.assertEqual(row["tools"], [("shell", 1)])
        self.assertEqual(row["first"], ts)
        self.assertEqual(row["last"], ts)

    def test_f5c_t05_api_codex_over_mixed_fixture_no_500(self):
        d = self.d
        junk = '[]\n[1]\nnull\ntrue\n123\n"text"\n'
        standard = "\n".join(_json.dumps(r)
                             for r in _f5c_standard_lines()) + "\n"
        p = self.sessions / "2026" / "09" / "16" / "rollout-mixed.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(junk + standard, encoding="utf-8")
        d._codex_cache = {}
        d._codex_sig = None
        saved = {}
        for k, v in (("db_path", str(Path(self._tmp.name) / "missing.sqlite3")),
                     ("router_events", str(Path(self._tmp.name)
                                          / "no-router.jsonl")),
                     ("router_limits", None), ("quiet", True)):
            saved[k] = getattr(d.Handler, k)
            setattr(d.Handler, k, v)
        token = _secrets.token_urlsafe(32)
        server = _ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
        server.auth_token = token
        d.Handler.auth_token = token
        host, port = server.server_address
        self.assertEqual(host, "127.0.0.1")
        thread = _threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/codex?refresh=1",
                         headers={"Authorization": "Bearer " + token})
            resp = conn.getresponse()
            status = resp.status
            body = resp.read()
            conn.close()
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
            thread.join(timeout=5)
            for k, v in saved.items():
                setattr(d.Handler, k, v)
            for name in ("ROUTER_CACHE", "LOCAL_CACHE", "WINDOWS"):
                try:
                    getattr(d, name).clear()
                except Exception:
                    pass
            try:
                d.CLOSE_TIMER.cancel()
            except Exception:
                pass
            d.CLOSE_TIMER = None
            d.LAST_REQUEST = 0.0
            d.HAD_WINDOW = False
        self.assertEqual(status, 200)
        payload = _json.loads(body)
        self.assertTrue(payload.get("ok"), payload.get("error"))
        self.assertEqual(payload["total"], 1)
        self.assertGreaterEqual(int(payload.get("invalid_records", 0)), 6)
        self.assertEqual(payload["sessions"][0]["name"], "rollout-mixed")


if __name__ == "__main__":
    unittest.main()
