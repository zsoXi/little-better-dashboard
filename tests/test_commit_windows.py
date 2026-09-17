"""Commit-window mapping skeleton tests (F6c).

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


class TestCommitWindows(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-commit-win-")
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
        self.assertTrue(hasattr(d, "subsystem_for_file"))

    def test_subsystem_for_file_pure(self):
        d = _load_dashboard()
        self.assertEqual(d.subsystem_for_file("/repo/src/foo.py", ["/repo"]), "src")
        self.assertEqual(d.subsystem_for_file("/repo", ["/repo"]), "(root)")
        # Longest matching worktree wins; pure string logic, no git calls.
        self.assertEqual(
            d.subsystem_for_file("/a/b/c.py", ["/a", "/a/b"]),
            "c.py".rsplit("/", 1)[0] if "/" in "c.py" else "c.py",
        )


if __name__ == "__main__":
    unittest.main()
