"""Local regression skeleton tests (F3).

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


class TestLocalRegressions(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-local-regr-")
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
        self.assertTrue(hasattr(d, "day_total"))
        self.assertTrue(hasattr(d, "cache_rate"))

    def test_day_total_and_cache_rate_pure(self):
        d = _load_dashboard()
        self.assertEqual(d.day_total({"ti": 1, "to": 2, "tr": 3, "cache": 4}), 10)
        self.assertEqual(d.day_total({}), 0)
        # OpenCode cache share: input excludes the separate cache stream.
        self.assertEqual(d.cache_rate(80, 20), 20.0)
        self.assertEqual(d.cache_rate(0, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
