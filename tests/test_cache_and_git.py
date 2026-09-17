"""Cache and git fingerprint skeleton tests (F6d).

Skeleton only: discovery smoke + one real pure-helper check.
All fixtures synthetic in temp dirs; HOME/USERPROFILE/LOCALAPPDATA isolated.
No server started; no real user data read; no real git repos touched.
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


class TestCacheAndGit(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-cache-git-")
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
        self.assertTrue(hasattr(d, "what_if_cost"))
        self.assertTrue(hasattr(d, "_fingerprint_paths"))

    def test_pricing_and_fingerprint_pure(self):
        d = _load_dashboard()
        # Known free model: 1M in + 1M out at (0.10, 0.20) -> 0.30.
        self.assertEqual(
            d.what_if_cost("muse-spark-1.3-contributor-free", 1000000, 1000000), 0.3
        )
        self.assertEqual(d.what_if_cost("unknown-model", 1000000, 1000000), 0.0)
        # Fingerprint covers only synthetic temp paths, never the real HOME.
        probe = Path(self._tmp.name) / "synth.db"
        probe.write_bytes(b"synthetic")
        fp = d._fingerprint_paths([probe])
        self.assertTrue(any(str(probe) in str(entry[0]) for entry in fp))


if __name__ == "__main__":
    unittest.main()
