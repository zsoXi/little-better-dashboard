"""A07 regression: the per-model average token field must divide by events
with a real measurement (F4 contract), not by successful outcomes.

An unknown-outcome request with a real usage reading is still a measurement;
dividing by successes alone produced absurd averages (0 for an unknown-only
model, doubled values for mixed success/unknown)."""

import json
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
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("opencode_dashboard import failed: %s" % (exc,))
    return d


def _record(total, hour, outcome="success"):
    return {"at": "2026-09-16T%02d:00:00" % hour, "model": "muse-spark",
            "provider": "openai",
            "status": 200 if outcome == "success" else None,
            "outcome": outcome,
            "inputTokens": float(total) - 20.0, "cachedInputTokens": 0,
            "outputTokens": 20.0, "totalTokens": float(total),
            "reasoningTokens": 0, "cacheWriteInputTokens": 0}


def _text(records):
    return "".join(json.dumps(record) + "\n" for record in records)


class A07ModelAverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lbd-a07-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.d = _load_dashboard()

    def _model_row(self, records):
        path = self.root / "usage.jsonl"
        path.write_bytes(_text(records).encode("utf-8"))
        stats = self.d.query_router_stats(str(path), 50, None, False)
        rows = stats["models"]
        self.assertEqual(len(rows), 1)
        return stats, rows[0]

    def test_a07_unknown_only_model_average_uses_measured_count(self):
        stats, row = self._model_row(
            [_record(120, 1, outcome="unknown")])
        # Old code divided by ok=0 and returned 0.
        self.assertEqual(row[8], 120.0)
        self.assertEqual(row[6], 0)          # no successes
        self.assertEqual(stats["totals"]["tokens_total"], 120.0)

    def test_a07_mixed_success_and_unknown_average(self):
        stats, row = self._model_row(
            [_record(120, 1), _record(30, 2, outcome="unknown")])
        # Old code: (120 + 30) / 1 success = 150.
        self.assertEqual(row[8], 75.0)
        self.assertEqual(row[6], 1)          # one success
        self.assertEqual(stats["totals"]["tokens_total"], 150.0)
        self.assertEqual(stats["totals"]["requests"], 2)


if __name__ == "__main__":
    unittest.main()
