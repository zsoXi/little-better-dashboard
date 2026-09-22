"""In-app update tests.

Version compare, the GitHub check (faked, never live) and the verified
apply path on a synthetic temp install. No network access is performed.
"""
import hashlib
import io
import sys
import tempfile
import unittest
import urllib.error
import warnings
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DASHBOARD = REPO_ROOT / "opencode_dashboard.py"
ZIP_NAME = "little-better-dashboard.zip"


def _load_dashboard():
    import opencode_dashboard as d
    return d


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _release(tag, assets):
    return {"tag": tag, "url": "https://github.com/x/y/releases/tag/" + tag,
            "notes": "", "assets": assets}


def _asset(name):
    return {"name": name, "url": "https://github.com/download/" + name, "size": 1}


class TestVersionCompare(unittest.TestCase):
    def test_version_tuple(self):
        d = _load_dashboard()
        self.assertEqual(d._version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(d._version_tuple("2.0"), (2, 0, 0))
        self.assertEqual(d._version_tuple(""), (0, 0, 0))
        self.assertGreater(d._version_tuple("v1.10.0"), d._version_tuple("v1.9.9"))


class TestUpdateCheck(unittest.TestCase):
    def test_newer_release_and_asset_pick(self):
        d = _load_dashboard()
        out = d.update_check(fetch=lambda: _release("v9.9.9", [
            _asset("notes.txt"), _asset(ZIP_NAME), _asset("checksums.txt")]))
        self.assertTrue(out["ok"])
        self.assertTrue(out["newer"])
        self.assertEqual(out["latest"], "9.9.9")
        self.assertEqual(out["zip"]["name"], ZIP_NAME)
        self.assertEqual(out["checksums"]["name"], "checksums.txt")

    def test_same_version_is_not_newer(self):
        d = _load_dashboard()
        out = d.update_check(fetch=lambda: _release("v" + d.DASHBOARD_VERSION,
                                                    [_asset(ZIP_NAME)]))
        self.assertTrue(out["ok"])
        self.assertFalse(out["newer"])

    def test_no_release_yet_is_ok(self):
        d = _load_dashboard()
        warnings.simplefilter("ignore", ResourceWarning)

        def _404():
            raise urllib.error.HTTPError("u", 404, "Not Found", None, None)

        out = d.update_check(fetch=_404)
        self.assertTrue(out["ok"])
        self.assertIsNone(out["latest"])
        self.assertFalse(out["newer"])

    def test_offline_reports_error(self):
        d = _load_dashboard()

        def _boom():
            raise OSError("network down")

        out = d.update_check(fetch=_boom)
        self.assertFalse(out["ok"])
        self.assertIn("network down", out["error"])


class TestUpdateApply(unittest.TestCase):
    def _install_dir(self):
        tmp = Path(tempfile.mkdtemp(prefix="lbd-update-"))
        self.addCleanup(lambda: None)  # temp dir is small and left for the OS
        (tmp / "opencode_dashboard.py").write_text("OLD-APP", encoding="utf-8")
        (tmp / "start-dashboard.bat").write_text("OLD-BAT", encoding="utf-8")
        return tmp

    def _fakes(self, d, tmp, zip_bytes, checksum=None, tag="v9.9.9"):
        digest = checksum if checksum is not None else hashlib.sha256(zip_bytes).hexdigest()
        checks = ("%s  %s\n" % (digest, ZIP_NAME)).encode("utf-8")
        checker = lambda: {"ok": True, "current": "1.0.0", "latest": tag.lstrip("v"),
                           "tag": tag, "newer": True, "zip": _asset(ZIP_NAME),
                           "checksums": _asset("checksums.txt")}
        downloader = lambda url: zip_bytes if url.endswith(".zip") else checks
        return checker, downloader

    def test_apply_replaces_installed_files_with_backups(self):
        d = _load_dashboard()
        tmp = self._install_dir()
        zip_bytes = _zip_bytes({"opencode_dashboard.py": "NEW-APP",
                                "start-dashboard.bat": "NEW-BAT",
                                "logo.ico": "NEW-ICON"})  # not installed -> skipped
        checker, downloader = self._fakes(d, tmp, zip_bytes)
        orig_target = d._update_target
        d._update_target = lambda: tmp
        try:
            out = d.update_apply(checker=checker, downloader=downloader)
        finally:
            d._update_target = orig_target
            d.UPDATE_APPLIED = False
        self.assertTrue(out["ok"], out)
        self.assertEqual(sorted(out["files"]), ["opencode_dashboard.py", "start-dashboard.bat"])
        self.assertEqual((tmp / "opencode_dashboard.py").read_text(encoding="utf-8"), "NEW-APP")
        self.assertEqual((tmp / "start-dashboard.bat").read_text(encoding="utf-8"), "NEW-BAT")
        self.assertEqual((tmp / "opencode_dashboard.py.bak").read_text(encoding="utf-8"), "OLD-APP")
        self.assertFalse((tmp / "logo.ico").exists())

    def test_apply_rejects_bad_checksum(self):
        d = _load_dashboard()
        tmp = self._install_dir()
        zip_bytes = _zip_bytes({"opencode_dashboard.py": "EVIL"})
        checker, downloader = self._fakes(d, tmp, zip_bytes, checksum="0" * 64)
        orig_target = d._update_target
        d._update_target = lambda: tmp
        try:
            out = d.update_apply(checker=checker, downloader=downloader)
        finally:
            d._update_target = orig_target
        self.assertFalse(out["ok"])
        self.assertIn("checksum", out["error"])
        self.assertEqual((tmp / "opencode_dashboard.py").read_text(encoding="utf-8"), "OLD-APP")
        self.assertFalse((tmp / "opencode_dashboard.py.bak").exists())

    def test_apply_refuses_when_current(self):
        d = _load_dashboard()
        out = d.update_apply(checker=lambda: {"ok": True, "newer": False})
        self.assertFalse(out["ok"])
        self.assertIn("up to date", out["error"])

    def test_apply_rejects_non_github_download_host(self):
        d = _load_dashboard()
        with self.assertRaises(ValueError):
            d._update_download("https://evil.example.com/app.zip")


class TestUpdateWiring(unittest.TestCase):
    def test_frontend_and_routes_are_wired(self):
        src = DASHBOARD.read_text(encoding="utf-8", errors="replace")
        for token in ('id="update"', "'/api/update/check'", "'/api/update/apply'",
                      "'/api/restart'", 'elif path.startswith("/api/update/check"):',
                      'elif path == "/api/update/apply":', 'elif path == "/api/restart":',
                      "def update_check(", "def update_apply(", "DASHBOARD_VERSION",
                      "OCD_TOKEN", 'UPDATE_APPLIED'):
            self.assertIn(token, src, "missing update wiring: %r" % token)

    def test_restart_keeps_token_and_port(self):
        src = DASHBOARD.read_text(encoding="utf-8", errors="replace")
        self.assertIn('env["OCD_TOKEN"] = tok', src)
        self.assertIn('a for a in sys.argv[1:] if a != "--open"', src)


if __name__ == "__main__":
    unittest.main()
