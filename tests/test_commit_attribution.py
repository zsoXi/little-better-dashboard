"""F7 commit-window attribution tests.

Verifies that the commit panels present usage in the 24h window before
each commit as an explicitly non-additive, global time-window signal,
never as exact commit cost or confirmed project attribution.
"""

import json
import os
import subprocess
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
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("dashboard import failed: %s" % exc)


GLOBAL_NOTE = "Global time window; may include other projects. Windows overlap. Rows must not be summed."
LOCAL_NOTE = "Project-matched time window; windows overlap; rows must not be summed; not commit cost."


class TestF7CommitWindows(unittest.TestCase):
    def setUp(self):
        self._saved_env = {}
        self._tmpdir = tempfile.TemporaryDirectory(prefix="f7-commit-")
        self.tmp = Path(self._tmpdir.name)
        for name in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
            self._saved_env[name] = os.environ.get(name)
            os.environ[name] = str(self.tmp)
        self.d = _load_dashboard()

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if hasattr(self.d, "ROUTER_CACHE"):
            self.d.ROUTER_CACHE.clear()
        self._tmpdir.cleanup()

    def _git_env(self):
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_AUTHOR_NAME"] = "T"
        env["GIT_AUTHOR_EMAIL"] = "t@example.invalid"
        env["GIT_COMMITTER_NAME"] = "T"
        env["GIT_COMMITTER_EMAIL"] = "t@example.invalid"
        return env

    def _git(self, args, cwd, env=None):
        subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            env=env or self._git_env(),
            check=True,
            capture_output=True,
            text=True,
        )

    def _repo(self, name, commits):
        path = self.tmp / name
        path.mkdir()
        self._git(["init", "-q"], path)
        for i, (when, content) in enumerate(commits):
            (path / "work.txt").write_text(content, encoding="utf-8")
            env = self._git_env()
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
            self._git(["add", "-A"], path, env)
            self._git(["commit", "-q", "-m", "c%d" % i, "--no-gpg-sign"], path, env)
        return path

    def _events(self, rows, name="events.jsonl"):
        path = self.tmp / name
        lines = []
        for row in rows:
            tot = float(row["total"])
            out_tok = 20.0 if tot >= 20 else tot
            in_tok = tot - out_tok
            event = {
                "at": row["at"],
                "model": "muse-spark",
                "provider": "openai",
                "status": 200,
                "outcome": "success",
                "inputTokens": in_tok,
                "cachedInputTokens": 0,
                "outputTokens": out_tok,
                "totalTokens": tot,
                "reasoningTokens": 0,
                "cacheWriteInputTokens": 0,
            }
            lines.append(json.dumps(event))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _query(self, events_path, worktrees):
        return self.d.query_router_stats(str(events_path), None, worktrees, True)

    def _meta(self, stats):
        self.assertIn("commit_windows", stats)
        meta = stats["commit_windows"]
        self.assertIsInstance(meta, dict)
        for key in ("method", "window_hours", "scope", "overlap_possible", "additive", "note"):
            self.assertIn(key, meta)
        return meta

    def test_f7_t01_two_commits_share_window_not_additive(self):
        repo = self._repo(
            "alpha",
            [
                ("2026-09-16T14:00:00+00:00", "one"),
                ("2026-09-16T15:00:00+00:00", "two"),
            ],
        )
        events = self._events([{"at": "2026-09-16T13:00:00Z", "total": 120}])
        stats = self._query(events, [("alpha", str(repo))])
        rows = stats["commits"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row[7], 120.0)
            self.assertEqual(row[8], 1)
        self.assertEqual(sum(row[7] for row in rows), 240.0)
        self.assertEqual(stats["totals"]["tokens_total"], 120.0)
        meta = self._meta(stats)
        self.assertIs(meta["additive"], False)
        self.assertIn("must not be summed", str(meta["note"]))

    def test_f7_t02_two_repos_global_scope(self):
        a = self._repo("alpha", [("2026-09-16T14:00:00+00:00", "a")])
        b = self._repo("beta", [("2026-09-16T14:30:00+00:00", "b")])
        events = self._events([{"at": "2026-09-16T13:00:00Z", "total": 90}])
        stats = self._query(events, [("alpha", str(a)), ("beta", str(b))])
        rows = stats["commits"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[3] for row in rows}, {"alpha", "beta"})
        for row in rows:
            self.assertEqual(row[7], 90.0)
            self.assertEqual(row[8], 1)
        meta = self._meta(stats)
        self.assertEqual(meta["scope"], "global")
        self.assertIs(meta["overlap_possible"], True)

    def test_f7_t03_window_boundaries_inclusive(self):
        repo = self._repo("alpha", [("2026-09-16T14:00:00+00:00", "only")])
        events = self._events(
            [
                {"at": "2026-09-15T13:59:59.999Z", "total": 500},
                {"at": "2026-09-15T14:00:00Z", "total": 30},
                {"at": "2026-09-16T14:00:00Z", "total": 40},
                {"at": "2026-09-16T14:00:00.001Z", "total": 600},
            ]
        )
        stats = self._query(events, [("alpha", str(repo))])
        rows = stats["commits"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][8], 2)
        self.assertEqual(rows[0][7], 70.0)
        self.assertEqual(stats["totals"]["tokens_total"], 1170.0)
        meta = self._meta(stats)
        self.assertEqual(meta["window_hours"], 24)
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("inclusive", readme)

    def test_f7_t04_timezone_equivalence(self):
        repo = self._repo("alpha", [("2026-09-16T14:00:00+00:00", "only")])
        utc = "2026-09-16T13:00:00Z"
        plus_two = "2026-09-16T15:00:00+02:00"
        self.assertEqual(self.d._router_time_key(utc), self.d._router_time_key(plus_two))
        events = self._events([{"at": utc, "total": 50}, {"at": plus_two, "total": 60}])
        stats = self._query(events, [("alpha", str(repo))])
        rows = stats["commits"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][7], 110.0)
        self.assertEqual(rows[0][8], 2)
        meta = self._meta(stats)
        self.assertEqual(meta["method"], "time_window")

    def test_f7_t05_warnings_in_ui_and_export(self):
        repo = self._repo("alpha", [("2026-09-16T14:00:00+00:00", "only")])
        events = self._events([{"at": "2026-09-16T13:00:00Z", "total": 10}])
        stats = self._query(events, [("alpha", str(repo))])
        meta = self._meta(stats)
        self.assertEqual(meta["note"], GLOBAL_NOTE)
        self.assertEqual(meta["scope"], "global")
        self.assertIs(meta["additive"], False)
        self.assertIs(meta["overlap_possible"], True)
        src = (REPO_ROOT / "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn(GLOBAL_NOTE, src)
        self.assertIn(LOCAL_NOTE, src)
        self.assertGreaterEqual(src.count("Usage in the 24h before each commit"), 2)
        self.assertIn("opencode-tokens-commits.csv", src)
        self.assertIn("codex-tokens-commits.csv", src)
        for column in ("overlap_possible", "additive", "window_hours"):
            self.assertIn(column, src)

    def test_f7_t06_no_inferred_attribution(self):
        a = self._repo("alpha", [("2026-09-16T14:00:00+00:00", "a")])
        b = self._repo("beta", [("2026-09-16T14:00:00+00:00", "b")])
        events = self._events([{"at": "2026-09-16T13:00:00Z", "total": 77}])
        stats = self._query(events, [("alpha", str(a)), ("beta", str(b))])
        rows = stats["commits"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[3] for row in rows}, {"alpha", "beta"})
        for row in rows:
            self.assertIsInstance(row, list)
            self.assertEqual(len(row), 9)
            self.assertEqual(row[7], 77.0)
        self._meta(stats)
        src = (REPO_ROOT / "opencode_dashboard.py").read_text(encoding="utf-8")
        start = src.index("timestamp-only join")
        end = src.index("MAX_COMMIT_ROWS", start)
        block = src[start : end + len("MAX_COMMIT_ROWS")]
        self.assertNotIn("cwd", block)
        self.assertNotIn("title", block)
        self.assertNotIn("subsystem_for_file", block)


if __name__ == "__main__":
    unittest.main()
