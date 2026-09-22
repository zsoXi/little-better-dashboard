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


class TestPeriodModels(unittest.TestCase):
    def test_model_row_helpers_defined(self):
        src = _source()
        self.assertIn("function periodModelRows(", src)
        self.assertIn("function rPeriodModelRows(", src)

    def test_backend_ships_model_days(self):
        src = _source()
        self.assertEqual(src.count('"model_days": model_day_rows,'), 2)
        self.assertEqual(src.count('"model_days": [],'), 2)

    def test_model_sections_scope_to_period(self):
        src = _source()
        self.assertIn("const mrows=periodModelRows();", src)
        self.assertIn("const rows=periodModelRows();", src)
        self.assertIn("const mrows=rPeriodModelRows();", src)
        self.assertIn("const nModels=rPeriodModelRows().length;", src)

    def test_range_choice_is_remembered(self):
        src = _source()
        self.assertIn("sessionStorage.setItem('ocd-range',RANGE)", src)
        self.assertIn("sessionStorage.setItem('ocd-rrange',RRANGE)", src)
        self.assertIn("sessionStorage.getItem('ocd-range')", src)
        self.assertIn("sessionStorage.getItem('ocd-rrange')", src)


class TestCustomDates(unittest.TestCase):
    def test_bars_have_custom_inputs(self):
        src = _source()
        for token in ('id="c-from"', 'id="c-to"', 'id="c-apply"',
                      'id="r-c-from"', 'id="r-c-to"', 'id="r-c-apply"'):
            self.assertIn(token, src, f"missing {token}")

    def test_custom_helpers_defined(self):
        src = _source()
        for name in ("cFrom", "cTo", "rCFrom", "rCTo"):
            self.assertIn(f"function {name}(", src, f"missing {name}")

    def test_custom_branches_in_range_helpers(self):
        src = _source()
        self.assertGreaterEqual(src.count("RANGE==='custom'"), 5)
        self.assertGreaterEqual(src.count("RRANGE==='custom'"), 5)
        self.assertIn("if(RANGE==='custom')return cFrom();", src)
        self.assertIn("if(RRANGE==='custom')return rCFrom();", src)

    def test_custom_choice_is_remembered(self):
        src = _source()
        self.assertIn("sessionStorage.setItem('ocd-custom'", src)
        self.assertIn("sessionStorage.getItem('ocd-custom')", src)
        self.assertIn("sessionStorage.setItem('ocd-rcustom'", src)
        self.assertIn("sessionStorage.getItem('ocd-rcustom')", src)

    def test_apply_handlers_and_labels(self):
        src = _source()
        self.assertIn("$('c-apply').onclick", src)
        self.assertIn("$('r-c-apply').onclick", src)
        self.assertIn("('CUSTOM '+cFrom()+' → '+cTo())", src)
        self.assertIn("('CUSTOM '+rCFrom()+' → '+rCTo())", src)


if __name__ == "__main__":
    unittest.main()
