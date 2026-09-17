"""HTTP security skeleton tests (F4).

Skeleton only: discovery smoke + one real pure-helper check.
Binds 127.0.0.1 with port 0 (system-allocated) only; cleans up
server/threads/dirs in tearDown/addCleanup even on failure.
HOME/USERPROFILE/LOCALAPPDATA isolated; no real user data read.
"""
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


class TestHttpSecurity(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-http-sec-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self._server = None
        self._thread = None

    def tearDown(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
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
        self.assertTrue(hasattr(d, "json_html"))

    def test_json_html_escapes_script_breakout(self):
        d = _load_dashboard()
        out = d.json_html({"title": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", out)
        self.assertIn("\\u003c", out)

    def test_loopback_ephemeral_bind_and_cleanup(self):
        # Must bind 127.0.0.1 with port 0 only; never a fixed port.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        self.addCleanup(self._server.server_close)
        host, port = self._server.server_address
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), b"ok")


if __name__ == "__main__":
    unittest.main()
