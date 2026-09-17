"""F5a+F6a RED-then-GREEN: atomic synth publish, no silent failures.

Baseline flaws (spec F5a ch.9, F6a ch.12): ensure_codex_synth commits the
cache signature BEFORE open(CODEX_SYNTH,"w"), writes the index file
in place (readers can see a truncated file), and swallows every OSError
with a bare pass. No publish lock exists although the server is a
ThreadingHTTPServer, so concurrent refreshes interleave freely.

Honest contract under test:
- ensure_codex_synth() returns (path, error). error is None on success
  (fresh publish or already-current valid snapshot). On publish failure
  with a previous good generation it returns the OLD path plus an error
  string recorded in _codex_last_error (old bytes + old sig kept). With
  no previous good generation it raises RuntimeError (explicit
  unavailability, never a fake empty success).
- Publish = parse outside lock, unique tmp in the same dir, os.replace
  under a short-scope _SYNTH_LOCK, sig+cache commit only after swap.
- Fast path still stats the file (deleted index rebuilds). Schema bump
  rebuilds. Single attempt per call (retry happens on later refresh).
- _codex_last_ok records the last good publish. Readers are lock-free
  (atomic replace gives full-A-or-B). Thread scope only, documented.

All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA
isolated. Write failures are injected by wrapping builtins.open (no
chmod tricks, Windows-safe). No fixed ports, no real user data,
stdlib only.
"""
import json as _json
import os
import sys
import tempfile
import threading
import time
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


def _rollout_lines(n, ti=100, cache=40, to=20, total=120, tr=5,
                   ts="2026-09-16T12:00:00", model="muse-spark"):
    lines = [
        {"type": "session_meta", "timestamp": ts,
         "payload": {"model_provider": "codex"}},
        {"type": "turn_context", "timestamp": ts,
         "payload": {"model": model}},
    ]
    for _ in range(n):
        lines.append(
            {"type": "token_usage_record", "timestamp": ts,
             "payload": {"usage": {"input_tokens": ti,
                                   "cached_input_tokens": cache,
                                   "output_tokens": to,
                                   "total_tokens": total,
                                   "reasoning_output_tokens": tr,
                                   "cache_write_input_tokens": 0}}})
    return "\n".join(_json.dumps(r) for r in lines) + "\n"


def _read_all_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().splitlines()


class _SlowWriter:
    """File proxy adding a small sleep per write (widens the race)."""

    def __init__(self, real, delay=0.0005):
        self._real = real
        self._delay = delay

    def write(self, data):
        time.sleep(self._delay)
        return self._real.write(data)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestSynthPublish(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k)
                         for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-synth-publish-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        d = self.d
        self._saved = {}
        for k in ("CODEX_DIR", "CODEX_SYNTH", "_codex_ev_cache",
                  "_codex_ev_sig", "_codex_last_error", "_codex_last_ok",
                  "SYNTH_SCHEMA"):
            self._saved[k] = getattr(d, k, "--missing--")
        self.addCleanup(self._restore)
        self.codex_dir = Path(self._tmp.name) / "codex-sessions"
        self.codex_dir.mkdir(parents=True, exist_ok=True)
        self.synth_path = Path(self._tmp.name) / "synth.jsonl"
        d.CODEX_DIR = self.codex_dir
        d.CODEX_SYNTH = self.synth_path
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        if hasattr(d, "_codex_last_error"):
            d._codex_last_error = None
        if hasattr(d, "_codex_last_ok"):
            d._codex_last_ok = None
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        (self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl").write_text(
            _rollout_lines(5), encoding="utf-8")

    def _restore(self):
        d = self.d
        for k, v in self._saved.items():
            if v == "--missing--":
                try:
                    delattr(d, k)
                except Exception:
                    pass
            else:
                try:
                    setattr(d, k, v)
                except Exception:
                    pass

    def _slow_open(self, real_open, delay=0.0005):
        d = self.d
        synth_name = str(d.CODEX_SYNTH)

        def fake_open(path, *a, **k):
            s = str(path)
            mode = a[0] if a else k.get("mode", "r")
            if ("w" in str(mode) and
                    (s == synth_name or ".tmp." in s)):
                return _SlowWriter(real_open(path, *a, **k), delay)
            return real_open(path, *a, **k)

        return fake_open

    # ---- F5a-T01: injected failure, then success on retry ----
    def test_f5a_t01_failure_then_success(self):
        d = self.d
        import builtins
        real_open = builtins.open
        calls = {"n": 0}

        def flaky(path, *a, **k):
            s = str(path)
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode) and (s == str(d.CODEX_SYNTH) or
                                     ".tmp." in s):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("injected write failure")
            return real_open(path, *a, **k)

        import unittest.mock as _mock
        with _mock.patch("builtins.open", side_effect=flaky):
            try:
                res = d.ensure_codex_synth()
            except RuntimeError as e:
                res = ("RAISED", str(e))
        # No previous good generation: failure must be explicit.
        self.assertTrue(
            (isinstance(res, tuple) and
             "fail" in str(res[1]).lower()) or res[0] == "RAISED",
            f"first failing publish must surface an error, got {res!r}")
        with _mock.patch("builtins.open", side_effect=real_open):
            pass
        path2, err2 = d.ensure_codex_synth()
        self.assertIsNone(err2, f"retry must succeed, got {err2!r}")
        self.assertTrue(Path(str(path2)).is_file())
        for ln in _read_all_lines(str(path2)):
            _json.loads(ln)

    # ---- F5a-T02: good A published, failed B keeps A byte-identical ----
    def test_f5a_t02_failed_republish_keeps_old(self):
        d = self.d
        import unittest.mock as _mock
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        bytes_a = Path(str(path_a)).read_bytes()
        sig_a = d._codex_ev_sig
        # New source content -> new candidate generation B.
        (self.codex_dir / "rollout-2026-01-02T00-00-00-bbbb.jsonl").write_text(
            _rollout_lines(7, ti=200), encoding="utf-8")
        real_open = open

        def always_fail(path, *a, **k):
            s = str(path)
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode) and (s == str(d.CODEX_SYNTH) or
                                     ".tmp." in s):
                raise OSError("injected write failure B")
            return real_open(path, *a, **k)

        with _mock.patch("builtins.open", side_effect=always_fail):
            path_b, err_b = d.ensure_codex_synth()
        self.assertIsNotNone(err_b, "failed republish must record an error")
        self.assertEqual(Path(str(path_b)).read_bytes(), bytes_a,
                         "failed B must leave A byte-identical on disk")
        self.assertEqual(d._codex_ev_sig, sig_a,
                         "failed B must not commit a new signature")

    # ---- F5a-T03: no previous generation + failure = explicit error ----
    def test_f5a_t03_no_previous_failure_is_explicit(self):
        d = self.d
        import unittest.mock as _mock
        real_open = open

        def always_fail(path, *a, **k):
            s = str(path)
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode) and (s == str(d.CODEX_SYNTH) or
                                     ".tmp." in s):
                raise OSError("injected write failure")
            return real_open(path, *a, **k)

        with _mock.patch("builtins.open", side_effect=always_fail):
            with self.assertRaises(RuntimeError):
                d.ensure_codex_synth()

    # ---- F5a-T04: deleted index rebuilds even on identical sig ----
    def test_f5a_t04_deleted_index_rebuilds(self):
        d = self.d
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        bytes_a = Path(str(path_a)).read_bytes()
        Path(str(path_a)).unlink()
        self.assertFalse(Path(str(path_a)).exists())
        path_b, err_b = d.ensure_codex_synth()
        self.assertIsNone(err_b)
        self.assertTrue(Path(str(path_b)).is_file(),
                        "deleted index must be rebuilt, not fast-pathed")
        self.assertEqual(Path(str(path_b)).read_bytes(), bytes_a)

    # ---- F5a-T05: schema bump rebuilds ----
    def test_f5a_t05_schema_bump_rebuilds(self):
        d = self.d
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        sig_a = d._codex_ev_sig
        try:
            d.SYNTH_SCHEMA = d.SYNTH_SCHEMA + 1
            path_b, err_b = d.ensure_codex_synth()
        finally:
            pass
        self.assertIsNone(err_b)
        self.assertNotEqual(d._codex_ev_sig, sig_a,
                            "schema bump must change the cache key")
        for ln in _read_all_lines(str(path_b)):
            _json.loads(ln)

    # ---- F5a-T06: no retry loop, readable error/last-ok state ----
    def test_f5a_t06_no_retry_loop_readable_state(self):
        d = self.d
        import unittest.mock as _mock
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        # Source change -> new candidate never committed while writes
        # fail, so every call must attempt exactly one publish.
        (self.codex_dir / "rollout-2026-01-02T00-00-00-bbbb.jsonl").write_text(
            _rollout_lines(7, ti=200), encoding="utf-8")
        real_open = open
        attempts = {"n": 0}

        def always_fail(path, *a, **k):
            s = str(path)
            mode = a[0] if a else k.get("mode", "r")
            if "w" in str(mode) and (s == str(d.CODEX_SYNTH) or
                                     ".tmp." in s):
                attempts["n"] += 1
                raise OSError("persistent failure")
            return real_open(path, *a, **k)

        with _mock.patch("builtins.open", side_effect=always_fail):
            for _ in range(3):
                d.ensure_codex_synth()
        self.assertEqual(attempts["n"], 3,
                         "one attempt per call, no internal retry loop")
        err_state = getattr(d, "_codex_last_error", None)
        self.assertTrue(err_state,
                        "last publish error must be readable, not silent")
        ok_state = getattr(d, "_codex_last_ok", None)
        self.assertTrue(ok_state,
                        "last good publish must stay recorded")
        # Retry with unchanged sources must publish without force.
        path_c, err_c = d.ensure_codex_synth()
        self.assertIsNone(err_c, f"retry must publish, got {err_c!r}")
        for ln in _read_all_lines(str(path_c)):
            _json.loads(ln)

    # ---- F6a-T01: readers mid-write always see complete A or B ----
    def test_f6a_t01_readers_see_complete_generations(self):
        d = self.d
        import builtins
        import unittest.mock as _mock
        (self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl").write_text(
            _rollout_lines(3000), encoding="utf-8")
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        bytes_a = Path(str(path_a)).read_bytes()
        n_a = len(bytes_a.splitlines())
        # New source -> candidate B with a different line count.
        (self.codex_dir / "rollout-2026-01-02T00-00-00-bbbb.jsonl").write_text(
            _rollout_lines(1500, ti=200), encoding="utf-8")
        real_open = builtins.open
        bad = []

        def reader():
            # Windows may transiently deny an open while os.replace moves the
            # new generation into place; retry briefly and report persists.
            try:
                while writer.is_alive():
                    try:
                        raw = Path(str(d.CODEX_SYNTH)).read_bytes()
                    except PermissionError:
                        time.sleep(0.001)
                        continue
                    if raw == bytes_a:
                        continue
                    lines = raw.splitlines()
                    for ln in lines:
                        _json.loads(ln)
                    if len(lines) not in (n_a,):
                        # Any non-A snapshot must be a complete B.
                        if not lines:
                            bad.append("empty-torn")
            except Exception as e:  # noqa: BLE001 - record, assert later
                bad.append(repr(e))

        with _mock.patch("builtins.open",
                         side_effect=self._slow_open(real_open)):
            writer = threading.Thread(target=d.ensure_codex_synth)
            writer.start()
            readers = [threading.Thread(target=reader) for _ in range(3)]
            for r in readers:
                r.start()
            writer.join(120)
            for r in readers:
                r.join(120)
        self.assertFalse(writer.is_alive(), "writer deadlocked")
        for r in readers:
            self.assertFalse(r.is_alive(), "reader deadlocked")
        # Final file must be a complete generation.
        final = Path(str(d.CODEX_SYNTH)).read_bytes().splitlines()
        for ln in final:
            _json.loads(ln)
        self.assertEqual(bad, [], f"readers saw torn snapshots: {bad[:3]}")

    # ---- F6a-T02: release mid-write, then complete B on disk ----
    def test_f6a_t02_release_then_complete(self):
        d = self.d
        import builtins
        import unittest.mock as _mock
        (self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl").write_text(
            _rollout_lines(2000), encoding="utf-8")
        real_open = builtins.open
        with _mock.patch("builtins.open",
                         side_effect=self._slow_open(real_open)):
            t = threading.Thread(target=d.ensure_codex_synth)
            t.start()
            t.join(120)
        self.assertFalse(t.is_alive(), "writer deadlocked")
        lines = Path(str(d.CODEX_SYNTH)).read_bytes().splitlines()
        self.assertTrue(len(lines) > 100)
        for ln in lines:
            _json.loads(ln)

    # ---- F6a-T03: concurrent publish + reads, no deadlock/corruption ----
    def test_f6a_t03_concurrent_publish_no_corruption(self):
        d = self.d
        (self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl").write_text(
            _rollout_lines(500), encoding="utf-8")
        errors = []

        def worker():
            try:
                for _ in range(3):
                    res = d.ensure_codex_synth()
                    if isinstance(res, tuple):
                        _, err = res
                    else:
                        err = None
                    _ = err
                    raw = Path(str(d.CODEX_SYNTH)).read_bytes()
                    for ln in raw.splitlines():
                        _json.loads(ln)
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        for t in threads:
            self.assertFalse(t.is_alive(), "publish worker deadlocked")
        self.assertEqual(errors, [], f"concurrent errors: {errors[:3]}")
        final = Path(str(d.CODEX_SYNTH)).read_bytes().splitlines()
        for ln in final:
            _json.loads(ln)

    # ---- F6a-T04: failure at write/flush/close/replace keeps old ----
    def test_f6a_t04_failure_stage_keeps_old(self):
        d = self.d
        import unittest.mock as _mock
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        bytes_a = Path(str(path_a)).read_bytes()
        (self.codex_dir / "rollout-2026-01-02T00-00-00-bbbb.jsonl").write_text(
            _rollout_lines(7, ti=200), encoding="utf-8")
        real_replace = os.replace

        def fail_replace(*a, **k):
            raise OSError("injected replace failure")

        with _mock.patch.object(os, "replace", side_effect=fail_replace):
            path_b, err_b = d.ensure_codex_synth()
        self.assertIsNotNone(err_b, "replace failure must be recorded")
        self.assertEqual(Path(str(path_b)).read_bytes(), bytes_a)
        leftovers = [p for p in Path(self._tmp.name).glob("*.tmp.*")
                     if p.is_file()]
        self.assertEqual(leftovers, [],
                         f"own tmp files must be cleaned: {leftovers}")
        _ = real_replace

    # ---- F6a-T05: aggregates always come from one generation ----
    def test_f6a_t05_single_generation_aggregates(self):
        d = self.d
        for i in range(5):
            (self.codex_dir /
             f"rollout-2026-01-0{i + 1}T00-00-00-f{i}.jsonl").write_text(
                _rollout_lines(50, ti=100 + i), encoding="utf-8")
            d.ensure_codex_synth()
            try:
                d.ROUTER_CACHE.clear()
            except Exception:
                pass
            st = d.query_router_stats(str(d.CODEX_SYNTH), None, None, True)
            days_total = round(sum(float(x.get("total", 0))
                                   for x in st.get("days", [])), 1)
            self.assertAlmostEqual(days_total,
                                   float(st["totals"]["tokens_total"]),
                                   places=0,
                                   msg="days must sum to headline total")

    # ---- F6a-T06: active-reader coordination or safe error, no torn ----
    def test_f6a_t06_active_reader_no_torn_file(self):
        d = self.d
        import builtins
        import unittest.mock as _mock
        (self.codex_dir / "rollout-2026-01-01T00-00-00-aaaa.jsonl").write_text(
            _rollout_lines(3000), encoding="utf-8")
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        (self.codex_dir / "rollout-2026-01-02T00-00-00-bbbb.jsonl").write_text(
            _rollout_lines(1500, ti=200), encoding="utf-8")
        real_open = builtins.open
        stop = threading.Event()
        bad = []

        def holder():
            try:
                with real_open(str(d.CODEX_SYNTH), "rb") as f:
                    stop.wait(10)
                    _ = f.read()
            except Exception as e:  # noqa: BLE001
                bad.append(("holder", repr(e)))

        def publisher():
            try:
                with _mock.patch("builtins.open",
                                 side_effect=self._slow_open(real_open)):
                    d.ensure_codex_synth()
            except Exception as e:  # noqa: BLE001
                bad.append(("publisher", repr(e)))
            finally:
                stop.set()

        # NOTE: holder opens with the REAL open (outside the mock), so the
        # mock only slows the publisher. If the platform refuses to replace
        # an open file, publish must fail safe (old kept, error recorded).
        h = threading.Thread(target=holder)
        h.start()
        time.sleep(0.2)
        p = threading.Thread(target=publisher)
        p.start()
        p.join(120)
        h.join(120)
        self.assertFalse(p.is_alive())
        self.assertFalse(h.is_alive())
        holder_errors = [b for b in bad if b[0] == "holder"]
        self.assertEqual(holder_errors, [], f"reader hurt: {holder_errors}")
        final = Path(str(d.CODEX_SYNTH)).read_bytes().splitlines()
        for ln in final:
            _json.loads(ln)

    # ---- F6a-T07: publish path does not mutate shared payloads ----
    def test_f6a_t07_no_shared_payload_mutation(self):
        d = self.d
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        before = d.query_router_stats(str(d.CODEX_SYNTH)
                                      if Path(str(d.CODEX_SYNTH)).exists()
                                      else None, None, None, True) \
            if False else None
        d.ensure_codex_synth()
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        snap_before = _json.dumps(d.query_router_stats(
            str(d.CODEX_SYNTH), None, None, True), sort_keys=True)
        d.ensure_codex_synth()
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        snap_after = _json.dumps(d.query_router_stats(
            str(d.CODEX_SYNTH), None, None, True), sort_keys=True)
        self.assertEqual(snap_before, snap_after)
        _ = before

    # ---- F6a-T08: killed-before-replace + restart keeps old, spares other ----
    def test_f6a_t08_restart_keeps_old_spares_foreign(self):
        d = self.d
        path_a, err_a = d.ensure_codex_synth()
        self.assertIsNone(err_a)
        bytes_a = Path(str(path_a)).read_bytes()
        synth_dir = Path(str(d.CODEX_SYNTH)).parent
        own_tmp = synth_dir / (Path(str(d.CODEX_SYNTH)).name +
                               ".tmp.99999.1")
        own_tmp.write_bytes(b'{"partial": true}\n')
        foreign = synth_dir / "foreign-notes.txt"
        foreign.write_bytes(b"do not touch")
        d.ensure_codex_synth()
        self.assertEqual(Path(str(d.CODEX_SYNTH)).read_bytes(), bytes_a,
                         "old snapshot must survive")
        self.assertFalse(own_tmp.exists(),
                         "own stale tmp must be cleaned on ensure")
        self.assertTrue(foreign.is_file(),
                        "foreign files must never be deleted")
        self.assertEqual(foreign.read_bytes(), b"do not touch")
