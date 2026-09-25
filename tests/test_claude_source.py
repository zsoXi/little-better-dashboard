"""Claude Code source tests.

Pure-helper checks plus query_claude round-trips on synthetic transcript
files. No real Claude Code data is read.
"""
import json
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


def _usage_line(mid, rid, ts, model, ti, cn, cr, to, th=0, cwd="D:\\Proj"):
    obj = {
        "type": "assistant",
        "timestamp": ts,
        "cwd": cwd,
        "sessionId": "sess-a",
        "requestId": rid,
        "message": {
            "id": mid,
            "model": model,
            "usage": {
                "input_tokens": ti,
                "cache_creation_input_tokens": cn,
                "cache_read_input_tokens": cr,
                "output_tokens": to,
                "output_tokens_details": {"thinking_tokens": th},
            },
        },
    }
    return json.dumps(obj)


def _title_line(title):
    return json.dumps({"type": "custom-title", "customTitle": title,
                       "sessionId": "sess-a"})


class TestCcHelpers(unittest.TestCase):
    def test_local_iso(self):
        d = _load_dashboard()
        out = d._cc_local_iso("2026-09-20T12:00:00.000Z")
        self.assertRegex(out, r"^2026-09-20T\d{2}:00:00$")
        self.assertEqual(d._cc_local_iso(""), "")
        self.assertEqual(d._cc_local_iso("2026-09-20 12:00:00"), "2026-09-20T12:00:00")
        self.assertEqual(d._cc_local_iso("garbage"), "garbage")

    def test_parse_file_caches(self):
        d = _load_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s1.jsonl"
            p.write_text(_usage_line("m1", "r1", "2026-09-20T12:00:00Z",
                                     "claude-opus-5-5", 10, 100, 1000, 5, 2),
                         encoding="utf-8")
            e1, meta1, bad1 = d._cc_parse_file(p)
            self.assertEqual(len(e1), 1)
            self.assertEqual(meta1["title"], "")
            self.assertEqual(bad1, 0)
            self.assertEqual(e1[0][4:], (10, 100, 1000, 5, 2))
            # identical stat -> cache hit returns the same lists
            e2, meta2, bad2 = d._cc_parse_file(p)
            self.assertIs(e2, e1)


class TestQueryClaude(unittest.TestCase):
    def _fixture(self, tmp):
        root = Path(tmp)
        proj = root / "projects" / "proj-a"
        proj.mkdir(parents=True)
        s1 = proj / "s1.jsonl"
        s1.write_text("\n".join([
            _title_line("Test Session"),
            _usage_line("m1", "r1", "2026-09-20T12:00:00.000Z", "claude-opus-5-5", 10, 100, 1000, 5, 2),
            _usage_line("m2", "r2", "2026-09-20T13:00:00.000Z", "claude-opus-5-5", 20, 200, 2000, 7, 3),
            '{"type":"assistant","message":{"usage":',
        ]), encoding="utf-8")
        s2 = proj / "s2.jsonl"
        s2.write_text("\n".join([
            _usage_line("m1", "r1", "2026-09-20T12:00:00.000Z", "claude-opus-5-5", 10, 100, 1000, 5, 2),
            _usage_line("m3", "r3", "2026-09-21T09:00:00.000Z", "claude-sonnet-5", 30, 300, 3000, 9, 4),
        ]), encoding="utf-8")
        return root

    def test_totals_dedupe_and_days(self):
        d = _load_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            res = d.query_claude(root)
            self.assertTrue(res["ok"])
            t = res["totals"]
            self.assertEqual(t["requests"], 3)          # m1 counted once
            self.assertEqual(t["input"], 60)
            self.assertEqual(t["cache_new"], 600)
            self.assertEqual(t["cache_read"], 6000)
            self.assertEqual(t["output"], 21)
            self.assertEqual(t["thinking"], 9)
            self.assertEqual(t["total"], 60 + 600 + 6000 + 21)
            day1 = d._cc_local_iso("2026-09-20T12:00:00.000Z")[:10]
            day2 = d._cc_local_iso("2026-09-21T09:00:00.000Z")[:10]
            self.assertEqual([x["date"] for x in res["days"]], [day1, day2])
            self.assertEqual(res["days"][0]["requests"], 2)
            self.assertEqual(res["days"][1]["requests"], 1)
            self.assertEqual(res["range"], f"{day1} → {day2}")
            self.assertEqual([m["model"] for m in res["models"]],
                             ["claude-opus-5-5", "claude-sonnet-5"])
            self.assertEqual(res["models"][0]["total"], 30 + 300 + 3000 + 12)

    def test_sessions_and_source(self):
        d = _load_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = self._fixture(tmp)
            res = d.query_claude(root)
            src = res["sources"][0]
            self.assertEqual(src["files"], 2)
            self.assertEqual(src["requests"], 3)
            self.assertEqual(src["bad_lines"], 1)
            self.assertTrue(src["exists"])
            sess = {s["id"]: s for s in res["sessions"]}
            self.assertEqual(len(sess), 2)
            s1 = sess["s1"]                      # duplicate m1 kept by first file
            self.assertEqual(s1["title"], "Test Session")
            self.assertEqual(s1["reqs"], 2)
            self.assertEqual(s1["tokens"], 30 + 300 + 3000 + 12)
            self.assertEqual(s1["cwd"], "D:\\Proj")
            self.assertEqual(s1["models"], ["claude-opus-5-5"])
            self.assertEqual(sess["s2"]["reqs"], 1)
            # sorted newest first
            self.assertEqual(res["sessions"][0]["id"], "s2")

    def test_missing_root(self):
        d = _load_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            res = d.query_claude(Path(tmp) / "nope")
            self.assertTrue(res["ok"])
            self.assertFalse(res["sources"][0]["exists"])
            self.assertEqual(res["days"], [])
            self.assertEqual(res["totals"]["requests"], 0)
            self.assertEqual(res["range"], "no activity")


class TestClaudeFrontendWiring(unittest.TestCase):
    def setUp(self):
        self.src = DASHBOARD.read_text(encoding="utf-8")

    def test_backend_wired(self):
        for token in ["def query_claude(", "CLAUDE_DIR_DEFAULT", "--claude-dir",
                      'path.startswith("/api/claude")', "claude_dir = None",
                      "Handler.claude_dir = args.claude_dir"]:
            self.assertIn(token, self.src)

    def test_frontend_wired(self):
        for token in ['id="tab-claude"', 'id="view-claude"', 'id="cl-tbl"',
                      "function renderClaude(){", "let CL=null;",
                      "$('tab-claude').onclick=()=>setTab('claude');",
                      "saved==='claude'", "else if(cc)renderClaude();",
                      "TAB==='claude'&&CL", "claude-usage.json",
                      "if(key==='claude')"]:
            self.assertIn(token, self.src)


if __name__ == "__main__":
    unittest.main()
