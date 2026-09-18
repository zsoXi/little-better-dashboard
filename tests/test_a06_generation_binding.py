"""A06 regression: line-count metadata and scanned content must describe the
same file generation.

The reader used to count lines and then open the file a second time to scan
it; an atomic replacement between those steps mixed generations (A's line
total with B's records). With one bound handle both stages describe the same
generation: whatever the swap timing, the payload stays self-consistent.
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


def _load_dashboard():
    try:
        import opencode_dashboard as d
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("opencode_dashboard import failed: %s" % (exc,))
    return d


def _record(total, hour):
    return {"at": "2026-09-16T%02d:00:00" % hour, "model": "muse-spark",
            "provider": "openai", "status": 200, "outcome": "success",
            "inputTokens": float(total) - 20.0, "cachedInputTokens": 0,
            "outputTokens": 20.0, "totalTokens": float(total),
            "reasoningTokens": 0, "cacheWriteInputTokens": 0}


def _text(records):
    return "".join(json.dumps(record) + "\n" for record in records)


class A06GenerationBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a06-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.d = _load_dashboard()
        self.live = self.root / "usage.jsonl"
        self.next_gen = self.root / "usage-next.jsonl"

    def _swap_after_count(self):
        d = self.d
        real_count = d._router_count_lines
        state = {"swapped": False}

        def wrapper(path, fh=None):
            if fh is None:
                result = real_count(path)
            else:
                result = real_count(path, fh)
            if not state["swapped"]:
                state["swapped"] = True
                try:
                    os.replace(str(self.next_gen), str(path))
                except PermissionError:
                    # Windows: the bound read handle blocks the replacement,
                    # so the race cannot happen at all here.
                    state["blocked"] = True
            return result

        return _mock.patch.object(d, "_router_count_lines",
                                  side_effect=wrapper), state

    def test_a06_swap_between_metadata_and_scan_stays_consistent(self):
        d = self.d
        self.live.write_bytes(_text([_record(120, 1)]).encode("utf-8"))
        self.next_gen.write_bytes(
            _text([_record(120, 1), _record(30, 2)]).encode("utf-8"))
        patch, state = self._swap_after_count()
        with patch:
            events, problems = d.parse_router_events(str(self.live), 50)
        self.assertTrue(state["swapped"], "the generation swap was triggered")
        # Metadata and scanned content must never mix generations: the old
        # bug reported A's line total with B's records.
        self.assertEqual(problems["lines_total"], problems["candidate_lines"])
        self.assertEqual(problems["lines_total"], len(events))
        self.assertEqual(problems["window_mode"], "full")
        self.assertEqual([e["total"] for e in events], [120.0])

    def test_a06_full_scan_swap_between_metadata_and_scan_stays_consistent(self):
        d = self.d
        self.live.write_bytes(_text([_record(120, 1)]).encode("utf-8"))
        self.next_gen.write_bytes(
            _text([_record(120, 1), _record(30, 2)]).encode("utf-8"))
        patch, state = self._swap_after_count()
        with patch:
            events, problems = d.parse_router_events(str(self.live), None)
        self.assertEqual(problems["lines_total"], len(events))
        self.assertEqual(problems["window_mode"], "full")
        self.assertEqual([e["total"] for e in events], [120.0])

    def test_a06_plain_read_keeps_full_window_semantics(self):
        d = self.d
        self.live.write_bytes(
            _text([_record(120, 1), _record(30, 2)]).encode("utf-8"))
        events, problems = d.parse_router_events(str(self.live), 50)
        self.assertEqual(problems["lines_total"], 2)
        self.assertEqual(problems["candidate_lines"], 2)
        self.assertEqual(problems["window_mode"], "full")
        self.assertEqual([e["total"] for e in events], [120.0, 30.0])


    def test_a06_full_scan_swap_between_metadata_and_scan_stays_consistent(self):
        d = self.d
        self.live.write_bytes(_text([_record(120, 1)]).encode("utf-8"))
        self.next_gen.write_bytes(
            _text([_record(120, 1), _record(30, 2)]).encode("utf-8"))
        patch, state = self._swap_after_count()
        with patch:
            events, problems = d.parse_router_events(str(self.live), None)
        # Metadata and scanned content must never mix generations: the full
        # scan used to count A's lines and stream B's records.
        self.assertEqual(problems["lines_total"], len(events))
        self.assertEqual([e["total"] for e in events], [120.0])


if __name__ == "__main__":
    unittest.main()
