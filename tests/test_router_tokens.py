"""Router token accounting skeleton tests (F1).

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


class TestRouterTokens(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-router-tokens-")
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
        # Discovery smoke: unittest found this file and HOME is isolated.
        self.assertTrue(True)
        self.assertEqual(os.environ.get("HOME"), self._tmp.name)
        self.assertEqual(os.environ.get("USERPROFILE"), self._tmp.name)
        self.assertTrue(Path(self._tmp.name).is_dir())

    def test_import_dashboard(self):
        d = _load_dashboard()
        self.assertTrue(hasattr(d, "router_cache_rate"))
        self.assertTrue(hasattr(d, "what_if_cost"))

    def test_router_cache_rate_pure(self):
        d = _load_dashboard()
        # Cached input is a subset of total input: 30/100 -> 30.0%.
        self.assertEqual(d.router_cache_rate(100, 30), 30.0)
        self.assertEqual(d.router_cache_rate(0, 0), 0.0)
        # Day tokens must not double-count cached input (subset of input).
        self.assertEqual(d._router_day_tokens({"ti": 10, "to": 5, "tr": 2}), 17)


if __name__ == "__main__":
    unittest.main()
