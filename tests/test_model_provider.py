"""Model/provider dimension tests.

Pure-helper checks plus query_sessions round-trips on a synthetic
read-only sqlite fixture. No server started; no real user data read.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_dashboard():
    import opencode_dashboard as d
    return d


class TestModelProviderHelpers(unittest.TestCase):
    def test_parse_provider(self):
        d = _load_dashboard()
        raw = json.dumps({"id": "muse-spark-1.3-contributor",
                          "providerID": "meta", "variant": "xhigh"})
        self.assertEqual(d.parse_provider(raw), "meta")
        self.assertEqual(d.parse_provider(""), "")
        self.assertEqual(d.parse_provider(None), "")
        self.assertEqual(d.parse_provider("not-json"), "")
        self.assertEqual(d.parse_provider(json.dumps({"id": "x"})), "")

    def test_model_key(self):
        d = _load_dashboard()
        raw = json.dumps({"id": "deepseek-v4.1-flash",
                          "providerID": "opencode-go-3", "variant": "max"})
        self.assertEqual(d.model_key(raw), "opencode-go-3/deepseek-v4.1-flash")
        self.assertEqual(d.model_key(json.dumps({"id": "m"})), "m")
        self.assertEqual(d.model_key(None), "-")


class TestQuerySessionsProvider(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-model-prov-")
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "opencode.db"
        con = sqlite3.connect(str(self.db))
        con.execute("""CREATE TABLE session (
            id TEXT PRIMARY KEY, title TEXT, agent TEXT, model TEXT,
            directory TEXT, parent_id TEXT,
            time_created INTEGER, time_updated INTEGER)""")
        rows = [
            ("ses_1", "muse via meta", "build",
             json.dumps({"id": "muse-spark-1.3-contributor",
                         "providerID": "meta", "variant": "xhigh"}),
             "D:/x", None, 10, 20),
            ("ses_2", "muse via go", "build",
             json.dumps({"id": "muse-spark-1.3-contributor",
                         "providerID": "opencode-go-2", "variant": "default"}),
             "D:/x", None, 11, 21),
            ("ses_3", "no model", "build", None, "D:/x", None, 12, 22),
        ]
        con.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()

    def test_rows_parsed(self):
        d = _load_dashboard()
        con = d.connect(str(self.db))
        try:
            out = d.query_sessions(con)
        finally:
            con.close()
        got = {r["id"]: (r["model"], r["provider"]) for r in out["rows"]}
        self.assertEqual(got["ses_1"], ("muse-spark-1.3-contributor", "meta"))
        self.assertEqual(got["ses_2"], ("muse-spark-1.3-contributor", "opencode-go-2"))
        self.assertEqual(got["ses_3"], ("-", ""))

    def test_model_filter_substring(self):
        d = _load_dashboard()
        con = d.connect(str(self.db))
        try:
            out = d.query_sessions(con, model="meta")
            self.assertEqual([r["id"] for r in out["rows"]], ["ses_1"])
            out = d.query_sessions(con, model="muse")
            self.assertEqual(len(out["rows"]), 2)
            out = d.query_sessions(con, model="opencode-go-2")
            self.assertEqual([r["id"] for r in out["rows"]], ["ses_2"])
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
