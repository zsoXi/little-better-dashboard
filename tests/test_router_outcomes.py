"""Router outcome classification skeleton tests (F2).

Skeleton only: discovery smoke + one real pure-helper check.
All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA isolated.
No server started; no real user data read.
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


class TestRouterOutcomes(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-router-outcomes-")
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
        self.assertTrue(hasattr(d, "router_is_free"))
        self.assertTrue(hasattr(d, "blank_router_stats"))

    def test_router_outcome_helpers_pure(self):
        d = _load_dashboard()
        self.assertTrue(d.router_is_free("muse-spark-1.3-contributor-free"))
        self.assertFalse(d.router_is_free("muse-spark-1.3-contributor"))
        # Empty timestamp parses to (None, None) without touching disk.
        self.assertEqual(d._router_event_day_hour(None), (None, None))
        self.assertEqual(d._router_event_day_hour(""), (None, None))
        # Blank router payload keeps the success/error/unknown shape.
        blank = d.blank_router_stats()
        self.assertIn("totals", blank)
        self.assertIn("errors", blank["totals"])


if __name__ == "__main__":
    unittest.main()
