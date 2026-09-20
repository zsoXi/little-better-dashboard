"""Jev source tests.

query_jev over synthetic audit logs: mock exclusion, verdict/model
aggregation, malformed lines, missing files and bounded reads, plus the
static frontend wiring (tab, view, route, export branch).
No real Jev install is touched; no server is started.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD = REPO_ROOT / "opencode_dashboard.py"


def _load_dashboard():
    import opencode_dashboard as d
    return d


def _line(**rec):
    return json.dumps(rec)


def _session(t, sid, mock=False):
    return {"time": t, "event": "session_created", "session_id": sid,
            "backend": "browser", "goal_hash": "h", "mock": mock,
            "previous": None, "hash": "x"}


def _proposal(t, sid, verdict, ms, model="jev-1.13.0", mock=False,
              inp=100, out=10):
    return {"time": t, "event": "proposal", "session_id": sid,
            "proposal_id": "p", "verdict": verdict, "action_hash": None,
            "model": model, "usage": {"input_tokens": inp, "output_tokens": out},
            "elapsed_ms": ms, "mock": mock, "previous": None, "hash": "x"}


class TestQueryJev(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-jev-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, lines):
        p = self.dir / name
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return p

    def test_aggregates_and_mock_exclusion(self):
        d = _load_dashboard()
        t0 = 1758096000
        p = self._write("audit.jsonl", [
            _line(**_session(t0, "s1")),
            _line(**_session(t0, "sm", mock=True)),
            _line(**_proposal(t0, "s1", "ESCALATE", 1000.0)),
            _line(**_proposal(t0, "s1", "NO_MATCH", 3000.0)),
            _line(**_proposal(t0, "sm", "ALLOW", 500.0, mock=True)),
            _line(time=t0, event="stop", session_id="s1"),
        ])
        out = d.query_jev([p])
        self.assertTrue(out["ok"])
        t = out["totals"]
        self.assertEqual(t["proposals"], 2)
        self.assertEqual(t["sessions"], 1)
        self.assertEqual(t["mock_skipped"], 2)
        self.assertEqual(t["input_tokens"], 200)
        self.assertEqual(t["output_tokens"], 20)
        self.assertEqual(t["avg_ms"], 2000.0)
        self.assertEqual(t["verdicts"], [{"verdict": "ESCALATE", "proposals": 1},
                                         {"verdict": "NO_MATCH", "proposals": 1}])
        self.assertEqual(t["models"], [{"model": "jev-1.13.0", "proposals": 2}])
        day = d._jev_local_iso(t0)[:10]
        self.assertEqual(out["days"], [{
            "date": day, "proposals": 2, "sessions": 1, "input_tokens": 200,
            "output_tokens": 20, "elapsed_ms": 4000.0, "avg_ms": 2000.0}])
        self.assertEqual(len(out["recent"]), 2)
        for r in out["recent"]:
            self.assertRegex(r["at"], re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"))
        self.assertEqual(out["sources"][0]["events"], 6)
        self.assertEqual(out["sources"][0]["stops"], 1)
        self.assertEqual(out["range"], day + " → " + day)

    def test_bad_lines_missing_and_duplicate_paths(self):
        d = _load_dashboard()
        t0 = 1758096000
        p = self._write("audit.jsonl", [
            _line(**_proposal(t0, "s1", "ALLOW", 100.0)),
            "{not json",
            "",
            json.dumps([1, 2, 3]),
        ])
        missing = self.dir / "nope.jsonl"
        out = d.query_jev([p, missing, p])
        self.assertEqual(len(out["sources"]), 2)
        src, miss = out["sources"]
        self.assertTrue(src["exists"])
        self.assertEqual(src["bad_lines"], 2)
        self.assertEqual(src["truncated"], False)
        self.assertFalse(miss["exists"])
        self.assertEqual(out["totals"]["proposals"], 1)

    def test_max_events_keeps_newest(self):
        d = _load_dashboard()
        t0 = 1758096000
        lines = [_line(**_proposal(t0 + i, "s1", "ALLOW", float(i)))
                 for i in range(8)]
        p = self._write("audit.jsonl", lines)
        old = d.JEV_MAX_EVENTS
        d.JEV_MAX_EVENTS = 3
        try:
            out = d.query_jev([p])
        finally:
            d.JEV_MAX_EVENTS = old
        self.assertEqual(out["totals"]["proposals"], 3)
        self.assertTrue(out["sources"][0]["truncated"])
        self.assertEqual(out["totals"]["avg_ms"], 6.0)

    def test_read_cap_drops_partial_first_line(self):
        d = _load_dashboard()
        t0 = 1758096000
        lines = [_line(**_proposal(t0, "s1", "ALLOW", float(i)))
                 for i in range(6)]
        p = self._write("audit.jsonl", lines)
        old = d.JEV_READ_CAP
        d.JEV_READ_CAP = 400
        try:
            out = d.query_jev([p])
        finally:
            d.JEV_READ_CAP = old
        self.assertTrue(out["sources"][0]["truncated"])
        self.assertGreaterEqual(out["totals"]["proposals"], 1)
        self.assertLessEqual(out["totals"]["proposals"], 6)


class TestJevFrontendWiring(unittest.TestCase):
    def _source(self):
        return DASHBOARD.read_text(encoding="utf-8")

    def test_tab_view_and_route_present(self):
        src = self._source()
        for token in ['id="tab-jev"', 'id="view-jev"', "function renderJev()",
                      "{key:'jev',url:'/api/jev'", "let JV=null;",
                      'id="tab-jev-cnt"', 'elif path.startswith("/api/jev"):']:
            self.assertIn(token, src)

    def test_switch_restore_and_export_wired(self):
        src = self._source()
        self.assertIn("$('tab-jev').onclick=()=>setTab('jev');", src)
        self.assertIn("saved==='router'||saved==='jev'", src)
        self.assertIn("TAB==='jev'&&JV", src)
        self.assertIn("if(key==='jev')", src)


if __name__ == "__main__":
    unittest.main()
