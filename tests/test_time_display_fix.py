"""Time-display and rendering regression tests.

Static guards for the embedded frontend (unary-plus concat hazard, the
isoLocal local-time rendering sites) plus a behavioural check for the
idle-clock seed. No server is started and no real user data is read.
"""
import re
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD = REPO_ROOT / "opencode_dashboard.py"


def _load_dashboard():
    import opencode_dashboard as d
    return d


def _source():
    return DASHBOARD.read_text(encoding="utf-8")


class TestUnaryPlusGuard(unittest.TestCase):
    def test_no_unary_plus_concat_in_source(self):
        # A line ending with '+' followed by a line starting with '+(' turns
        # the parenthesised expression into a unary plus (Number('…') = NaN).
        hits = re.findall(r"(?m)^\s*\+\s*\(", _source())
        self.assertEqual(hits, [], "unary-plus concat hazard reintroduced")


class TestLocalTimeRendering(unittest.TestCase):
    def test_isolocal_helper_defined(self):
        src = _source()
        self.assertIn("const isoLocal=t=>{", src)
        self.assertIn("d.getFullYear()", src)
        self.assertIn("d.getMonth()", src)
        self.assertIn("d.getHours()", src)

    def test_display_sites_use_isolocal(self):
        src = _source()
        self.assertIn("ESC(isoLocal(r.at))", src)
        self.assertIn("isoLocal(r.biggest_request.at)", src)
        self.assertIn("ESC(isoLocal(r.last))", src)

    def test_raw_utc_slices_removed(self):
        src = _source()
        self.assertNotIn("(r.at||'').replace('T',' ').slice(0,19)", src)
        self.assertNotIn("String(r.biggest_request.at||'').replace('T',' ').slice(0,19)", src)
        self.assertNotIn("(r.last||'').slice(0,19).replace('T',' ')", src)


class TestIdleClockSeed(unittest.TestCase):
    def test_prime_idle_clock_sets_recent_timestamp(self):
        d = _load_dashboard()
        old = d.LAST_REQUEST
        self.addCleanup(setattr, d, "LAST_REQUEST", old)
        d.LAST_REQUEST = 0.0
        d._prime_idle_clock()
        self.assertGreater(d.LAST_REQUEST, time.time() - 5)

    def test_main_seeds_idle_clock(self):
        src = _source()
        self.assertIn("_prime_idle_clock()", src)
        main_slice = src[src.index("def main():"):]
        self.assertIn("_prime_idle_clock()", main_slice)


if __name__ == "__main__":
    unittest.main()
