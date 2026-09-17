"""F6b RED-then-GREEN tests: bounded router tail reads, cached line counter
and honest range metadata (spec section 13).

Contract exercised here:

- ``parse_router_events`` reads at most a bounded physical tail of the usage
  file (no ``readlines`` of the whole file) and reports candidate source
  lines and valid records separately, plus oversize-line and unterminated
  trailing-line counters. Nothing is dropped silently.
- ``query_router_stats`` exposes ``total_lines`` from a cached line counter
  with fingerprint invalidation (append / truncate / rotation) and adds the
  ``window_mode`` / ``candidate_lines`` / ``pending_tail_line`` /
  ``oversize_records`` / ``count_cached`` metadata. Small files keep their
  previous numbers.
- Synthesis (``full_scan=True``) keeps the whole history: no silent cut to
  30000 and the recent-rows view stays limited.

DO NOT EDIT beyond this family: the file is per-family and self-contained.
"""

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as _mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_ISO = "2026-09-16T%02d:00:00"


def _load_dashboard():
    try:
        import opencode_dashboard as d
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("dashboard not importable: %s" % (exc,))
    return d


def _record(total, hour, model=None, extra=None):
    event = {
        "at": _ISO % hour,
        "model": model or "muse-spark",
        "provider": "openai",
        "status": 200,
        "outcome": "success",
        "inputTokens": float(total) - 20.0,
        "cachedInputTokens": 0,
        "outputTokens": 20.0,
        "totalTokens": float(total),
        "reasoningTokens": 0,
        "cacheWriteInputTokens": 0,
    }
    if extra:
        event.update(extra)
    return event


def _text(records, ending="\n"):
    return "".join(json.dumps(r) + ending for r in records)


def _reference_tail(text, limit):
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    tail = lines[-limit:] if limit else lines
    return tail


class _FileSpy:
    def __init__(self, fh, spy):
        self._fh = fh
        self._spy = spy

    def __enter__(self):
        self._fh.__enter__()
        return self

    def __exit__(self, *exc):
        return self._fh.__exit__(*exc)

    def readlines(self, *args, **kwargs):
        self._spy["readlines_calls"] += 1
        return self._fh.readlines(*args, **kwargs)

    def read(self, *args, **kwargs):
        data = self._fh.read(*args, **kwargs)
        self._spy["read_bytes"] += len(data)
        return data

    def __iter__(self):
        return iter(self._fh)

    def __getattr__(self, name):
        return getattr(self._fh, name)


class TestF6bRouterEvents(unittest.TestCase):
    def setUp(self):
        self._saved_env = {}
        for name in ("HOME", "USERPROFILE", "LOCALAPPDATA"):
            self._saved_env[name] = os.environ.get(name)
        self._tmp = tempfile.TemporaryDirectory(prefix="f6b-router-")
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self.d = _load_dashboard()
        self.addCleanup(self._restore_env)
        if hasattr(self.d, "_ROUTER_COUNT_CACHE"):
            self.d._ROUTER_COUNT_CACHE.clear()
        if hasattr(self.d, "ROUTER_CACHE"):
            self.d.ROUTER_CACHE["key"] = None
            self.d.ROUTER_CACHE["stats"] = None

    def _restore_env(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def _write(self, name, text):
        path = Path(self._tmp.name, name)
        path.write_bytes(text.encode("utf-8"))
        return path

    def _query(self, path, full_scan=False):
        return self.d.query_router_stats(str(path), None, None, full_scan)

    def test_f6b_t01_large_file_returns_bounded_tail(self):
        records = [_record(100 + i, 1 + (i % 22)) for i in range(200)]
        path = self._write("tail.jsonl", _text(records))
        events, problems = self.d.parse_router_events(str(path), 50)
        self.assertEqual(len(events), 50)
        self.assertEqual(
            [e["total"] for e in events],
            [float(100 + i) for i in range(150, 200)],
        )
        self.assertEqual(problems["candidate_lines"], 50)
        self.assertEqual(problems["valid_records"], 50)
        self.assertEqual(problems["lines_total"], 200)
        self.assertEqual(problems["window_mode"], "physical_tail")
        self.assertFalse(problems["pending_tail_line"])
        with _mock.patch.object(self.d, "MAX_ROUTER_EVENTS", 50):
            stats = self._query(path)
        self.assertTrue(stats["truncated"])
        self.assertEqual(stats["scanned"], 50)
        self.assertEqual(stats["total_lines"], 200)
        self.assertEqual(stats["window_mode"], "physical_tail")
        self.assertEqual(stats["candidate_lines"], 50)
        self.assertEqual(stats["totals"]["requests"], 50)
        self.assertEqual(
            stats["totals"]["tokens_total"],
            float(sum(100 + i for i in range(150, 200))),
        )

    def test_f6b_t02_tail_never_readlines_and_warm_read_is_cheap(self):
        records = [_record(1000 + i, 1 + (i % 22)) for i in range(4000)]
        text = _text(records)
        path = self._write("big.jsonl", text)
        size = len(text.encode("utf-8"))
        spy = {"readlines_calls": 0, "read_bytes": 0}
        real_open = open

        def opener(file, *args, **kwargs):
            fh = real_open(file, *args, **kwargs)
            if str(file) == str(path):
                return _FileSpy(fh, spy)
            return fh

        with _mock.patch("builtins.open", side_effect=opener):
            events1, p1 = self.d.parse_router_events(str(path), 10)
            first_bytes = spy["read_bytes"]
            spy["read_bytes"] = 0
            events2, p2 = self.d.parse_router_events(str(path), 10)
            second_bytes = spy["read_bytes"]

        self.assertEqual(len(events1), 10)
        self.assertEqual(len(events2), 10)
        self.assertEqual(spy["readlines_calls"], 0)
        self.assertLess(first_bytes, size)
        self.assertLess(second_bytes, size // 4)
        self.assertIs(p2["count_cached"], True)

    def test_f6b_t03_synthesis_keeps_full_history_no_30000_cut(self):
        count = 31000
        records = [_record(1 + (i % 9), 1 + (i % 22)) for i in range(count)]
        path = self._write("synth.jsonl", _text(records))
        events, problems = self.d.parse_router_events(
            str(path), None, synth_context=True)
        self.assertEqual(len(events), count)
        self.assertEqual(problems["window_mode"], "full")
        self.assertEqual(problems["lines_total"], count)
        stats = self.d.query_router_stats(str(path), None, None, True)
        self.assertFalse(stats["truncated"])
        self.assertEqual(stats["scanned"], count)
        self.assertEqual(stats["totals"]["requests"], count)
        self.assertLessEqual(len(stats["requests"]), self.d.MAX_ROUTER_ROWS)

    def test_f6b_t04_crlf_unicode_and_block_boundaries_match_reference(self):
        text = (
            json.dumps(_record(100, 8, model="muse-\u26a1")) + "\r\n"
            + "\r\n"
            + json.dumps(_record(101, 9)) + "\n"
            + json.dumps(_record(102, 10, model="muse-\u017c\u00f3\u0142\u0107")) + "\r\n"
            + "\n"
            + json.dumps(_record(103, 11)) + "\n"
        )
        path = self._write("crlf.jsonl", text)
        with _mock.patch.object(self.d, "_ROUTER_READ_BLOCK", 5):
            events, problems = self.d.parse_router_events(str(path), 4)
            self.d._ROUTER_COUNT_CACHE.clear()
            events_all, _ = self.d.parse_router_events(str(path), None)
        expected = [
            json.loads(line.rstrip("\r").strip())["totalTokens"]
            for line in _reference_tail(text, 4)
            if line.rstrip("\r").strip()
        ]
        self.assertEqual([e["total"] for e in events], expected)
        self.assertEqual(
            events[-1]["model"] if events[-1]["model"] else None, "muse-spark")
        models = {e["model"] for e in events_all}
        self.assertIn("muse-\u26a1", models)
        self.assertIn("muse-\u017c\u00f3\u0142\u0107", models)
        self.assertNotIn("\ufffd", "".join(models))
        self.assertEqual(problems["candidate_lines"], 4)
        self.assertEqual(len(events_all), 4)

    def test_f6b_t05_oversize_line_is_counted_and_next_record_survives(self):
        huge = _record(500, 9, extra={"note": "x" * 400})
        good = _record(101, 10)
        text = json.dumps(huge) + "\n" + json.dumps(good) + "\n"
        path = self._write("oversize.jsonl", text)
        with _mock.patch.object(self.d, "MAX_ROUTER_RECORD_BYTES", 600):
            events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual([e["total"] for e in events], [101.0])
        self.assertEqual(problems["oversize_records"], 1)
        self.assertEqual(problems["invalid_records"], 0)
        self.assertEqual(problems["candidate_lines"], 2)

    def test_f6b_t06_unterminated_tail_is_pending_then_read_once(self):
        full = json.dumps(_record(120, 12))
        path = self._write("pending.jsonl", full[:20])
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual(events, [])
        self.assertIs(problems["pending_tail_line"], True)
        self.assertEqual(problems["invalid_records"], 0)
        with open(str(path), "ab") as fh:
            fh.write((full[20:] + "\n").encode("utf-8"))
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual([e["total"] for e in events], [120.0])
        self.assertIs(problems["pending_tail_line"], False)
        self.assertEqual(problems["lines_total"], 1)
        events_again, _ = self.d.parse_router_events(str(path), None)
        self.assertEqual(len(events_again), 1)

    def test_f6b_t07_append_truncate_rotation_invalidate_counts(self):
        path = self._write("live.jsonl", _text(
            [_record(100, 8), _record(101, 9), _record(102, 10)]))
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual(problems["lines_total"], 3)
        with open(str(path), "ab") as fh:
            fh.write(_text([_record(103, 11), _record(104, 12)]).encode("utf-8"))
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual(len(events), 5)
        self.assertEqual(problems["lines_total"], 5)
        self._write("live.jsonl", _text([_record(200, 13)]))
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual([e["total"] for e in events], [200.0])
        self.assertEqual(problems["lines_total"], 1)
        rotated = Path(self._tmp.name, "rotated.jsonl")
        rotated.write_bytes(_text([_record(300, 14), _record(301, 15)]).encode("utf-8"))
        os.replace(str(rotated), str(path))
        events, problems = self.d.parse_router_events(str(path), None)
        self.assertEqual(problems["lines_total"], 2)
        stats = self._query(path)
        self.assertEqual(stats["total_lines"], 2)

    def test_f6b_t08_non_chronological_tail_scope_and_recent_sort(self):
        records = [
            _record(110, 10),
            _record(120, 8),
            _record(130, 12),
            _record(140, 9),
        ]
        path = self._write("mixed.jsonl", _text(records))
        events, problems = self.d.parse_router_events(str(path), 2)
        self.assertEqual(problems["window_mode"], "physical_tail")
        self.assertEqual([e["total"] for e in events], [130.0, 140.0])
        with _mock.patch.object(self.d, "MAX_ROUTER_EVENTS", 2):
            stats = self._query(path)
        self.assertEqual(stats["totals"]["tokens_total"], 270.0)
        self.assertEqual(stats["requests"][0]["hour"], 12)
        self.assertEqual(stats["requests"][1]["hour"], 9)
        stats_all = self.d.query_router_stats(str(path), None, None, True)
        self.assertEqual(
            [r["hour"] for r in stats_all["requests"][:4]],
            [12, 10, 9, 8],
        )


if __name__ == "__main__":
    unittest.main()
