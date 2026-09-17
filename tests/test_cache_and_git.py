"""Cache and git fingerprint skeleton tests (F6d).

Skeleton only: discovery smoke + one real pure-helper check.
All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA isolated.
No server started; no real user data read; no real git repos touched.
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


class TestCacheAndGit(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-cache-git-")
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
        self.assertTrue(hasattr(d, "what_if_cost"))
        self.assertTrue(hasattr(d, "_fingerprint_paths"))

    def test_pricing_and_fingerprint_pure(self):
        d = _load_dashboard()
        # Known free model: 1M in + 1M out at (0.10, 0.20) -> 0.30.
        self.assertEqual(
            d.what_if_cost("muse-spark-1.3-contributor-free", 1000000, 1000000), 0.3
        )
        self.assertEqual(d.what_if_cost("unknown-model", 1000000, 1000000), 0.0)
        # Fingerprint covers only synthetic temp paths, never the real HOME.
        probe = Path(self._tmp.name) / "synth.db"
        probe.write_bytes(b"synthetic")
        fp = d._fingerprint_paths([probe])
        self.assertTrue(any(str(probe) in str(entry[0]) for entry in fp))


# ---------------------------------------------------------------------------
# F6d (spec INSTRUKCJA ch.15): independent refresh, deadline covering the
# body, honest status. The JavaScript lives embedded in PAGE; structural
# invariants below pin the patched shape (RED before the patch), the
# behavioral proof runs in tools/run_browser_tests.py against a real
# browser. T10 additionally exercises the backend single-build gate for
# real (two concurrent cold builds -> at most one parse).
# ---------------------------------------------------------------------------
import json as _json
import re as _re
import threading as _threading
import time as _time


def _f6d_page_js():
    d = _load_dashboard()
    blocks = _re.findall(r"<script>(.*?)</script>", d.PAGE, _re.S)
    if not blocks:
        blocks = _re.findall(r"<script[^>]*>(.*?)</script>", d.PAGE, _re.S)
    return "\n".join(blocks)


def _f6d_rollout_lines():
    ts = "2026-09-16T12:00:00"
    return [
        {"type": "session_meta", "timestamp": ts,
         "payload": {"id": "f6d-1", "cwd": "C:/synthetic",
                     "model_provider": "codex"}},
        {"type": "turn_context", "timestamp": ts,
         "payload": {"model": "muse-spark-1.3-contributor-free"}},
        {"type": "token_usage_record", "timestamp": ts,
         "payload": {"input_tokens": 100, "cached_input_tokens": 40,
                     "output_tokens": 20, "total_tokens": 120,
                     "reasoning_output_tokens": 5}},
    ]


class TestF6dRefresh(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f6d-")
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

    def test_f6d_t01_sections_render_independently(self):
        js = _f6d_page_js()
        # One cycle is not a blocking Promise.all of eight fetches; each
        # section owns its render path and results are handled individually.
        self.assertNotIn("await Promise.all([", js)
        self.assertIn("Promise.allSettled(", js)
        self.assertIn("SECTIONS.map(", js)
        for url in ("'/api/stats'", "'/api/router'", "'/api/agents'",
                    "'/api/graph'", "'/api/sessions'", "'/api/projects'",
                    "'/api/signals'", "'/api/codex'"):
            self.assertIn(url, js, msg=url)

    def test_f6d_t02_deadline_covers_body(self):
        js = _f6d_page_js()
        self.assertIn("async function fetchJson", js)
        self.assertIn("AbortController", js)
        # The timer must stay armed through the whole body read and parse.
        self.assertIn("await Promise.race([resp.text(),deadline])", js)
        self.assertIn("'timeout after '", js)
        self.assertLess(js.index("JSON.parse"), js.index("clearTimeout(timer)"),
                        "clearTimeout must run after the parse, in finally")

    def test_f6d_t03_payload_errors_are_section_errors(self):
        js = _f6d_page_js()
        # HTTP 200 with explicit error / ok:false and invalid JSON are errors.
        self.assertIn("data&&data.error", js)
        self.assertIn("data.ok===false", js)
        self.assertIn("invalid JSON", js)
        # 'Updated' only when every section of the cycle succeeded.
        self.assertIn("'Updated '+", js)
        self.assertIn("'Partial update: '", js)
        self.assertIn("ok===SECTIONS.length", js)

    def test_f6d_t04_error_keeps_previous_data_stale(self):
        js = _f6d_page_js()
        self.assertIn("st.lastSuccess", js)
        self.assertIn("st.state='stale'", js)
        self.assertIn("unavailable: ", js)
        self.assertIn("console.warn", js)

    def test_f6d_t05_recovery_updates_success(self):
        js = _f6d_page_js()
        self.assertIn("st.state='success'", js)
        self.assertIn("st.lastSuccess=Date.now()", js)
        self.assertIn("st.error=null", js)

    def test_f6d_t06_late_response_never_overwrites_newer(self):
        js = _f6d_page_js()
        self.assertIn("st.gen++", js)
        self.assertIn("gen!==st.gen", js)
        # Search and inspector also carry generation guards.
        self.assertIn("_ssGen", js)
        self.assertIn("_inspGen", js)

    def test_f6d_t07_bounded_concurrency_no_queue(self):
        js = _f6d_page_js()
        self.assertIn("if(loading){pendingRefresh=true;return;}", js)
        self.assertIn("pendingRefresh&&!AUTH_FAILED", js)
        self.assertIn("st.inFlight", js)
        self.assertIn("if(st.inFlight)return true;", js)

    def test_f6d_t08_missing_source_isolated(self):
        js = _f6d_page_js()
        # No shared throw across sources: the old loop over the eight
        # responses is gone; each section fails on its own.
        self.assertNotIn("for(const _r of [r1,", js)
        self.assertIn("runSection", js)
        self.assertIn("if(e&&e.status===401){noteAuthFailure();return false;}", js)

    def test_f6d_t09_inspect_search_prompts_timeout(self):
        js = _f6d_page_js()
        self.assertRegex(js, r"function fetchSessions\(\)\{var p=[^}]*fetchJson\(")
        idx = js.index("async function inspect(sid)")
        self.assertIn("fetchJson(", js[idx:idx + 600])
        self.assertRegex(js, r"fetchJson\('/api/session/'\+encodeURIComponent")
        # A finished/deadline cycle always unlocks the manual refresh.
        self.assertIn("$('refresh').disabled=false;", js)

    def test_f6d_t10_single_build_and_injectable_interval(self):
        js = _f6d_page_js()
        self.assertIn("__ocdTimeouts", js)
        self.assertIn("refreshMs", js)
        d = _load_dashboard()
        self.assertTrue(hasattr(d, "_CODEX_BUILD_LOCK"))
        root = Path(self._tmp.name) / "rollouts"
        root.mkdir()
        (root / "rollout-f6d.jsonl").write_text(
            "\n".join(_json.dumps(x) for x in _f6d_rollout_lines()) + "\n",
            encoding="utf-8")
        saved = {}
        for name in ("CODEX_DIR", "CODEX_CACHE_DIR", "CODEX_INDEX",
                     "CODEX_SYNTH", "_codex_ev_sig", "_codex_last_error",
                     "_codex_last_ok"):
            saved[name] = getattr(d, name, None)
        saved_ev_state = dict(getattr(d, "_codex_ev_state", {}) or {})
        saved_file_state = dict(d._codex_file_state)
        saved_parse = d._parse_codex_events

        def _restore():
            for n, v in saved.items():
                try:
                    setattr(d, n, v)
                except Exception:
                    pass
            try:
                d._codex_ev_state.clear()
                d._codex_ev_state.update(saved_ev_state)
            except Exception:
                pass
            try:
                d._codex_file_state.clear()
                d._codex_file_state.update(saved_file_state)
            except Exception:
                pass
            d._parse_codex_events = saved_parse

        self.addCleanup(_restore)
        d.CODEX_DIR = root
        d.CODEX_CACHE_DIR = Path(self._tmp.name) / "cache"
        d.CODEX_INDEX = d.CODEX_CACHE_DIR / "codex_index.json"
        d.CODEX_SYNTH = Path(self._tmp.name) / "codex_router_events.jsonl"
        d._codex_ev_sig = None
        d._codex_last_error = None
        d._codex_last_ok = None
        try:
            d._codex_ev_cache.clear()
        except Exception:
            pass
        d._codex_ev_state.clear()
        d._codex_file_state.clear()

        counters = {"active": 0, "max": 0, "calls": 0}
        real_parse = d._parse_codex_events

        def slow_parse(p):
            counters["active"] += 1
            counters["max"] = max(counters["max"], counters["active"])
            counters["calls"] += 1
            _time.sleep(0.4)
            try:
                return real_parse(p)
            finally:
                counters["active"] -= 1

        d._parse_codex_events = slow_parse
        barrier = _threading.Barrier(2)
        out = {}

        def work(i):
            barrier.wait()
            t0 = _time.time()
            try:
                out[i] = ("ok", d.ensure_codex_synth(), _time.time() - t0)
            except Exception as e:
                out[i] = ("err", str(e), _time.time() - t0)

        threads = [_threading.Thread(target=work, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(15)
        self.assertEqual(counters["max"], 1,
                         "at most one synth build may run at a time")
        oks = [v for v in out.values() if v[0] == "ok"]
        self.assertEqual(len(oks), 2,
                         "concurrent callers share one build and all succeed")
        self.assertEqual(counters["calls"], 1,
                         "the parse ran once for two concurrent callers")


# ---------------------------------------------------------------------------
# F6f (spec INSTRUKCJA ch.17): expensive git calls must be TTL-cached per
# worktree, bounded per refresh, resilient to timeouts and non-repos, and
# always executed with list argv (never through a shell). The baseline runs
# git on every freshness check; these tests pin the cache + fallback shape.
# ---------------------------------------------------------------------------
import shutil as _shutil
import sqlite3 as _sqlite3
import subprocess as _subprocess
import unittest.mock as _mock


_F6F_SCHEMA = """
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


def _f6f_make_db(path, worktree):
    con = _sqlite3.connect(str(path))
    con.executescript(_F6F_SCHEMA)
    con.execute(
        "INSERT INTO project VALUES ('proj-1',?,?,NULL,'git','work')",
        (str(worktree), str(worktree)))
    con.commit()
    con.close()


def _f6f_git_env():
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    })
    return env


def _f6f_git(args, cwd, env):
    out = _subprocess.run(["git"] + args, cwd=str(cwd),
                          capture_output=True, text=True, timeout=30, env=env)
    if out.returncode != 0:
        raise RuntimeError("git %r failed: %s" % (args, out.stderr[:200]))
    return out


def _f6f_repo(root, name):
    d = root / name
    d.mkdir(parents=True)
    env = _f6f_git_env()
    _f6f_git(["init", "-q"], cwd=d, env=env)
    (d / "a.txt").write_text("one", encoding="utf-8")
    _f6f_git(["add", "a.txt"], cwd=d, env=env)
    _f6f_git(["commit", "-q", "-m", "one"], cwd=d, env=env)
    return d, env


class TestF6fGitCalls(unittest.TestCase):
    def setUp(self):
        if not _shutil.which("git"):
            self.skipTest("git not available")
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f6f-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        try:
            self.d.LOCAL_CACHE.clear()
        except Exception:
            pass

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _counting(self):
        real = _subprocess.run
        calls = []

        def counting(cmd, *a, **k):
            calls.append((cmd, k))
            return real(cmd, *a, **k)
        return calls, counting

    def test_f6f_t01_refreshes_before_ttl_bounded_subprocesses(self):
        d = self.d
        repo, _env = _f6f_repo(Path(self._tmp.name), "repo-one")
        db = Path(self._tmp.name) / "f6f1.db"
        _f6f_make_db(db, repo)
        wts = [(repo.name, str(repo))]
        calls, counting = self._counting()
        with _mock.patch.object(d.subprocess, "run", side_effect=counting):
            for _ in range(5):
                d.repo_heads(wts)
            for _ in range(5):
                d.collect_repo_commits(wts)
            for _ in range(3):
                d.cached_local_stats(str(db))
        git_calls = [c for c, _ in calls if isinstance(c, list) and c and c[0] == "git"]
        self.assertLessEqual(
            len(git_calls), 3,
            "git must be TTL-cached, got %d calls: %r" % (len(git_calls), git_calls))

    def test_f6f_t02_timeout_or_non_repo_readable_state(self):
        d = self.d
        repo, _env = _f6f_repo(Path(self._tmp.name), "repo-two")
        wts = [(repo.name, str(repo))]
        head1 = dict(d.repo_heads(wts))[str(repo)]
        self.assertTrue(head1, "warm head expected")
        entry = d._HEADS_CACHE[str(repo)]
        entry["at"] -= 3600

        def boom(cmd, *a, **k):
            raise _subprocess.TimeoutExpired(cmd, 5)

        with _mock.patch.object(d.subprocess, "run", side_effect=boom):
            heads2 = dict(d.repo_heads(wts))
        self.assertEqual(heads2[str(repo)], head1,
                         "the last known head must survive a timeout")
        self.assertEqual(d._HEADS_CACHE[str(repo)]["state"], "stale")

        plain = Path(self._tmp.name) / "not-a-repo"
        plain.mkdir()
        with _mock.patch.object(d.subprocess, "run", side_effect=boom):
            heads3 = dict(d.repo_heads([("plain", str(plain))]))
        self.assertIsNone(heads3[str(plain)])
        self.assertEqual(d._HEADS_CACHE[str(plain)]["state"], "unavailable")

        db = Path(self._tmp.name) / "f6f2.db"
        _f6f_make_db(db, repo)
        with _mock.patch.object(d.subprocess, "run", side_effect=boom):
            stats = d.cached_local_stats(str(db))
        self.assertIsInstance(stats, dict)
        self.assertIn("totals", stats)

    def test_f6f_t03_head_change_visible_after_ttl(self):
        d = self.d
        repo, env = _f6f_repo(Path(self._tmp.name), "repo-three")
        wts = [(repo.name, str(repo))]
        head1 = dict(d.repo_heads(wts))[str(repo)]
        self.assertTrue(head1)
        (repo / "b.txt").write_text("two", encoding="utf-8")
        _f6f_git(["add", "b.txt"], cwd=repo, env=env)
        _f6f_git(["commit", "-q", "-m", "two"], cwd=repo, env=env)
        cached = dict(d.repo_heads(wts))[str(repo)]
        self.assertEqual(cached, head1,
                         "inside the TTL the cached head is served")
        d._HEADS_CACHE[str(repo)]["at"] -= 3600
        head2 = dict(d.repo_heads(wts))[str(repo)]
        self.assertNotEqual(head2, head1,
                            "after the TTL the new HEAD must be visible")

    def test_f6f_t04_special_paths_argv_no_shell(self):
        d = self.d
        weird = "repo sp\u00e4ce & $HOME ! (x)"
        repo, _env = _f6f_repo(Path(self._tmp.name), weird)
        wts = [(weird, str(repo))]
        calls, counting = self._counting()
        with _mock.patch.object(d.subprocess, "run", side_effect=counting):
            heads = dict(d.repo_heads(wts))
            commits = d.collect_repo_commits(wts)
        self.assertTrue(heads[str(repo)])
        self.assertTrue(commits)
        self.assertTrue(calls)
        for cmd, kwargs in calls:
            self.assertIsInstance(cmd, list, "git argv must be a list")
            self.assertTrue(kwargs.get("shell") in (None, False),
                            "shell must never be used for git")
            self.assertIn(str(repo), cmd,
                          "the exact path must be one argv element")


if __name__ == "__main__":
    unittest.main()
