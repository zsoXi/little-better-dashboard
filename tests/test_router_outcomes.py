"""F4 RED-then-GREEN: unknown instead of synthetic successes, tokens kept.

Baseline lie: ensure_codex_synth stamps status 200 on every record, and
parse_router_events treats status 0/missing as success, so ok/err counters
are meaningless. Honest contract (spec F4 ch.8): synth emits
status:null + outcome:unknown; normalizer maps 200-299->success,
400-599->error, missing/null/0/bad/1xx/3xx->unknown; usage_known stays
separate from outcome (unknown/error with tokens still count); every
aggregation carries success/error/unknown summing to read events;
synth UI shows 'Outcome unavailable' + unknown count with 'Usage events'
wording, success-rate over known only with coverage (N/A when 0 known),
averages over metered events, latency N/A for synth, old 200-index
never trusted as success (schema bump + synth-context override).

All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA
isolated. Live checks boot in-process on 127.0.0.1:0 with per-instance
bearer token (F2 contract); unauthenticated private access must 401.
No fixed ports (never 8765-8770), no real user data, stdlib only.
"""
import datetime as _dt
import json as _json
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
        raise RuntimeError(f"opencode_dashboard import failed: {e}")


F4_TODAY = _dt.date.today().isoformat()


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(_json.dumps(r) + "\n")


def _router_rec(date_s, model="muse-spark", status=200, ti=100, to=20,
                cache=40, tr=5, total=120, hour=12, provider="codex",
                duration=5, omit=(), extra=None):
    rec = {"at": f"{date_s}T{hour:02d}:00:00", "model": model,
           "provider": provider, "inputTokens": ti, "outputTokens": to,
           "cachedInputTokens": cache, "reasoningTokens": tr,
           "totalTokens": total, "durationMs": duration}
    if status != "OMIT":
        rec["status"] = status
    for k in omit:
        rec.pop(k, None)
    if extra:
        rec.update(extra)
    return rec


def _query(d, path, full_scan=False):
    try:
        d.ROUTER_CACHE.clear()
    except Exception:
        pass
    return d.query_router_stats(path, None, None, full_scan)


def _codex_rollout_text(ts="2026-09-16T12:00:00", model="muse-spark",
                        ti=100, cache=40, to=20, total=120, tr=5):
    lines = [
        {"type": "session_meta", "timestamp": ts,
         "payload": {"model_provider": "codex"}},
        {"type": "turn_context", "timestamp": ts,
         "payload": {"model": model}},
        {"type": "token_usage_record", "timestamp": ts,
         "payload": {"usage": {"input_tokens": ti,
                               "cached_input_tokens": cache,
                               "output_tokens": to,
                               "total_tokens": total,
                               "reasoning_output_tokens": tr,
                               "cache_write_input_tokens": 0}}},
    ]
    return "\n".join(_json.dumps(r) for r in lines) + "\n"


class TestRouterOutcomes(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-router-outcomes-")
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
        self.assertTrue(hasattr(d, "router_is_free"))
        self.assertTrue(hasattr(d, "blank_router_stats"))

    def test_router_outcome_helpers_pure(self):
        d = _load_dashboard()
        self.assertTrue(d.router_is_free("muse-spark-1.3-contributor-free"))
        self.assertFalse(d.router_is_free("muse-spark-1.3-contributor"))
        # Empty timestamp parses to (None, None) without touching disk.
        self.assertEqual(d._router_event_day_hour(None), (None, None))
        self.assertEqual(d._router_event_day_hour(""), (None, None))
        # Blank router payload keeps the success/error/unknown shape.
        blank = d.blank_router_stats()
        self.assertIn("totals", blank)
        self.assertIn("errors", blank["totals"])


def _patch_codex_env(d, tmpdir):
    """Point CODEX_DIR/CODEX_SYNTH at temp dirs; save/restore globals."""
    saved = {}
    for k in ("CODEX_DIR", "CODEX_SYNTH", "_codex_ev_cache", "_codex_ev_sig"):
        saved[k] = getattr(d, k)
    codex_dir = Path(tmpdir) / "codex-sessions"
    codex_dir.mkdir(parents=True, exist_ok=True)
    synth_path = Path(tmpdir) / "synth.jsonl"
    d.CODEX_DIR = codex_dir
    d.CODEX_SYNTH = synth_path
    d._codex_ev_cache = {}
    d._codex_ev_sig = None
    try:
        d.ROUTER_CACHE.clear()
    except Exception:
        pass
    return saved, codex_dir, synth_path


def _restore_codex_env(d, saved):
    for k, v in saved.items():
        try:
            setattr(d, k, v)
        except Exception:
            pass
    try:
        d.ROUTER_CACHE.clear()
    except Exception:
        pass


class TestF4UnknownOutcomes(unittest.TestCase):
    """F4-T01..T10 honest-unknown contract. RED on baseline (lie), GREEN after fix."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f4-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self._servers = []
        self.addCleanup(self._cleanup_servers)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _cleanup_servers(self):
        for srv, thr in self._servers:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        for _, thr in self._servers:
            try:
                thr.join(timeout=5)
            except Exception:
                pass
        self._servers = []
        try:
            import opencode_dashboard as d
            try:
                d.ROUTER_CACHE.clear()
            except Exception:
                pass
        except Exception:
            pass

    # F4-T01: single synth event 100/20/40/5 -> 0/0/1, total 120
    def test_f4_t01_synth_single_event_unknown_120(self):
        d = _load_dashboard()
        saved, codex_dir, synth_path = _patch_codex_env(d, self._tmp.name)
        self.addCleanup(lambda: _restore_codex_env(d, saved))
        (codex_dir / "rollout-f4t01.jsonl").write_text(
            _codex_rollout_text(), encoding="utf-8")
        out, synth_err = d.ensure_codex_synth(force=True)
        self.assertIsNone(synth_err)
        rows = Path(out).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(rows), 1)
        synth_rec = _json.loads(rows[0])
        # Honest synth: no confirmed HTTP status, explicit unknown outcome.
        self.assertIsNone(synth_rec.get("status"),
                          msg=f"synth must stamp status null, got {synth_rec.get('status')!r}")
        self.assertEqual(synth_rec.get("outcome"), "unknown")
        stats = d.query_router_stats(out, None, None, True)
        t = stats["totals"]
        self.assertEqual(t.get("unknown"), 1, msg=f"totals={t}")
        self.assertEqual(t.get("ok"), 0)
        self.assertEqual(t.get("errors"), 0)
        self.assertEqual(t.get("tokens_total"), 120)

    # F4-T02: bad statuses never become success, never crash
    def test_f4_t02_bad_statuses_are_unknown_not_success(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t02.jsonl")
        recs = [
            _router_rec(F4_TODAY, model="m-missing", status="OMIT", ti=10, to=5,
                        cache=2, tr=1, total="OMIT", omit=("totalTokens",)),
            dict(_router_rec(F4_TODAY, model="m-null", ti=10, to=5, cache=2,
                             tr=1, total=15), status=None),
            dict(_router_rec(F4_TODAY, model="m-zero", ti=10, to=5, cache=2,
                             tr=1, total=15), status=0),
            dict(_router_rec(F4_TODAY, model="m-str", ti=10, to=5, cache=2,
                             tr=1, total=15), status="oops"),
            dict(_router_rec(F4_TODAY, model="m-bool", ti=10, to=5, cache=2,
                             tr=1, total=15), status=True),
            dict(_router_rec(F4_TODAY, model="m-list", ti=10, to=5, cache=2,
                             tr=1, total=15), status=[200]),
        ]
        _write_jsonl(p, recs)
        stats = _query(d, p)  # must not raise
        t = stats["totals"]
        self.assertEqual(t.get("ok"), 0, msg=f"bad statuses must not be success: {t}")
        self.assertEqual(t.get("errors"), 0)
        self.assertEqual(t.get("unknown"), 6, msg=f"totals={t}")
        self.assertEqual(t.get("requests"), 6)
        events, _ = d.parse_router_events(p)
        for e in events:
            self.assertEqual(e.get("outcome"), "unknown", msg=f"event={e}")
            self.assertIsNone(e.get("status"))

    # F4-T03: real 200/201/401/429/500 classified, 429/5xx kept
    def test_f4_t03_real_statuses_classified(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t03.jsonl")
        recs = [_router_rec(F4_TODAY, model=f"m-{s}", status=s, hour=10 + i)
                for i, s in enumerate([200, 201, 401, 429, 500])]
        _write_jsonl(p, recs)
        stats = _query(d, p)
        t = stats["totals"]
        self.assertEqual(t.get("ok"), 2, msg=f"{t}")
        self.assertEqual(t.get("errors"), 3)
        self.assertEqual(t.get("unknown"), 0)
        self.assertEqual(t.get("err429"), 1)
        self.assertEqual(t.get("err500"), 1)
        self.assertEqual(t.get("requests"), 5)

    # F4-T04: mixed 200+120, 429 no-usage, unknown+30 -> 1/1/1, 150 tokens
    def test_f4_t04_mixed_tokens_150(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t04.jsonl")
        ok_rec = _router_rec(F4_TODAY, model="m-ok")
        err_rec = {"at": f"{F4_TODAY}T13:00:00", "model": "m-err",
                   "provider": "codex", "status": 429, "durationMs": 5}
        unk_rec = {"at": f"{F4_TODAY}T14:00:00", "model": "m-unk",
                   "provider": "codex", "inputTokens": 20, "outputTokens": 10,
                   "durationMs": 5}
        _write_jsonl(p, [ok_rec, err_rec, unk_rec])
        stats = _query(d, p)
        t = stats["totals"]
        self.assertEqual(t.get("requests"), 3, msg=f"{t}")
        self.assertEqual(t.get("ok"), 1)
        self.assertEqual(t.get("errors"), 1)
        self.assertEqual(t.get("unknown"), 1)
        self.assertEqual(t.get("tokens_total"), 150, msg=f"{t}")

    # F4-T05: success rate 50% over known 2/3, not 33%/100%
    def test_f4_t05_success_rate_over_known_with_coverage(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t05.jsonl")
        ok_rec = _router_rec(F4_TODAY, model="m-ok")
        err_rec = {"at": f"{F4_TODAY}T13:00:00", "model": "m-err",
                   "provider": "codex", "status": 429, "durationMs": 5}
        unk_rec = {"at": f"{F4_TODAY}T14:00:00", "model": "m-unk",
                   "provider": "codex", "inputTokens": 20, "outputTokens": 10,
                   "durationMs": 5}
        _write_jsonl(p, [ok_rec, err_rec, unk_rec])
        stats = _query(d, p)
        t = stats["totals"]
        known = t.get("known_outcomes")
        self.assertEqual(known, 2, msg=f"{t}")
        rate = t.get("success_rate")
        self.assertIsNotNone(rate, msg=f"{t}")
        self.assertAlmostEqual(float(rate), 0.5, msg=f"{t}")
        # Coverage 2 known of 3 events.
        cov = t.get("coverage") if isinstance(t.get("coverage"), dict) else None
        if cov is not None:
            self.assertEqual(cov.get("known"), 2)
            self.assertEqual(cov.get("total"), 3)
        else:
            self.assertEqual(t.get("known_outcomes"), 2)
            self.assertEqual(t.get("requests"), 3)
        # Zero-known case is N/A, not 0%/100% (covered in T01 synth: known 0).
        saved2, codex_dir, synth_path = _patch_codex_env(d, self._tmp.name)
        try:
            (codex_dir / "rollout-f4t05.jsonl").write_text(
                _codex_rollout_text(), encoding="utf-8")
            out, synth_err = d.ensure_codex_synth(force=True)
            self.assertIsNone(synth_err)
            s2 = d.query_router_stats(out, None, None, True)
            self.assertIsNone(s2["totals"].get("success_rate"),
                              msg=f"synth with 0 known must be N/A: {s2['totals']}")
        finally:
            _restore_codex_env(d, saved2)

    # F4-T06: unknown usage present in day/model/hour/activity
    def test_f4_t06_unknown_tokens_everywhere(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t06.jsonl")
        unk_rec = {"at": f"{F4_TODAY}T12:00:00", "model": "m-unk6",
                   "provider": "codex", "inputTokens": 20, "outputTokens": 10,
                   "cachedInputTokens": 4, "reasoningTokens": 1,
                   "durationMs": 5}
        _write_jsonl(p, [unk_rec])
        stats = _query(d, p)
        self.assertEqual(stats["totals"].get("tokens_total"), 30)
        self.assertEqual(stats["totals"].get("unknown"), 1)
        by_day = {x["date"]: x for x in stats["days"]}
        self.assertIn(F4_TODAY, by_day)
        self.assertEqual(by_day[F4_TODAY]["total"], 30)
        self.assertEqual(by_day[F4_TODAY].get("unknown"), 1)
        by_model = {m[0]: m for m in stats["models"]}
        self.assertIn("m-unk6", by_model)
        self.assertEqual(by_model["m-unk6"][2], 30)
        # Model row carries unknown count (appended index) when available.
        if len(by_model["m-unk6"]) > 14:
            self.assertEqual(by_model["m-unk6"][14], 1)
        hr = _dt.datetime.fromisoformat(f"{F4_TODAY}T12:00:00").hour
        self.assertEqual(stats["hours"][hr], 1)
        # Activity heatmap reuses authoritative day total (30, not 0).
        cell = next((s for s in stats["activity"] if s.get("date") == F4_TODAY), None)
        self.assertIsNotNone(cell)
        self.assertEqual(cell["total"], 30)

    # F4-T07: explicit zero vs missing usage both keep known result
    def test_f4_t07_zero_vs_missing_measurement(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t07.jsonl")
        explicit_zero = _router_rec(F4_TODAY, model="m-zero", status=200, ti=0,
                                    to=0, cache=0, tr=0, total=0)
        no_usage = {"at": f"{F4_TODAY}T13:00:00", "model": "m-nousage",
                    "provider": "codex", "status": 200, "durationMs": 5}
        _write_jsonl(p, [explicit_zero, no_usage])
        stats = _query(d, p)
        t = stats["totals"]
        self.assertEqual(t.get("requests"), 2, msg=f"{t}")
        self.assertEqual(t.get("ok"), 2)
        self.assertEqual(t.get("tokens_total"), 0)
        events, _ = d.parse_router_events(p)
        by_model = {e["model"]: e for e in events}
        # Explicit zero is a known zero measurement; missing is unmetered.
        self.assertTrue(by_model["m-zero"].get("usage_known"))
        self.assertFalse(by_model["m-zero"].get("usage_partial"))
        self.assertFalse(by_model["m-nousage"].get("usage_known"))
        self.assertTrue(by_model["m-nousage"].get("usage_partial"))
        self.assertEqual(t.get("unmetered"), 1, msg=f"{t}")

    # F4-T08: old 200-index never trusted as success in synth context
    def test_f4_t08_old_synth_200_invalidated(self):
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "f4t08.jsonl")
        old_style = _router_rec(F4_TODAY, model="m-old", status=200)
        _write_jsonl(p, [old_style])
        real_stats = _query(d, p, full_scan=False)
        self.assertEqual(real_stats["totals"].get("ok"), 1)
        # Same bytes read as a synth generation must be unknown (migration).
        synth_stats = _query(d, p, full_scan=True)
        self.assertEqual(synth_stats["totals"].get("ok"), 0,
                         msg=f"old 200 must not survive as success: {synth_stats['totals']}")
        self.assertEqual(synth_stats["totals"].get("unknown"), 1)
        self.assertEqual(synth_stats["totals"].get("tokens_total"), 120)
        # Schema bump actually shipped.
        self.assertGreaterEqual(int(getattr(d, "SYNTH_SCHEMA", 0)), 3)

    # F4-T09: synth duration null -> latency N/A overall (and no 0ms fake)
    def test_f4_t09_synth_latency_na(self):
        d = _load_dashboard()
        saved, codex_dir, synth_path = _patch_codex_env(d, self._tmp.name)
        self.addCleanup(lambda: _restore_codex_env(d, saved))
        (codex_dir / "rollout-f4t09.jsonl").write_text(
            _codex_rollout_text(), encoding="utf-8")
        out, synth_err = d.ensure_codex_synth(force=True)
        self.assertIsNone(synth_err)
        raw = [_json.loads(l) for l in Path(out).read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertTrue(raw)
        for r in raw:
            self.assertIsNone(r.get("durationMs"), msg=f"{r}")
        stats = d.query_router_stats(out, None, None, True)
        self.assertTrue(stats.get("latency_na"))
        self.assertEqual(stats.get("req_word"), "Usage events")
        # No fake 0ms average standing in for a measurement.
        self.assertTrue(stats["totals"].get("avg_ms") is None
                        or stats.get("latency_na") is True)
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("latency_na", src)

    # F4-T10: UI third state + period sums + exports (with authed live boot)
    def test_f4_t10_ui_unknown_exports_and_authed_api(self):
        import secrets as _secrets
        import threading
        import urllib.request
        import urllib.error
        d = _load_dashboard()
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("Outcome unavailable", src)
        self.assertIn("Usage events", src)
        self.assertIn("unknown", src.lower())
        # Period aggregation carries the third counter.
        self.assertIn("unknown", src[src.index("function rSumDays"):src.index("function rSumDays") + 800])
        # Status UI + filter + export all handle unknown.
        self.assertIn("Unknown", src)
        # Export header includes unknown (no silent ok/err-only CSV).
        exp_idx = src.index("function exportData")
        exp_end = src.index("\nfunction ", exp_idx + 10)
        self.assertIn("unknown", src[exp_idx:exp_end].lower())
        # Requests table never prints raw null/200 as confirmed success.
        self.assertIn("Outcome unavailable", src[src.index("function renderRouterRequests") - 2000:
                                                 src.index("function renderRouterRequests") + 4000]
                      if "function renderRouterRequests" in src else src)
        # Live boot: F1 120 via AUTHENTICATED /api/router, unauth 401 (F2 intact).
        p = str(Path(self._tmp.name) / "f4t10.jsonl")
        _write_jsonl(p, [_router_rec(F4_TODAY)])
        saved_h = {}
        for k in ("db_path", "router_events", "router_limits"):
            saved_h[k] = getattr(d.Handler, k)
        d.Handler.db_path = str(Path(self._tmp.name) / "missing.sqlite3")
        d.Handler.router_events = p
        d.Handler.router_limits = None
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
        try:
            srv.auth_token = _secrets.token_urlsafe(32)
        except Exception:
            pass
        try:
            d.Handler.auth_token = srv.auth_token
        except Exception:
            pass
        token = srv.auth_token
        host, port = srv.server_address
        self.assertEqual(host, "127.0.0.1")
        thr = threading.Thread(target=srv.serve_forever, daemon=True)
        thr.start()
        self._servers.append((srv, thr))
        # Unauthenticated private access must 401 (F2 contract).
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/router", timeout=10) as r:
                self.fail(f"unauth must 401, got {r.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/router",
            headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8")
        payload = _json.loads(body)
        self.assertEqual(payload["totals"]["tokens_total"], 120)
        self.assertEqual(payload["totals"].get("unknown"), 0)
        self.assertEqual(payload["totals"].get("ok"), 1)


if __name__ == "__main__":
    unittest.main()
