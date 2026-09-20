"""Time-range preset tests: Last 24h, Today, Yesterday.

Static guards over the embedded frontend: both period bars carry the new
buttons, the range helpers implement the new values, and the old raw
millisecond arithmetic is gone. No server is started and no real user
data is read.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD = REPO_ROOT / "opencode_dashboard.py"


def _source():
    return DASHBOARD.read_text(encoding="utf-8")


class TestPresetButtons(unittest.TestCase):
    def test_opencode_bar_has_new_presets(self):
        src = _source()
        self.assertEqual(src.count('data-r="24h"'), 1)
        self.assertEqual(src.count('data-r="today"'), 1)
        self.assertEqual(src.count('data-r="yesterday"'), 1)
        self.assertIn('data-r="7"', src)

    def test_router_bar_has_new_presets(self):
        src = _source()
        self.assertEqual(src.count('data-rr="24h"'), 1)
        self.assertEqual(src.count('data-rr="today"'), 1)
        self.assertEqual(src.count('data-rr="yesterday"'), 1)
        self.assertIn('data-rr="7"', src)


class TestRangeHelpers(unittest.TestCase):
    HELPERS = (
        "rangeCutMs", "rangeEndMs", "inRangeMs", "inRangeDay", "rangeLabel",
        "rRangeCutMs", "rRangeEndMs", "rInRangeMs", "rInRangeDay",
    )

    def test_helpers_defined(self):
        src = _source()
        for name in self.HELPERS:
            self.assertIn(f"function {name}(", src, f"missing {name}")

    def test_old_arithmetic_removed(self):
        src = _source()
        self.assertNotIn("RANGE*86400000", src)
        self.assertNotIn("RRANGE*86400000", src)

    def test_range_filters_use_helpers(self):
        src = _source()
        self.assertEqual(src.count("inRangeMs(s.time_created)"), 2)
        self.assertIn("inRangeDay(c[2])", src)
        self.assertIn("rInRangeDay(c[2])", src)
        self.assertIn("rInRangeMs(Date.parse(r.at||'", src)

    def test_labels_use_range_label(self):
        src = _source()
        self.assertIn("rangeLabel(RANGE)", src)
        self.assertIn("rangeLabel(RRANGE)", src)


if __name__ == "__main__":
    unittest.main()
