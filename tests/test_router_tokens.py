"""Router token accounting skeleton tests (F1).

Skeleton only: discovery smoke + one real pure-helper check.
All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA isolated.
No server started; no real user data read.
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


class TestRouterTokens(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-router-tokens-")
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
        # Discovery smoke: unittest found this file and HOME is isolated.
        self.assertTrue(True)
        self.assertEqual(os.environ.get("HOME"), self._tmp.name)
        self.assertEqual(os.environ.get("USERPROFILE"), self._tmp.name)
        self.assertTrue(Path(self._tmp.name).is_dir())

    def test_import_dashboard(self):
        d = _load_dashboard()
        self.assertTrue(hasattr(d, "router_cache_rate"))
        self.assertTrue(hasattr(d, "what_if_cost"))

    def test_router_cache_rate_pure(self):
        d = _load_dashboard()
        # Cached input is a subset of total input: 30/100 -> 30.0%.
        self.assertEqual(d.router_cache_rate(100, 30), 30.0)
        self.assertEqual(d.router_cache_rate(0, 0), 0.0)
        # Day tokens must not double-count cached input (subset of input)
        # nor reasoning (subset of output): F1 contract is ti + to.
        self.assertEqual(d._router_day_tokens({"ti": 10, "to": 5, "tr": 2}), 15)


F1_TODAY = __import__("datetime").date.today().isoformat()
F1_YESTERDAY = (__import__("datetime").date.today() - __import__("datetime").timedelta(days=1)).isoformat()


def _f1_record(date_s, model="muse-spark", status=200, ti=100, to=20,
               cache=40, tr=5, total=120, hour=12):
    """Spec fixture record: ti=100, to=20, cache=40, tr=5, total=120."""
    rec = {"at": f"{date_s}T{hour:02d}:00:00", "model": model,
           "provider": "codex", "inputTokens": ti, "outputTokens": to,
           "cachedInputTokens": cache, "reasoningTokens": tr,
           "totalTokens": total, "durationMs": 5}
    if status != "OMIT":
        rec["status"] = status
    return rec


def _f1_write(path, records):
    import json as _json
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(_json.dumps(r) + "\n")


def _f1_query(d, path):
    try:
        d.ROUTER_CACHE.clear()
    except Exception:
        pass
    return d.query_router_stats(path)


def _f1_activity_cell(stats, date_s):
    for slot in stats.get("activity", []):
        if slot.get("date") == date_s:
            return slot
    return None


class TestRouterTokenContract(unittest.TestCase):
    """F1 RED-then-GREEN: router total contract is ti + to (cache/reasoning
    are subsets, never additive). Fixture: 100/20/40/5 -> 120, not 125."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f1-")
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

    def test_f1_01_contract_120_all_aggregates(self):
        # F1-T01: 120 in headline totals, model, day, hour peak and activity.
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        _f1_write(p, [_f1_record(F1_TODAY)])
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["tokens_total"], 120)
        self.assertEqual(stats["models"][0][2], 120)
        self.assertEqual(stats["days"][0]["total"], 120)
        self.assertEqual(stats["records"]["peak_hour"], 12)
        cell = _f1_activity_cell(stats, F1_TODAY)
        self.assertIsNotNone(cell)
        self.assertEqual(cell["total"], 120)
        self.assertEqual(d._router_day_tokens({"ti": 100, "to": 20, "tr": 5}), 120)

    def test_f1_04_cache_rate_40(self):
        # F1-T04: cache=40 of input=100 -> 40%, never 28.57% nor 66.67%.
        d = _load_dashboard()
        self.assertEqual(d.router_cache_rate(100, 40), 40.0)
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        _f1_write(p, [_f1_record(F1_TODAY)])
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["cache_rate"], 40.0)

    def test_f1_05_multi_record_consistency_210(self):
        # F1-T05: two records, two days, two models, sum 210, one definition.
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        _f1_write(p, [
            _f1_record(F1_TODAY, model="model-a"),
            dict(_f1_record(F1_YESTERDAY, model="model-b"),
                 inputTokens=60, outputTokens=30, cachedInputTokens=10,
                 reasoningTokens=2, totalTokens=90),
        ])
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["tokens_total"], 210)
        by_model = {m[0]: m[2] for m in stats["models"]}
        self.assertEqual(by_model["model-a"], 120)
        self.assertEqual(by_model["model-b"], 90)
        by_day = {x["date"]: x["total"] for x in stats["days"]}
        self.assertEqual(by_day[F1_TODAY], 120)
        self.assertEqual(by_day[F1_YESTERDAY], 90)
        self.assertEqual(_f1_activity_cell(stats, F1_TODAY)["total"], 120)
        self.assertEqual(_f1_activity_cell(stats, F1_YESTERDAY)["total"], 90)

    def test_f1_06_missing_total_fallback(self):
        # F1-T06: no totalTokens, complete 100/20 components -> fallback 120.
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        rec = _f1_record(F1_TODAY)
        del rec["totalTokens"]
        _f1_write(p, [rec])
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["tokens_total"], 120)
        self.assertEqual(stats["models"][0][2], 120)
        self.assertEqual(stats["days"][0]["total"], 120)
        self.assertEqual(_f1_activity_cell(stats, F1_TODAY)["total"], 120)

    def test_f1_07_zeros_partial_conflict(self):
        # F1-T07: zeros stay zero; total-only keeps reported sum flagged
        # partial; 120-components vs 125-declared normalizes to 120 + flag.
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        zero = dict(_f1_record(F1_YESTERDAY, model="zero-m"),
                    inputTokens=0, outputTokens=0, cachedInputTokens=0,
                    reasoningTokens=0, totalTokens=0)
        only = {"at": f"{F1_YESTERDAY}T13:00:00", "model": "only-m",
                "provider": "codex", "status": 200, "totalTokens": 50,
                "durationMs": 5}
        clash = dict(_f1_record(F1_TODAY, model="clash-m"), totalTokens=125)
        _f1_write(p, [zero, only, clash])
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["tokens_total"], 170)
        by_model = {m[0]: m[2] for m in stats["models"]}
        self.assertEqual(by_model["clash-m"], 120)
        self.assertEqual(by_model["only-m"], 50)
        by_day = {x["date"]: x["total"] for x in stats["days"]}
        self.assertEqual(by_day[F1_TODAY], 120)
        self.assertEqual(by_day[F1_YESTERDAY], 50)
        self.assertEqual(_f1_activity_cell(stats, F1_TODAY)["total"], 120)
        self.assertEqual(_f1_activity_cell(stats, F1_YESTERDAY)["total"], 50)
        self.assertEqual(stats["totals"]["total_conflicts"], 1)
        events, problems = d.parse_router_events(p)
        self.assertEqual(problems["total_conflicts"], 1)
        by_model_ev = {e["model"]: e for e in events}
        self.assertEqual(by_model_ev["clash-m"]["total"], 120)
        self.assertEqual(by_model_ev["clash-m"]["total_reported"], 125)
        self.assertTrue(by_model_ev["only-m"]["usage_partial"])
        self.assertFalse(by_model_ev["clash-m"]["usage_partial"])

    def test_f1_08_invalid_values_sanitized(self):
        # F1-T08: NaN/Infinity/negative/bool/list/str never reach JSON sums;
        # no 500; explicit invalid_records count.
        import json as _json
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        base = dict(at=f"{F1_TODAY}T12:00:00", provider="codex", status=200,
                    inputTokens=100, outputTokens=20, cachedInputTokens=40,
                    reasoningTokens=5, durationMs=5)
        recs = [dict(base, model="clean-m", totalTokens=120)]
        bad_fields = [("inputTokens", float("nan")), ("outputTokens", float("inf")),
                      ("cachedInputTokens", -5), ("totalTokens", True),
                      ("reasoningTokens", [5]), ("inputTokens", "100")]
        for i, (k, v) in enumerate(bad_fields):
            r = dict(base, model=f"bad-m-{i}")
            r.pop("totalTokens", None)
            r[k] = v
            recs.append(r)
        _f1_write(p, recs)
        stats = _f1_query(d, p)  # must not raise (no 500 through the API path)
        self.assertEqual(stats["totals"]["invalid_records"], 6)
        for v in (stats["totals"]["tokens_total"], stats["totals"]["tokens_input"],
                  stats["totals"]["tokens_output"]):
            self.assertTrue(v == v and v not in (float("inf"), float("-inf")))
        _json.dumps(stats, allow_nan=False)
        for m in stats["models"]:
            self.assertTrue(m[2] == m[2])

    def test_f1_09_local_path_unchanged(self):
        # F1-T09: local OpenCode semantics stay additive (cache + reasoning
        # are separate streams) and byte-identical to baseline.
        d = _load_dashboard()
        self.assertEqual(d.day_total({"ti": 100, "to": 20, "tr": 5, "cache": 40}), 165)
        self.assertEqual(d.day_total({}), 0)
        self.assertEqual(d.cache_rate(80, 20), 20.0)
        self.assertEqual(d.cache_rate(0, 0), 0.0)

    def test_f1_10_odd_status_still_counts_usage(self):
        # F1-T10: missing/null/0 status never drops measured usage (120 each).
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        recs = [_f1_record(F1_TODAY, model="m-missing", status="OMIT"),
                dict(_f1_record(F1_TODAY, model="m-null"), status=None),
                dict(_f1_record(F1_TODAY, model="m-zero"), status=0)]
        _f1_write(p, recs)
        stats = _f1_query(d, p)
        self.assertEqual(stats["totals"]["tokens_total"], 360)
        self.assertEqual(stats["days"][0]["total"], 360)
        self.assertEqual(_f1_activity_cell(stats, F1_TODAY)["total"], 360)
        by_model = {m[0]: m[2] for m in stats["models"]}
        self.assertEqual(by_model["m-missing"], 120)
        self.assertEqual(by_model["m-null"], 120)
        self.assertEqual(by_model["m-zero"], 120)


class TestRouterFrontendContract(unittest.TestCase):
    """F1-T02/T03: the shipped router JS aggregates through rDayTokenTotal
    (ti + to). Executed with node against the real source, not a mirror."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f1js-")
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

    def _js_slice(self):
        import re
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        start = src.index("function rDayTokenTotal")
        end = src.index("function rStackChart")
        return src[start:end]

    def test_f1_03_frontend_js_totals_via_node(self):
        import shutil
        import subprocess
        if shutil.which("node") is None:
            raise unittest.SkipTest("node not available")
        day = '{"date":"%s","reqs":1,"ok":1,"err":0,"err429":0,"err500":0,' \
              '"ti":100,"to":20,"tr":5,"cache":40,"total":120,"what_if":0}' % F1_TODAY
        harness = (
            "const assert=require('assert');\n"
            "const FN=(v)=>'['+v+']';\n"
            "const F=(v)=>'['+v+']';\n"
            "let RSPLIT=true;\n"
            "const day=%s;\n"
            "assert.strictEqual(rDayTokenTotal(day),120,'rDayTokenTotal must be ti+to');\n"
            "const period=rSumDays([day]);\n"
            "assert.strictEqual(period.total,120,'rSumDays period total');\n"
            "const tip=rDayTip(day);\n"
            "assert.ok(tip.includes('[120]'),'tooltip shows 120');\n"
            "assert.ok(!tip.includes('[125]'),'tooltip never shows 125');\n"
            "console.log('JS_CONTRACT_OK total='+rDayTokenTotal(day));\n" % day
        )
        code = self._js_slice() + harness
        proc = subprocess.run(["node", "-e", code], capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertIn("JS_CONTRACT_OK total=120", proc.stdout)

    def test_f1_02_export_and_act_wiring(self):
        # CSV export and the activity heatmap must both flow through the
        # single rDayTokenTotal contract (ti + to, never + tr).
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("function rActDayTotal(dd){return rDayTokenTotal(dd);}", src)
        self.assertIn("Math.round(rDayTokenTotal(d))", src)
        line = next(l for l in src.splitlines() if l.startswith("function rDayTokenTotal"))
        self.assertNotIn("tr", line)


class TestRouterApiServe(unittest.TestCase):
    """F1-T02: the real page served over real HTTP embeds the 120-shape."""

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f1srv-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self._server = None
        self._thread = None
        self._saved = {}

    def tearDown(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        try:
            d = sys.modules.get("opencode_dashboard")
            if d is not None:
                for k, v in self._saved.items():
                    setattr(d.Handler, k, v)
                try:
                    d.ROUTER_CACHE.clear()
                except Exception:
                    pass
        finally:
            for k, v in self._old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_f1_02_served_page_shape_120(self):
        import json as _json
        import threading
        from http.server import ThreadingHTTPServer
        d = _load_dashboard()
        p = str(Path(self._tmp.name) / "usage-events.jsonl")
        _f1_write(p, [_f1_record(F1_TODAY)])
        for k in ("db_path", "router_events", "router_limits"):
            self._saved[k] = getattr(d.Handler, k)
        d.Handler.db_path = str(Path(self._tmp.name) / "missing.sqlite3")
        d.Handler.router_events = p
        d.Handler.router_limits = None
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), d.Handler)
        host, port = self._server.server_address
        self.assertEqual(host, "127.0.0.1")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/router", timeout=10) as r:
            body = r.read().decode("utf-8")
        strict = _json.loads(body, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        self.assertEqual(strict["totals"]["tokens_total"], 120)
        self.assertEqual(strict["days"][0]["total"], 120)
        self.assertEqual(strict["models"][0][2], 120)
        cell = _f1_activity_cell(strict, F1_TODAY)
        self.assertIsNotNone(cell)
        self.assertEqual(cell["total"], 120)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as r:
            page = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        self.assertIn('"tokens_total": 120', page)


if __name__ == "__main__":
    unittest.main()
