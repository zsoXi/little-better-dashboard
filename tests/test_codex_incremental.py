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


# ---------------------------------------------------------------------------
# F6c (spec chapter 14): incremental Codex read with per-file state.
# After the first pass only new bytes of appended files are parsed; stored
# context (model/provider) survives the offset; truncation/rotation rebuild a
# file's contribution instead of summing; the restart checkpoint is a cache
# tied to the published generation and is rejected when invalid.
# ---------------------------------------------------------------------------

import hashlib as _hashlib
import random as _random
import unittest.mock as _mock


def _f6c_line(obj):
    return _json.dumps(obj)


def _f6c_session_line(sid="sid-1", cwd="C:/x", provider="codex"):
    return {"type": "session_meta", "timestamp": "2026-09-16T10:00:00",
            "payload": {"id": sid, "cwd": cwd, "model_provider": provider}}


def _f6c_turn_line(model="muse-spark"):
    return {"type": "turn_context", "timestamp": "2026-09-16T10:00:01",
            "payload": {"model": model}}


def _f6c_token_line(total=120, ts="2026-09-16T10:00:02", pad=None):
    payload = {"usage": {"input_tokens": max(0, total - 20),
                         "cached_input_tokens": 0,
                         "output_tokens": min(20, total),
                         "total_tokens": total,
                         "reasoning_output_tokens": 0,
                         "cache_write_input_tokens": 0}}
    if pad is not None:
        payload["note"] = pad
    return {"type": "token_usage_record", "timestamp": ts, "payload": payload}


def _f6c_bytes(records):
    return ("\n".join(_f6c_line(r) for r in records) + "\n").encode("utf-8")


def _f6c_oracle_totals(path):
    """Simple full-parser reference: token totals in file order."""
    totals = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = _json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "token_usage_record":
            continue
        pay = rec.get("payload")
        if not isinstance(pay, dict):
            continue
        usage = pay.get("usage")
        if not isinstance(usage, dict):
            continue
        totals.append(int(usage.get("total_tokens") or 0))
    return totals


class TestF6cIncrementalRead(unittest.TestCase):
    def setUp(self):
        self._env = {}
        for name in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
            self._env[name] = os.environ.get(name)
        self._tmp = tempfile.TemporaryDirectory(prefix="f6c-codex-")
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.addCleanup(self._cleanup)
        self.d = _load_dashboard()
        d = self.d
        self._saved = {}
        for key in ("CODEX_DIR", "CODEX_SYNTH", "CODEX_INDEX",
                    "_codex_cache", "_codex_sig",
                    "_codex_ev_cache", "_codex_ev_sig",
                    "_codex_file_state", "_codex_ev_state",
                    "_codex_last_error", "_codex_last_ok"):
            self._saved[key] = getattr(d, key, "__missing__")
        self.addCleanup(self._restore)
        self.sessions = Path(self._tmp.name) / "codex" / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        d.CODEX_DIR = self.sessions
        d.CODEX_SYNTH = Path(self._tmp.name) / "codex_router_events.jsonl"
        d.CODEX_INDEX = Path(self._tmp.name) / "codex_index.json"
        d._codex_cache = {}
        d._codex_sig = None
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_file_state = {}
        d._codex_ev_state = {}

    def _cleanup(self):
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def _restore(self):
        d = self.d
        for key, value in self._saved.items():
            if value == "__missing__":
                try:
                    delattr(d, key)
                except AttributeError:
                    pass
            else:
                setattr(d, key, value)

    def _write(self, name, data):
        path = self.sessions / name
        path.write_bytes(data)
        return path

    def _spy(self):
        calls = {"bytes": 0}
        real_open = open
        root = str(self.sessions)

        class _Counted:
            def __init__(self, raw):
                self._raw = raw

            def read(self, *a, **k):
                data = self._raw.read(*a, **k)
                calls["bytes"] += len(data)
                return data

            def readline(self, *a, **k):
                data = self._raw.readline(*a, **k)
                calls["bytes"] += len(data)
                return data

            def __getattr__(self, name):
                return getattr(self._raw, name)

            def __enter__(self):
                self._raw.__enter__()
                return self

            def __exit__(self, *exc):
                return self._raw.__exit__(*exc)

        def counting_open(file, mode="r", *args, **kwargs):
            fh = real_open(file, mode, *args, **kwargs)
            if "b" in str(mode) and str(file).startswith(root):
                return _Counted(fh)
            return fh

        return calls, counting_open

    def test_f6c_t01_unchanged_refresh_reuses_state(self):
        d = self.d
        p = self._write("rollout-a.jsonl", _f6c_bytes([
            _f6c_session_line(), _f6c_turn_line(), _f6c_token_line(120)]))
        first = d.query_codex(force=True)
        self.assertEqual(first["tokens"], 120)
        self.assertEqual(first["total"], 1)
        state = d._codex_file_state[str(p)]
        self.assertEqual(state["offset"], p.stat().st_size)
        calls, counting_open = self._spy()
        with _mock.patch("builtins.open", side_effect=counting_open):
            second = d.query_codex()
        self.assertEqual(second["tokens"], 120)
        self.assertEqual(second["total"], 1)
        self.assertEqual(calls["bytes"], 0)

    def test_f6c_t02_append_reads_only_new_range(self):
        d = self.d
        p = self._write("rollout-b.jsonl", _f6c_bytes([
            _f6c_session_line(), _f6c_turn_line(), _f6c_token_line(100)]))
        first = d.query_codex(force=True)
        self.assertEqual(first["tokens"], 100)
        extra = _f6c_bytes([_f6c_token_line(140)])
        with p.open("ab") as fh:
            fh.write(extra)
        calls, counting_open = self._spy()
        with _mock.patch("builtins.open", side_effect=counting_open):
            second = d.query_codex()
        self.assertEqual(second["tokens"], 240)
        self.assertLessEqual(calls["bytes"], len(extra) + 64)

    def test_f6c_t03_context_survives_offset(self):
        d = self.d
        p = self._write("rollout-c.jsonl", _f6c_bytes([
            _f6c_session_line(provider="codex"), _f6c_turn_line(model="gpt-5.2")]))
        self.assertEqual(d._parse_codex_events(p), [])
        state = d._codex_ev_state[str(p)]
        self.assertEqual(state["model"], "gpt-5.2")
        self.assertEqual(state["provider"], "codex")
        extra = _f6c_bytes([_f6c_token_line(77)])
        with p.open("ab") as fh:
            fh.write(extra)
        calls, counting_open = self._spy()
        with _mock.patch("builtins.open", side_effect=counting_open):
            events = d._parse_codex_events(p)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model"], "gpt-5.2")
        self.assertEqual(events[0]["provider"], "codex")
        self.assertEqual(events[0]["total"], 77)
        self.assertLessEqual(calls["bytes"], len(extra) + 64)

    def test_f6c_t04_unfinished_line_waits_for_completion(self):
        d = self.d
        prefix = _f6c_bytes([_f6c_session_line(), _f6c_turn_line()])
        partial = _f6c_line(_f6c_token_line(120)).encode("utf-8")
        p = self._write("rollout-d.jsonl", prefix + partial[:20])
        self.assertEqual(d._parse_codex_events(p), [])
        state = d._codex_ev_state[str(p)]
        self.assertEqual(state["offset"], len(prefix))
        with p.open("ab") as fh:
            fh.write(partial[20:] + b"\n")
        events = d._parse_codex_events(p)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["total"], 120)
        again = d._parse_codex_events(p)
        self.assertEqual(len(again), 1)

    def test_f6c_t05_replace_and_truncate_rebuild_contribution(self):
        d = self.d
        pad_a = "a" * 40
        pad_b = "b" * 40
        content1 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(120, pad=pad_a),
                               _f6c_token_line(140, pad=pad_a)])
        p = self._write("rollout-e.jsonl", content1)
        first = d.query_codex(force=True)
        self.assertEqual(first["tokens"], 260)
        original = p.stat()
        content2 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(180, pad=pad_b),
                               _f6c_token_line(190, pad=pad_b)])
        self.assertEqual(len(content2), len(content1))
        tmp = p.with_name("rollout-e.jsonl.new")
        tmp.write_bytes(content2)
        os.utime(tmp, ns=(original.st_atime_ns, original.st_mtime_ns))
        os.replace(tmp, p)
        after = p.stat()
        self.assertEqual(after.st_size, original.st_size)
        self.assertEqual(after.st_mtime_ns, original.st_mtime_ns)
        second = d.query_codex()
        self.assertEqual(second["tokens"], 370)
        p.write_bytes(_f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                                  _f6c_token_line(180, pad=pad_b)]))
        third = d.query_codex()
        self.assertEqual(third["tokens"], 180)

    def test_f6c_t06_subsecond_change_detected(self):
        d = self.d
        pad_a = "a" * 40
        pad_b = "b" * 40
        content1 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(120, pad=pad_a)])
        p = self._write("rollout-f.jsonl", content1)
        first = d.query_codex(force=True)
        self.assertEqual(first["tokens"], 120)
        original = p.stat()
        content2 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(190, pad=pad_b)])
        self.assertEqual(len(content2), len(content1))
        p.write_bytes(content2)
        bumped = original.st_mtime_ns + 1000
        os.utime(p, ns=(original.st_atime_ns, bumped))
        st = p.stat()
        self.assertEqual(st.st_size, original.st_size)
        self.assertEqual(st.st_mtime_ns // 10**9, original.st_mtime_ns // 10**9)
        self.assertNotEqual(st.st_mtime_ns, original.st_mtime_ns)
        second = d.query_codex()
        self.assertEqual(second["tokens"], 190)

    def test_f6c_t07_additive_collision_fingerprint_distinguishes(self):
        d = self.d
        pad_a = "x" * 60
        content1 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(120, pad=pad_a)])
        p1 = self._write("rollout-g1.jsonl", content1)
        base_seconds = 1_700_000_000
        os.utime(p1, ns=(base_seconds * 10**9, base_seconds * 10**9))
        first = d.query_codex(force=True)
        self.assertEqual(first["tokens"], 120)
        p1.unlink()
        pad_b = "y" * 52
        content2 = _f6c_bytes([_f6c_session_line(), _f6c_turn_line(),
                               _f6c_token_line(190, pad=pad_b)])
        size_delta = len(content1) - len(content2)
        self.assertGreater(size_delta, 0)
        p2 = self._write("rollout-g2.jsonl", content2)
        shifted = base_seconds + size_delta
        os.utime(p2, ns=(shifted * 10**9, shifted * 10**9))
        self.assertEqual(len(content1) + base_seconds, len(content2) + shifted)
        second = d.query_codex()
        self.assertEqual(second["files"], 1)
        self.assertEqual(second["tokens"], 190)

    def test_f6c_t08_restart_uses_valid_checkpoint(self):
        d = self.d
        p = self._write("rollout-h.jsonl", _f6c_bytes([
            _f6c_session_line(), _f6c_turn_line(), _f6c_token_line(120)]))
        path, err = d.ensure_codex_synth(force=True)
        self.assertIsNone(err)
        self.assertTrue(d.CODEX_SYNTH.is_file())
        index = _json.loads(d.CODEX_INDEX.read_text(encoding="utf-8"))
        self.assertEqual(index["version"], d.CODEX_PARSER_VERSION)
        digest = _hashlib.sha256(d.CODEX_SYNTH.read_bytes()).hexdigest()
        self.assertEqual(index["generation"], digest)
        self.assertIn(str(p), index["files"])
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_ev_state = {}
        calls, counting_open = self._spy()
        with _mock.patch("builtins.open", side_effect=counting_open):
            path2, err2 = d.ensure_codex_synth()
        self.assertIsNone(err2)
        rows = [l for l in d.CODEX_SYNTH.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(_json.loads(rows[0])["totalTokens"], 120)
        self.assertLessEqual(calls["bytes"], 64)
        extra = _f6c_bytes([_f6c_token_line(90)])
        with p.open("ab") as fh:
            fh.write(extra)
        calls2, counting_open2 = self._spy()
        with _mock.patch("builtins.open", side_effect=counting_open2):
            path3, err3 = d.ensure_codex_synth()
        self.assertIsNone(err3)
        rows2 = [l for l in d.CODEX_SYNTH.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows2), 2)
        self.assertLessEqual(calls2["bytes"], len(extra) + 64)

    def test_f6c_t09_missing_or_corrupt_checkpoint_rebuilds(self):
        d = self.d
        p = self._write("rollout-i.jsonl", _f6c_bytes([
            _f6c_session_line(), _f6c_turn_line(), _f6c_token_line(120)]))
        path, err = d.ensure_codex_synth(force=True)
        self.assertIsNone(err)
        d.CODEX_INDEX.unlink()
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_ev_state = {}
        path2, err2 = d.ensure_codex_synth()
        self.assertIsNone(err2)
        rows = [l for l in d.CODEX_SYNTH.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertTrue(d.CODEX_INDEX.is_file())
        d.CODEX_INDEX.write_text("{ not json", encoding="utf-8")
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        d._codex_ev_state = {}
        path3, err3 = d.ensure_codex_synth()
        self.assertIsNone(err3)
        rows3 = [l for l in d.CODEX_SYNTH.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(rows3), 1)
        index = _json.loads(d.CODEX_INDEX.read_text(encoding="utf-8"))
        self.assertEqual(index["version"], d.CODEX_PARSER_VERSION)

    def test_f6c_t10_seeded_sequence_matches_reference_oracle(self):
        d = self.d
        rng = _random.Random(20260916)
        p = self._write("rollout-j.jsonl", _f6c_bytes([
            _f6c_session_line(), _f6c_turn_line()]))
        self.assertEqual(d._parse_codex_events(p), [])
        self.assertIn(str(p), d._codex_ev_state)
        next_total = 100

        def check():
            events = d._parse_codex_events(p)
            self.assertEqual([e["total"] for e in events], _f6c_oracle_totals(p))
            state = d._codex_ev_state[str(p)]
            self.assertLessEqual(state["offset"], p.stat().st_size)
            return events

        for step in range(10):
            op = rng.choice(["append", "append", "append", "rewrite", "restart", "refresh"])
            if op == "append":
                with p.open("ab") as fh:
                    fh.write(_f6c_bytes([_f6c_token_line(next_total)]))
                next_total += rng.choice([10, 20, 30])
            elif op == "rewrite":
                tokens = [_f6c_token_line(next_total + 10 * i) for i in range(rng.randint(1, 3))]
                p.write_bytes(_f6c_bytes([_f6c_session_line(), _f6c_turn_line()] + tokens))
                next_total += 100
            elif op == "restart":
                d._codex_ev_cache = {}
                d._codex_ev_sig = None
                d._codex_ev_state = {}
            else:
                calls, counting_open = self._spy()
                with _mock.patch("builtins.open", side_effect=counting_open):
                    check()
                self.assertEqual(calls["bytes"], 0)
                continue
            check()

        final = check()
        self.assertEqual(len(final), len(_f6c_oracle_totals(p)))


if __name__ == "__main__":
    unittest.main()
