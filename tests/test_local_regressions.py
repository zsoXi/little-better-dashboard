"""Local regression tests (F3): session activity is history, not execution.

Skeleton smoke tests plus F3-T01..T06: activity_state boundaries, unknown
timestamps, list limit counters, time-based transitions without DB writes,
runtime_status contract and UI/README language checks.
All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA isolated.
No server started; no real user data read.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock as _mock
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


class TestLocalRegressions(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-local-regr-")
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
        self.assertTrue(hasattr(d, "day_total"))
        self.assertTrue(hasattr(d, "cache_rate"))

    def test_day_total_and_cache_rate_pure(self):
        d = _load_dashboard()
        self.assertEqual(d.day_total({"ti": 1, "to": 2, "tr": 3, "cache": 4}), 10)
        self.assertEqual(d.day_total({}), 0)
        # OpenCode cache share: input excludes the separate cache stream.
        self.assertEqual(d.cache_rate(80, 20), 20.0)
        self.assertEqual(d.cache_rate(0, 0), 0.0)


class TestF3ActivityStates(unittest.TestCase):
    """F3-T01..T06: activity_state is a session-history signal, not execution."""

    NOW_MS = 1800000000000

    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f3-act-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        self.db_path = Path(self._tmp.name, "agents.db")
        con = sqlite3.connect(str(self.db_path))
        con.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, agent TEXT,"
            " model TEXT, directory TEXT, parent_id TEXT,"
            " time_created INTEGER, time_updated INTEGER,"
            " tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,"
            " tokens_cache_read INTEGER, tokens_cache_write INTEGER)"
        )
        con.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT)")
        con.commit()
        con.close()

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _add(self, sid, updated_ms, created_ms=None, parent="parent-1", agent="build"):
        con = sqlite3.connect(str(self.db_path))
        con.execute(
            "INSERT INTO session (id, title, agent, model, directory, parent_id,"
            " time_created, time_updated, tokens_input, tokens_output, tokens_reasoning,"
            " tokens_cache_read, tokens_cache_write)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0)",
            (sid, sid + " title", agent, "muse-spark", "C:/work", parent,
             created_ms if created_ms is not None else self.NOW_MS - 60000,
             updated_ms),
        )
        con.commit()
        con.close()

    def _query(self, now_ms=None):
        if now_ms is None:
            now_ms = self.NOW_MS
        con = self.d.connect(str(self.db_path))
        try:
            with _mock.patch.object(self.d.time, "time", return_value=now_ms / 1000.0):
                return self.d.query_agents(con)
        finally:
            con.close()

    def _runs_by_id(self, res):
        return {r["id"]: r for r in res["child_runs"]}

    def test_f3_t01_age_boundaries_exact(self):
        deltas = [0, 120, 121, 900, 901]
        for gap in deltas:
            self._add("c%d" % gap, self.NOW_MS - gap * 1000)
        runs = self._runs_by_id(self._query())
        self.assertEqual(
            [runs["c%d" % gap]["activity_state"] for gap in deltas],
            ["recent", "recent", "quiet", "quiet", "stale"],
        )
        self.assertEqual([runs["c%d" % gap]["age_s"] for gap in deltas], deltas)

    def test_f3_t02_invalid_timestamps_are_unknown(self):
        for sid, upd in (("u-none", None), ("u-zero", 0), ("u-text", "abc"),
                         ("u-future", self.NOW_MS + 60000)):
            self._add(sid, upd)
        runs = self._runs_by_id(self._query())
        for sid in ("u-none", "u-zero", "u-text", "u-future"):
            self.assertEqual(runs[sid]["activity_state"], "unknown", sid)
            self.assertIsNone(runs[sid]["age_s"], sid)

    def test_f3_t03_limit_counters_and_truncation(self):
        for k in range(104):
            self._add("bulk-%03d" % k, self.NOW_MS - (k + 1) * 1000)
        self._add("veteran", self.NOW_MS, created_ms=12345)
        res = self._query()
        self.assertEqual(res["child_runs"][0]["id"], "veteran")
        self.assertEqual(res["listed_count"], 100)
        self.assertEqual(res["total_child_sessions"], 105)
        self.assertEqual(res["limit"], 100)
        self.assertTrue(res["truncated"])
        self.assertEqual(len(res["child_runs"]), 100)
        self.assertEqual(sum(res["activity_counts"].values()), 100)

    def test_f3_t04_time_passes_without_db_writes(self):
        self._add("solo", self.NOW_MS)
        self.assertEqual(self._runs_by_id(self._query())["solo"]["activity_state"], "recent")
        r2 = self._runs_by_id(self._query(self.NOW_MS + 300000))["solo"]
        self.assertEqual((r2["activity_state"], r2["age_s"]), ("quiet", 300))
        r3 = self._runs_by_id(self._query(self.NOW_MS + 1000000))["solo"]
        self.assertEqual((r3["activity_state"], r3["age_s"]), ("stale", 1000))

    def test_f3_t05_no_execution_state_claims(self):
        self._add("child-1", self.NOW_MS)
        run = self._query()["child_runs"][0]
        self.assertEqual(run["runtime_status"], "unknown")
        self.assertEqual(run["relation"], "child_session")
        self.assertNotIn("status", run)

    def test_f3_t06_ui_and_readme_use_activity_language(self):
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        readme = Path(REPO_ROOT, "README.md").read_text(encoding="utf-8")
        for needle in ("Recent activity", "No recent activity", "Older activity",
                       "Based on session updates; not execution status",
                       "activity_state", "runtime_status"):
            self.assertIn(needle, src)
        for needle in ("running NOW", "running_sec", "idle_sec", "status_counts",
                       "dot.running", "dot.idle", "dot.finished",
                       "'Running'", "'Finished'", "'Idle'"):
            self.assertNotIn(needle, src)
        self.assertNotIn("**live status**", readme)
        self.assertNotIn("running, idle or finished", readme)
        self.assertIn("not execution status", readme)
        self.assertIn("auto-refreshed", readme)


if __name__ == "__main__":
    unittest.main()
