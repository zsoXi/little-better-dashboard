"""Antigravity source tests.

Pure-helper checks plus query_antigravity round-trips on synthetic
read-only sqlite fixtures. No real Antigravity data is read.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD = REPO_ROOT / "opencode_dashboard.py"


def _load_dashboard():
    import opencode_dashboard as d
    return d


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _step_meta(seconds):
    sub = b"\x08" + _varint(seconds)
    return b"\x0a" + _varint(len(sub)) + sub


class TestAgHelpers(unittest.TestCase):
    def test_varint(self):
        d = _load_dashboard()
        self.assertEqual(d._ag_varint(b"\x96\x01", 0), (150, 2))
        with self.assertRaises(ValueError):
            d._ag_varint(b"", 0)
        with self.assertRaises(ValueError):
            d._ag_varint(b"\xff" * 12, 0)

    def test_step_time_roundtrip(self):
        d = _load_dashboard()
        t = 1789771939
        self.assertEqual(d._ag_step_time(_step_meta(t)), t)
        sub = b"\x08" + _varint(t) + b"\x10" + _varint(12345)
        meta = b"\x0a" + _varint(len(sub)) + sub
        self.assertEqual(d._ag_step_time(meta), t)

    def test_step_time_rejects(self):
        d = _load_dashboard()
        self.assertIsNone(d._ag_step_time(None))
        self.assertIsNone(d._ag_step_time(b""))
        self.assertIsNone(d._ag_step_time(b"\x10\x01"))
        self.assertIsNone(d._ag_step_time(b"\x0a"))
        self.assertIsNone(d._ag_step_time(_step_meta(5)))
        sub = b"\x08" + b"\xff" * 12
        self.assertIsNone(d._ag_step_time(b"\x0a" + _varint(len(sub)) + sub))

    def test_workspace(self):
        d = _load_dashboard()
        self.assertEqual(d._ag_workspace('["file:///d%3A/Project%20X"]'), "d:/Project X")
        self.assertEqual(d._ag_workspace("file:///c%3A/Work/Dir"), "c:/Work/Dir")
        self.assertEqual(d._ag_workspace(""), "")
        self.assertEqual(d._ag_workspace(None), "")

    def test_local_iso(self):
        d = _load_dashboard()
        exp = datetime.fromisoformat("2026-09-20 19:59:46.100+00:00")
        exp = exp.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        self.assertEqual(d._ag_local_iso("2026-09-20 19:59:46.100+00:00"), exp)
        self.assertEqual(d._ag_local_iso("2026-09-20T19:59:46"), "2026-09-20T19:59:46")
        self.assertEqual(d._ag_local_iso(""), "")


class TestQueryAntigravity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-ag-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "conversations").mkdir()
        self.t1 = 1789771939
        con = sqlite3.connect(str(self.root / "conversation_summaries.db"))
        con.execute("""CREATE TABLE conversation_summaries (
            conversation_id TEXT, title TEXT, preview TEXT, step_count INTEGER,
            last_modified_time TEXT, last_user_input_time TEXT,
            workspace_uris TEXT, status TEXT)""")
        rows = [
            ("c-1", "Fix hover text", "preview one", 3,
             "2026-09-20 19:59:46.1+00:00", "2026-09-20 19:59:22.4+00:00",
             '["file:///d%3A/Project%20X"]', "CASCADE_RUN_STATUS_IDLE"),
            ("c-2", "", "preview two", 1,
             "2026-09-14 00:36:57.5+00:00", "2026-09-14 00:36:53.1+00:00",
             "file:///c%3A/Work/Dir", "CASCADE_RUN_STATUS_RUNNING"),
        ]
        con.executemany(
            "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        self._make_steps("c-1", [
            (0, 15, _step_meta(self.t1)),
            (1, 132, _step_meta(self.t1 + 60)),
            (2, 15, _step_meta(self.t1 + 120)),
        ])
        self._make_steps("c-2", [(0, 14, b"garbage")])

    def _make_steps(self, conv, rows):
        con = sqlite3.connect(str(self.root / "conversations" / (conv + ".db")))
        con.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, step_type INTEGER, metadata BLOB)")
        con.executemany("INSERT INTO steps (idx, step_type, metadata) VALUES (?,?,?)", rows)
        con.commit()
        con.close()

    def test_totals_and_sources(self):
        d = _load_dashboard()
        out = d.query_antigravity(self.root)
        self.assertTrue(out["ok"])
        s1, s2 = out["sources"]
        self.assertTrue(s1["exists"])
        self.assertEqual(s1["conversations"], 2)
        self.assertTrue(s2["exists"])
        self.assertEqual(s2["files"], 2)
        self.assertEqual(s2["steps"], 4)
        self.assertFalse(s2["truncated"])
        t = out["totals"]
        self.assertEqual(t["conversations"], 2)
        self.assertEqual(t["steps"], 4)
        self.assertEqual(t["step_types"],
                         [{"type": 15, "count": 2}, {"type": 132, "count": 1}])

    def test_days_local_buckets(self):
        d = _load_dashboard()
        out = d.query_antigravity(self.root)
        day1 = datetime.fromtimestamp(self.t1).strftime("%Y-%m-%d")
        got = {x["date"]: {"steps": x["steps"], "conversations": x["conversations"]}
               for x in out["days"]}
        self.assertEqual(got, {day1: {"steps": 3, "conversations": 1}})
        self.assertEqual(out["range"], day1 + " \u2192 " + day1)

    def test_conversations_parsed(self):
        d = _load_dashboard()
        out = d.query_antigravity(self.root)
        convs = out["conversations"]
        self.assertEqual([c["id"] for c in convs], ["c-1", "c-2"])
        c1 = convs[0]
        self.assertEqual(c1["title"], "Fix hover text")
        self.assertEqual(c1["workspace"], "d:/Project X")
        self.assertEqual(c1["status"], "IDLE")
        self.assertEqual(c1["steps"], 3)
        exp_last = datetime.fromisoformat("2026-09-20 19:59:46.1+00:00")
        exp_last = exp_last.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        self.assertEqual(c1["last"], exp_last)
        c2 = convs[1]
        self.assertEqual(c2["title"], "")
        self.assertEqual(c2["workspace"], "c:/Work/Dir")
        self.assertEqual(c2["status"], "RUNNING")
        self.assertEqual(out["totals"]["latest"], exp_last)

    def test_missing_root(self):
        d = _load_dashboard()
        out = d.query_antigravity(Path(self._tmp.name) / "nope")
        self.assertTrue(out["ok"])
        self.assertEqual([s["exists"] for s in out["sources"]], [False, False])
        self.assertEqual(out["days"], [])
        self.assertEqual(out["totals"]["steps"], 0)
        self.assertEqual(out["range"], "no activity")


class TestFrontendWiring(unittest.TestCase):
    def test_antigravity_tab_wired(self):
        src = DASHBOARD.read_text(encoding="utf-8")
        for token in ['id="tab-antigravity"', 'id="view-antigravity"',
                      "function renderAntigravity()", "url:'/api/antigravity'",
                      "AG_DIR_DEFAULT", '"--antigravity-dir"',
                      "TAB==='antigravity'&&AG", "saved==='antigravity'",
                      'path.startswith("/api/antigravity")', "query_antigravity("]:
            self.assertIn(token, src)


if __name__ == "__main__":
    unittest.main()
