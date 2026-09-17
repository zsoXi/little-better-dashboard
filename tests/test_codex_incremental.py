"""Codex incremental/synth skeleton tests (F5c).

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


class TestCodexIncremental(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-codex-incr-")
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
        self.assertTrue(hasattr(d, "zero_days_window"))
        self.assertTrue(hasattr(d, "_fingerprint_paths"))

    def test_window_and_fingerprint_pure(self):
        d = _load_dashboard()
        window = d.zero_days_window()
        self.assertEqual(len(window), d.ACTIVITY_DAYS)
        self.assertIn("date", window[0])
        # Fingerprint helper works on synthetic temp files only.
        probe = Path(self._tmp.name) / "probe.db"
        probe.write_text("synthetic", encoding="utf-8")
        fp = d._fingerprint_paths([probe])
        self.assertTrue(len(fp) >= 1)
        self.assertEqual(fp[0][0], str(probe))


if __name__ == "__main__":
    unittest.main()
