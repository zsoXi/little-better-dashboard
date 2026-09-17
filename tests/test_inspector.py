"""Inspector skeleton tests (F6b).

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
        raise RuntimeError(f"opencode_dashboard import failed: {e}")


class TestInspector(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-inspector-")
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
        self.assertTrue(hasattr(d, "parse_model"))
        self.assertTrue(hasattr(d, "short_dir"))

    def test_inspector_display_helpers_pure(self):
        d = _load_dashboard()
        self.assertEqual(d.short_dir("/a/b/c"), "b/c")
        self.assertEqual(d.short_dir(""), "-")
        self.assertEqual(d._sstr(None), "")
        self.assertEqual(d._sstr("hello"), "hello")


# ---------------------------------------------------------------------------
# F6e (spec INSTRUKCJA §16): bounded inspector, no per-message query.
# Pagination with a stable (time_created, id) cursor, batched part fetching
# with per-message caps, a bounded serialized response with explicit
# truncation flags, controlled handling of bad inputs, and read-only SQLite.
# All fixtures synthetic in temp dirs; isolated HOME; stdlib only.
# ---------------------------------------------------------------------------
import json as _json
import sqlite3 as _sqlite3
import threading as _threading
import time as _time


def _f6e_schema(con):
    con.executescript(
        """
CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, agent TEXT, model TEXT,
 directory TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER,
 tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
 tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost REAL,
 project_id TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
 time_created INTEGER);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
 data TEXT, time_created INTEGER);
CREATE TABLE project (id TEXT PRIMARY KEY, directory TEXT, worktree TEXT,
 path TEXT, vcs TEXT, name TEXT);
CREATE TABLE session_input (session_id TEXT, prompt TEXT, time_created INTEGER);
CREATE TABLE router (id TEXT PRIMARY KEY);
"""
    )


def _f6e_make_db(path, n_messages=100, parts_per_message=2, part_size=200):
    con = _sqlite3.connect(str(path))
    _f6e_schema(con)
    con.execute(
        "INSERT INTO session VALUES ('sid-insp','Inspect me','build',"
        "'muse-spark','C:/work',NULL,1800000000000,1800000100000,10,5,1,2,0,"
        "0.0,'proj-1')")
    con.execute(
        "INSERT INTO project VALUES ('proj-1','C:/work',NULL,NULL,'git','work')")
    msgs = []
    parts = []
    for i in range(n_messages):
        mid = "m%05d" % i
        msgs.append((mid, "sid-insp",
                     _json.dumps({"role": "user" if i % 2 == 0 else "assistant",
                                  "agent": "build", "model": "muse-spark",
                                  "summary": "s" * 40}),
                     1800000000000 + i * 1000))
        for j in range(parts_per_message):
            parts.append(("%s-p%d" % (mid, j), mid, "sid-insp",
                          _json.dumps({"type": "text", "text": "x" * part_size}),
                          1800000000000 + i * 1000 + j))
    con.executemany("INSERT INTO message VALUES (?,?,?,?)", msgs)
    con.executemany("INSERT INTO part VALUES (?,?,?,?,?)", parts)
    con.commit()
    con.close()


class TestF6eInspector(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f6e-")
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

    def _db(self, name="insp.db"):
        return Path(self._tmp.name) / name

    def test_f6e_t01_first_page_limit_has_more_cursor(self):
        d = _load_dashboard()
        db = self._db()
        _f6e_make_db(db, n_messages=120, parts_per_message=1)
        con = d.connect(str(db))
        try:
            out = d.query_inspect(con, "sid-insp")
            self.assertTrue(out.get("found"))
            self.assertEqual(len(out["messages"]), 50,
                             "default page must be 50 messages")
            self.assertTrue(out.get("has_more"),
                            "has_more must be true with 120 messages")
            self.assertTrue(out.get("cursor"), "a cursor must be returned")
            first_ids = [m["id"] for m in out["messages"]]
            out2 = d.query_inspect(con, "sid-insp", cursor=out["cursor"])
            second_ids = [m["id"] for m in out2["messages"]]
            self.assertTrue(second_ids)
            self.assertFalse(set(first_ids) & set(second_ids),
                             "second page must not repeat the first")
        finally:
            con.close()

    def test_f6e_t02_cursor_paging_stable_no_dup_loss(self):
        d = _load_dashboard()
        db = self._db()
        _f6e_make_db(db, n_messages=120, parts_per_message=1)

        def walk():
            con = d.connect(str(db))
            try:
                seen = []
                cursor = None
                pages = 0
                while True:
                    out = d.query_inspect(con, "sid-insp", cursor=cursor,
                                          limit=45)
                    seen.extend(m["id"] for m in out["messages"])
                    pages += 1
                    if not out.get("has_more"):
                        break
                    cursor = out.get("cursor")
                    self.assertTrue(cursor, "has_more requires a cursor")
                    self.assertLess(pages, 10, "paging must terminate")
                return seen
            finally:
                con.close()

        seen = walk()
        self.assertEqual(len(seen), 120)
        self.assertEqual(len(set(seen)), 120, "no duplicates across pages")
        self.assertEqual(seen, sorted(seen),
                         "stable ascending (time_created, id) order")
        self.assertEqual(walk(), seen, "paging repeats deterministically")

    def test_f6e_t03_huge_parts_unicode_bounded_response(self):
        d = _load_dashboard()
        db = self._db()
        con = _sqlite3.connect(str(db))
        _f6e_schema(con)
        con.execute(
            "INSERT INTO session VALUES ('sid-insp','Inspect me','build',"
            "'muse-spark','C:/work',NULL,1800000000000,1800000100000,10,5,1,"
            "2,0,0.0,'proj-1')")
        msgs = []
        parts = []
        for i in range(100):
            mid = "m%05d" % i
            msgs.append((mid, "sid-insp",
                         _json.dumps({"role": "user", "agent": "build",
                                      "model": "muse-spark",
                                      "summary": "zażółć gęślą jaźń 😀" * 20}),
                         1800000000000 + i * 1000))
            for j in range(25):
                text = ("ż" * 2000) + ("😀" * 50)
                if i < 2 and j == 0:
                    text = "x" * 2000000
                parts.append(("%s-p%02d" % (mid, j), mid, "sid-insp",
                              _json.dumps({"type": "text", "text": text},
                                          ensure_ascii=False),
                              1800000000000 + i * 1000 + j))
        con.executemany("INSERT INTO message VALUES (?,?,?,?)", msgs)
        con.executemany("INSERT INTO part VALUES (?,?,?,?,?)", parts)
        con.commit()
        con.close()
        con = d.connect(str(db))
        try:
            out = d.query_inspect(con, "sid-insp", limit=200)
            payload = _json.dumps(out)
            self.assertLessEqual(len(payload.encode("utf-8")),
                                 1024 * 1024 + 4096,
                                 "serialized response must stay within the budget")
            self.assertTrue(out.get("messages"), "page must not be empty")
            self.assertTrue(out.get("response_truncated"),
                            "size-driven shortening must be flagged")
            self.assertTrue(out.get("parts_truncated"),
                            "messages with >20 parts must be flagged")
            omitted = [m.get("parts_omitted", 0) for m in out["messages"]]
            self.assertTrue(max(omitted) >= 5,
                            "per-message omitted parts must be counted")
            for m in out["messages"]:
                for p in m.get("parts", []):
                    self.assertLessEqual(len(str(p.get("preview") or "")), 4096)
        finally:
            con.close()

    def test_f6e_t04_query_count_not_linear(self):
        d = _load_dashboard()
        small = self._db("small.db")
        big = self._db("big.db")
        _f6e_make_db(small, n_messages=2, parts_per_message=2)
        _f6e_make_db(big, n_messages=100, parts_per_message=2)

        class CountingCon:
            def __init__(self, con):
                self._con = con
                self.count = 0

            def execute(self, sql, params=()):
                self.count += 1
                return self._con.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._con, name)

        con_s = d.connect(str(small))
        con_b = d.connect(str(big))
        try:
            cc_s = CountingCon(con_s)
            out_s = d.query_inspect(cc_s, "sid-insp")
            self.assertTrue(out_s.get("found"))
            cc_b = CountingCon(con_b)
            out_b = d.query_inspect(cc_b, "sid-insp", limit=200)
            self.assertTrue(out_b.get("found"))
            self.assertLessEqual(
                cc_b.count, cc_s.count + 2,
                "query count must not grow with message count (%d vs %d)"
                % (cc_b.count, cc_s.count))
            self.assertLessEqual(cc_b.count, 10,
                                 "a page must need only a few SELECTs")
        finally:
            con_s.close()
            con_b.close()

    def test_f6e_t05_bad_inputs_controlled_no_injection(self):
        d = _load_dashboard()
        db = self._db()
        _f6e_make_db(db, n_messages=10, parts_per_message=1)
        con = d.connect(str(db))
        try:
            out = d.query_inspect(con, "nope")
            self.assertFalse(out.get("found"))
            for bad in ("junk", "1|x|y", "-5|-5",
                        "'; DROP TABLE message; --", "1||2"):
                try:
                    d.query_inspect(con, "sid-insp", cursor=bad)
                except Exception as e:  # noqa: BLE001
                    self.fail("bad cursor %r raised %r" % (bad, e))
            for bad_lim in ("-5", "0", "notanint", "99999999999999999999"):
                try:
                    d.query_inspect(con, "sid-insp", limit=bad_lim)
                except Exception as e:  # noqa: BLE001
                    self.fail("bad limit %r raised %r" % (bad_lim, e))
            huge = d.query_inspect(con, "sid-insp", limit=10 ** 9)
            self.assertLessEqual(len(huge["messages"]), 200,
                                 "huge limit must clamp to the hard maximum")
        finally:
            con.close()
        con2 = d.connect(str(db))
        try:
            n = con2.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        finally:
            con2.close()
        self.assertEqual(n, 10, "injection attempt must not touch the table")

    def test_f6e_t06_inspector_parallel_with_stats(self):
        d = _load_dashboard()
        db = self._db("big6.db")
        _f6e_make_db(db, n_messages=1000, parts_per_message=2)
        out = {}

        def work():
            con = d.connect(str(db))
            try:
                page = d.query_inspect(con, "sid-insp", limit=200)
                out["n"] = len(page["messages"])
            except Exception as e:  # noqa: BLE001
                out["err"] = repr(e)
            finally:
                con.close()

        t = _threading.Thread(target=work)
        t.start()
        t0 = _time.time()
        stats = d.cached_local_stats(str(db))
        dt = _time.time() - t0
        t.join(30)
        self.assertFalse(out.get("err"), out)
        self.assertFalse(t.is_alive(), "the inspector must finish")
        self.assertEqual(out.get("n"), 200, "hard page cap applies")
        self.assertIsInstance(stats, dict)
        self.assertLess(dt, 5.0,
                        "stats must not wait unboundedly on the inspector")


if __name__ == "__main__":
    unittest.main()
