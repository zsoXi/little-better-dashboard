"""Codex publication tests (F5b) + original skeleton smoke checks.

F5b (spec INSTRUKCJA ch.10): diagnostics, the session list and the synth
reader must share one Codex rollout enumeration - same root, same
"rollout-*.jsonl" pattern - and distinguish missing dir / empty dir /
files present / partial or unreadable reads. Baseline diagnostics used a
top-level os.listdir(".jsonl") while readers recurse, so nested rollouts
were invisible to diagnostics and foreign top-level .jsonl files inflated
its health count. All fixtures synthetic in temp dirs; isolated HOME;
no fixed ports; no real user data read.
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


class TestCodexPublication(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-codex-pub-")
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
        self.assertTrue(hasattr(d, "blank_stats"))
        self.assertTrue(hasattr(d, "json_html"))

    def test_blank_stats_shape_and_safe_embed(self):
        d = _load_dashboard()
        stats = d.blank_stats()
        self.assertIn("totals", stats)
        self.assertIn("activity", stats)
        self.assertEqual(stats["totals"]["sessions"], 0)
        # Published payload must embed safely inside <script> tags.
        embedded = d.json_html({"title": "</script>"})
        self.assertNotIn("</script>", embedded)


# ---------------------------------------------------------------------------
# F5b (spec INSTRUKCJA ch.10): one shared Codex rollout enumeration for
# diagnostics, the session list and the synth reader. Baseline flaw:
# query_signals listed only the top level with os.listdir + ".jsonl", while
# both readers match nested "rollout-*.jsonl" recursively - nested rollouts
# were invisible to diagnostics while foreign top-level .jsonl files
# inflated its health count.
# ---------------------------------------------------------------------------
import builtins as _builtins
import json as _json
import sqlite3 as _sqlite3
import unittest.mock as _mock
from unittest.mock import patch as _patch


def _f5b_lines(n=3, ts="2026-09-16T12:00:00", model="muse-spark"):
    lines = [
        {"type": "session_meta", "timestamp": ts,
         "payload": {"model_provider": "codex"}},
        {"type": "turn_context", "timestamp": ts, "payload": {"model": model}},
        {"type": "event_msg", "timestamp": ts,
         "payload": {"item": {"content": [{"text": "probe"}]}}},
    ]
    for _ in range(n):
        lines.append(
            {"type": "token_usage_record", "timestamp": ts,
             "payload": {"usage": {"input_tokens": 100,
                                   "cached_input_tokens": 40,
                                   "output_tokens": 20,
                                   "total_tokens": 120,
                                   "reasoning_output_tokens": 5,
                                   "cache_write_input_tokens": 0}}})
    return "\n".join(_json.dumps(r) for r in lines) + "\n"


def _f5b_read_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().splitlines()


class TestF5bEnumeration(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k)
                         for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-codex-enum-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        d = self.d
        self._saved = {}
        for k in ("CODEX_DIR", "CODEX_SYNTH", "_codex_cache", "_codex_sig",
                  "_codex_ev_cache", "_codex_ev_sig", "_codex_last_error",
                  "_codex_last_ok"):
            self._saved[k] = getattr(d, k, "--missing--")
        self.addCleanup(self._restore)
        self.sessions = Path(self._tmp.name) / "codex" / "sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        d.CODEX_DIR = self.sessions
        d.CODEX_SYNTH = Path(self._tmp.name) / "synth.jsonl"
        d._codex_cache = {}
        d._codex_sig = None
        d._codex_ev_cache = {}
        d._codex_ev_sig = None
        if hasattr(d, "_codex_last_error"):
            d._codex_last_error = None
        if hasattr(d, "_codex_last_ok"):
            d._codex_last_ok = None

    def _restore(self):
        d = self.d
        for k, v in self._saved.items():
            if v == "--missing--":
                try:
                    delattr(d, k)
                except AttributeError:
                    pass
            else:
                setattr(d, k, v)

    def _write(self, rel, n=3, model="muse-spark"):
        p = self.sessions / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_f5b_lines(n, model=model), encoding="utf-8")
        return p

    def _signals(self):
        con = _sqlite3.connect(":memory:")
        try:
            con.row_factory = _sqlite3.Row
            return self.d.query_signals(con)["signals"]
        finally:
            con.close()

    def _codex_signals(self):
        return [s for s in self._signals() if "Codex" in s["text"]]

    def test_f5b_t01_nested_rollout_seen_by_diagnostics_and_readers(self):
        self._write("2026/09/16/rollout-fixture.jsonl")
        sigs = self._codex_signals()
        self.assertEqual(len(sigs), 1, sigs)
        self.assertIn("1 files", sigs[0]["text"])
        self.assertEqual(sigs[0]["level"], "ok")
        self.assertNotIn("empty", sigs[0]["text"])
        res = self.d.query_codex(force=True)
        self.assertEqual(res["files"], 1)
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["sessions"][0]["name"], "rollout-fixture")
        path, err = self.d.ensure_codex_synth(force=True)
        self.assertIsNone(err)
        self.assertEqual(len(_f5b_read_lines(self.d.CODEX_SYNTH)), 3)

    def test_f5b_t02_empty_dir_vs_missing_dir_distinct(self):
        sigs = self._codex_signals()
        self.assertTrue(any("empty" in s["text"] for s in sigs), sigs)
        self.d.CODEX_DIR = Path(self._tmp.name) / "codex" / "no-such-dir"
        sigs = self._codex_signals()
        self.assertTrue(any("missing" in s["text"] for s in sigs), sigs)
        self.assertFalse(any("empty" in s["text"] for s in sigs), sigs)

    def test_f5b_t03_foreign_jsonl_does_not_inflate_counts(self):
        self._write("2026/09/16/rollout-a.jsonl")
        self._write("2026/09/17/rollout-b.jsonl")
        (self.sessions / "foreign.jsonl").write_text("{}\n", encoding="utf-8")
        sigs = self._codex_signals()
        self.assertEqual(len(sigs), 1, sigs)
        self.assertIn("2 files", sigs[0]["text"])
        res = self.d.query_codex(force=True)
        self.assertEqual(res["files"], 2)
        self.assertEqual(res["total"], 2)
        path, err = self.d.ensure_codex_synth(force=True)
        self.assertIsNone(err)
        self.assertEqual(len(_f5b_read_lines(self.d.CODEX_SYNTH)), 6)

    def test_f5b_t04_injected_failures_are_explicit_not_clean(self):
        self._write("2026/09/16/rollout-fixture.jsonl")
        with _patch.object(Path, "rglob",
                           side_effect=PermissionError("injected denied")):
            sigs = self._codex_signals()
            self.assertTrue(any("unreadable" in s["text"] for s in sigs), sigs)
            res = self.d.query_codex(force=True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["files"], 0)
        target = str(self.sessions / "2026" / "09" / "16"
                     / "rollout-fixture.jsonl")
        real_open = _builtins.open

        def _deny_open(file, *a, **kw):
            if str(file) == target:
                raise PermissionError("injected open denied")
            return real_open(file, *a, **kw)

        with _patch("builtins.open", new=_deny_open):
            res = self.d.query_codex(force=True)
        self.assertEqual(res["files"], 1)
        self.assertTrue(res.get("partial"), res)

    def test_f5b_t05_added_removed_files_refresh_consumers(self):
        self._write("2026/09/16/rollout-a.jsonl", n=3)
        r1 = self.d.query_codex()
        self.assertEqual((r1["files"], r1["total"]), (1, 1))
        self.assertIn("1 files", self._codex_signals()[0]["text"])
        self._write("2026/09/17/rollout-b.jsonl", n=5)
        r2 = self.d.query_codex()
        self.assertEqual((r2["files"], r2["total"]), (2, 2))
        self.assertIn("2 files", self._codex_signals()[0]["text"])
        (self.sessions / "2026" / "09" / "17" / "rollout-b.jsonl").unlink()
        r3 = self.d.query_codex()
        self.assertEqual((r3["files"], r3["total"]), (1, 1))
        self.assertIn("1 files", self._codex_signals()[0]["text"])


if __name__ == "__main__":
    unittest.main()
