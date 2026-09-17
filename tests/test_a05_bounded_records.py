"""A05 regression: declared record caps must bound reader memory.

The declared per-record cap must bound the reader's buffers, not just the
final oversize counter: a single line far above the cap (and growing) must
be skipped in block-sized discard steps. Measured with tracemalloc: the
peak stays far below the line size and must not scale with it. Correctness
around the skip is asserted too (neighbouring records survive, counters and
window metadata stay honest).
"""

import json
import sys
import tempfile
import tracemalloc
import unittest
import unittest.mock as _mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_ISO = "2026-09-16T%02d:00:00"
CAP = 1024
PEAK_LIMIT = 1 << 20


def _load_dashboard():
    try:
        import opencode_dashboard as d
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("opencode_dashboard import failed: %s" % (exc,))
    return d


def _record(total, hour, extra=None):
    rec = {
        "at": _ISO % hour,
        "model": "muse-spark",
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
        rec.update(extra)
    return rec


def _measure(func):
    tracemalloc.start()
    try:
        result = func()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


class A05BoundedRecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a05-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.d = _load_dashboard()

    def _write(self, name, text):
        path = self.root / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def _text_with_huge_line(self, line_bytes):
        filler = "y" * (line_bytes - 256)
        huge = json.dumps(_record(777, 9, extra={"note": filler}))
        return (json.dumps(_record(101, 8)) + "\n"
                + huge + "\n"
                + json.dumps(_record(102, 10)) + "\n")

    def test_a05_tail_scan_peak_is_bounded_and_does_not_scale(self):
        peaks = []
        for name, line_bytes in (("tail-4mb.jsonl", 4 * 1024 * 1024),
                                 ("tail-16mb.jsonl", 16 * 1024 * 1024)):
            path = self._write(name, self._text_with_huge_line(line_bytes))
            with _mock.patch.object(self.d, "MAX_ROUTER_RECORD_BYTES", CAP):
                (events, problems), peak = _measure(
                    lambda p=path: self.d.parse_router_events(str(p), 5))
            self.assertEqual([e["total"] for e in events], [101.0, 102.0])
            self.assertEqual(problems["oversize_records"], 1)
            self.assertEqual(problems["candidate_lines"], 3)
            self.assertLess(peak, PEAK_LIMIT,
                            "tail reader peak %d for %d-byte line"
                            % (peak, line_bytes))
            peaks.append(peak)
        self.assertLess(max(peaks), PEAK_LIMIT)

    def test_a05_stream_scan_peak_is_bounded_after_discard(self):
        path = self._write("stream-16mb.jsonl",
                           self._text_with_huge_line(16 * 1024 * 1024))
        # The line ends with content AFTER the huge line, so the reader must
        # both discard the oversize remainder and resume normal records.
        with _mock.patch.object(self.d, "MAX_ROUTER_RECORD_BYTES", CAP):
            (events, problems), peak = _measure(
                lambda: self.d.parse_router_events(str(path), None))
        self.assertEqual([e["total"] for e in events], [101.0, 102.0])
        self.assertEqual(problems["oversize_records"], 1)
        self.assertEqual(problems["invalid_records"], 0)
        self.assertLess(peak, PEAK_LIMIT)

    def test_a05_codex_scan_skips_oversize_line_with_bounded_peak(self):
        d = self.d
        codex_dir = self.root / "codex"
        codex_dir.mkdir()
        roll = codex_dir / "rollout-2026-01-01T00-00-00-a05.jsonl"
        meta_line = json.dumps({"type": "session_meta",
                                "payload": {"model_provider": "codex"}})
        usage_line = json.dumps({
            "type": "token_usage_record",
            "payload": {"usage": {
                "input_tokens": 10, "cached_input_tokens": 0,
                "output_tokens": 5, "total_tokens": 15,
                "reasoning_output_tokens": 0,
                "cache_write_input_tokens": 0}}})
        huge = json.dumps({"type": "token_usage_record",
                           "note": "z" * (16 * 1024 * 1024)})
        roll.write_bytes((meta_line + "\n" + huge + "\n" + usage_line + "\n")
                         .encode("utf-8"))
        d._codex_oversize_records = 0
        with _mock.patch.object(d, "MAX_ROUTER_RECORD_BYTES", CAP):
            (lines, used, fp, rebuild, tail), peak = _measure(
                lambda: d._codex_full_read(
                    roll, (roll.stat().st_size, 0, 0)))
        self.assertEqual(d._codex_oversize_records, 1)
        self.assertEqual(lines, [meta_line, usage_line])
        self.assertLess(peak, PEAK_LIMIT)


    def test_a05_oversize_skip_is_reported_in_source_metadata(self):
        d = self.d
        codex_dir = self.root / "codex"
        codex_dir.mkdir()
        roll = codex_dir / "rollout-2026-01-01T00-00-00-a05b.jsonl"
        lines = [
            json.dumps({"type": "session_meta",
                        "payload": {"model_provider": "codex"}}),
            json.dumps({"type": "turn_context",
                        "payload": {"model": "muse-spark"}}),
            json.dumps({"type": "token_usage_record", "payload": {"usage": {
                "input_tokens": 10, "cached_input_tokens": 0,
                "output_tokens": 0, "total_tokens": 10,
                "reasoning_output_tokens": 0,
                "cache_write_input_tokens": 0}}}),
            json.dumps({"type": "token_usage_record",
                        "note": "x" * 2000, "payload": {"usage": {
                            "input_tokens": 900, "cached_input_tokens": 0,
                            "output_tokens": 0, "total_tokens": 900,
                            "reasoning_output_tokens": 0,
                            "cache_write_input_tokens": 0}}}),
            json.dumps({"type": "token_usage_record", "payload": {"usage": {
                "input_tokens": 20, "cached_input_tokens": 0,
                "output_tokens": 0, "total_tokens": 20,
                "reasoning_output_tokens": 0,
                "cache_write_input_tokens": 0}}}),
        ]
        roll.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        dir_backup = d.CODEX_DIR
        synth_backup = d.CODEX_SYNTH
        index_backup = d.CODEX_INDEX
        try:
            d.CODEX_DIR = codex_dir
            d.CODEX_SYNTH = self.root / "synth.jsonl"
            d.CODEX_INDEX = self.root / "codex_index.json"
            d._codex_ev_cache = {}
            d._codex_ev_sig = None
            if hasattr(d, "_codex_ev_state"):
                d._codex_ev_state.clear()
            if hasattr(d, "_codex_read_failures"):
                d._codex_read_failures.clear()
            with _mock.patch.object(d, "MAX_ROUTER_RECORD_BYTES", CAP):
                path, err = d.ensure_codex_synth(force=True)
            self.assertIsNone(err)
            self.assertEqual(getattr(d, "_codex_last_oversize", None), 1)
            published = []
            for line in Path(str(path)).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    published.append(json.loads(line)["totalTokens"])
            self.assertEqual(published, [10.0, 20.0])
            index = json.loads(d.CODEX_INDEX.read_text(encoding="utf-8"))
            self.assertEqual(index.get("oversize"), 1)
            # A restart adopts the count together with the checkpoints.
            d._codex_ev_cache = {}
            d._codex_ev_sig = None
            d._codex_ev_state.clear()
            d._codex_last_oversize = 0
            d._load_codex_index([roll])
            self.assertEqual(d._codex_last_oversize, 1)
        finally:
            d.CODEX_DIR = dir_backup
            d.CODEX_SYNTH = synth_backup
            d.CODEX_INDEX = index_backup


    def test_a05_oversize_skip_is_reported_in_source_metadata(self):
        d = self.d
        codex_dir = self.root / "codex"
        codex_dir.mkdir()
        roll = codex_dir / "rollout-2026-01-01T00-00-00-a05b.jsonl"
        lines = [
            json.dumps({"type": "session_meta",
                        "payload": {"model_provider": "codex"}}),
            json.dumps({"type": "turn_context",
                        "payload": {"model": "muse-spark"}}),
            json.dumps({"type": "token_usage_record", "payload": {"usage": {
                "input_tokens": 10, "cached_input_tokens": 0,
                "output_tokens": 0, "total_tokens": 10,
                "reasoning_output_tokens": 0,
                "cache_write_input_tokens": 0}}}),
            json.dumps({"type": "token_usage_record",
                        "note": "x" * 2000, "payload": {"usage": {
                            "input_tokens": 900, "cached_input_tokens": 0,
                            "output_tokens": 0, "total_tokens": 900,
                            "reasoning_output_tokens": 0,
                            "cache_write_input_tokens": 0}}}),
            json.dumps({"type": "token_usage_record", "payload": {"usage": {
                "input_tokens": 20, "cached_input_tokens": 0,
                "output_tokens": 0, "total_tokens": 20,
                "reasoning_output_tokens": 0,
                "cache_write_input_tokens": 0}}}),
        ]
        roll.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        backup = (getattr(d, "CODEX_DIR", None), d.CODEX_SYNTH, d.CODEX_INDEX)
        try:
            d.CODEX_DIR = codex_dir
            d.CODEX_SYNTH = self.root / "synth.jsonl"
            d.CODEX_INDEX = self.root / "codex_index.json"
            d._codex_ev_cache = {}
            d._codex_ev_sig = None
            if hasattr(d, "_codex_ev_state"):
                d._codex_ev_state.clear()
            if hasattr(d, "_codex_read_failures"):
                d._codex_read_failures.clear()
            with _mock.patch.object(d, "MAX_ROUTER_RECORD_BYTES", CAP):
                path, err = d.ensure_codex_synth(force=True)
            self.assertIsNone(err)
            # The skipped raw record is reported in the source metadata, not
            # silently absorbed into a "complete" index.
            self.assertEqual(getattr(d, "_codex_last_oversize", None), 1)
            published = [json.loads(l)["totalTokens"]
                         for l in Path(str(path)).read_text(
                             encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(published, [10.0, 20.0])
            index = json.loads(d.CODEX_INDEX.read_text(encoding="utf-8"))
            self.assertEqual(index.get("oversize"), 1)
            # A restart adopts the count together with the checkpoints.
            d._codex_ev_cache = {}
            d._codex_ev_sig = None
            d._codex_ev_state.clear()
            d._codex_last_oversize = 0
            d._load_codex_index([roll])
            self.assertEqual(d._codex_last_oversize, 1)
        finally:
            d.CODEX_DIR, d.CODEX_SYNTH, d.CODEX_INDEX = backup


if __name__ == "__main__":
    unittest.main()
