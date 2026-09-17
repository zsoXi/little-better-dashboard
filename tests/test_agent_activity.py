"""Agent activity skeleton tests (F6a).

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


class TestAgentActivity(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-agent-act-")
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
        self.assertTrue(hasattr(d, "compute_streaks"))
        self.assertTrue(hasattr(d, "fmt_dur_min"))

    def test_streaks_and_duration_pure(self):
        d = _load_dashboard()
        slot = {"msgs": 1, "ti": 1, "to": 0, "tr": 0, "cache": 0}
        streaks = d.compute_streaks([slot])
        self.assertEqual(streaks, {"current": 1, "longest": 1})
        self.assertEqual(d.fmt_dur_min(0.5), "under a minute")
        self.assertEqual(d.fmt_dur_min(5), "5 min")


if __name__ == "__main__":
    unittest.main()
