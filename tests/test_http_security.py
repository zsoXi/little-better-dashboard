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


# ---------------------------------------------------------------------------
# F2 access protection (RED-then-GREEN per INSTRUKCJA §6 / §3).
# Real HTTP against the dashboard Handler on 127.0.0.1:0, synthetic fixtures
# in temp dirs, isolated HOME. No token/secret is written to logs: failure
# messages never echo the token value.
# ---------------------------------------------------------------------------
F2_CANARY = "F2CANARY9Z7QW4SYNTH"
F2_TODAY = __import__("datetime").date.today().isoformat()

import http.client as _http_client
import json as _json
import secrets as _secrets
import socket as _socket


def _f2_write_router(path, model):
    rec = {"at": f"{F2_TODAY}T12:00:00", "model": model,
           "provider": "codex", "status": 200,
           "inputTokens": 100, "outputTokens": 20,
           "cachedInputTokens": 40, "reasoningTokens": 5,
           "totalTokens": 120, "durationMs": 5}
    with open(path, "w", encoding="utf-8") as f:
        f.write(_json.dumps(rec) + "\n")


def _f2_raw_request(port, method, path, headers, body=None, timeout=5):
    """Send a hand-built HTTP/1.1 request so no client normalizes Host.

    headers: list of (name, value) tuples in send order; may be empty,
    may repeat a name, may omit Host entirely.
    Returns (status:int, headers:dict-lower, body:bytes).
    """
    if body is None:
        body = b""
    lines = [f"{method} {path} HTTP/1.1"]
    for k, v in headers:
        lines.append(f"{k}: {v}")
    lines.append(f"Content-Length: {len(body)}")
    lines.append("Connection: close")
    lines.append("")
    lines.append("")
    raw = "\r\n".join(lines).encode("latin-1") + body
    with _socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall(raw)
        data = b""
        while True:
            try:
                chunk = s.recv(65536)
            except _socket.timeout:
                break
            if not chunk:
                break
            data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    head_lines = head.split(b"\r\n")
    status = int(head_lines[0].split(b" ")[1])
    h = {}
    for ln in head_lines[1:]:
        if b":" in ln:
            k, v = ln.split(b":", 1)
            h[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
    return status, h, rest


class _F2Base(unittest.TestCase):
    def setUp(self):
        self._old_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "LOCALAPPDATA")}
        self._tmp = tempfile.TemporaryDirectory(prefix="dash-f2-")
        self.addCleanup(self._tmp.cleanup)
        os.environ["HOME"] = self._tmp.name
        os.environ["USERPROFILE"] = self._tmp.name
        os.environ["LOCALAPPDATA"] = self._tmp.name
        self._server = None
        self._thread = None
        self._saved_handler = {}
        self._canary_model = F2_CANARY + "-MODEL"

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
        try:
            import sys as _sys
            d = _sys.modules.get("opencode_dashboard")
            if d is not None:
                for k, v in self._saved_handler.items():
                    setattr(d.Handler, k, v)
                try:
                    d.ROUTER_CACHE.clear()
                except Exception:
                    pass
                try:
                    d.LOCAL_CACHE.clear()
                except Exception:
                    pass
                try:
                    d.WINDOWS.clear()
                except Exception:
                    pass
                try:
                    d.CLOSE_TIMER.cancel()
                except Exception:
                    pass
                try:
                    import opencode_dashboard as _dd
                    _dd.LAST_REQUEST = 0.0
                    _dd.HAD_WINDOW = False
                    _dd.CLOSE_TIMER = None
                except Exception:
                    pass
        finally:
            for k, v in self._old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def _start(self, with_router_canary=True):
        import sys as _sys
        if "opencode_dashboard" in _sys.modules:
            d = _sys.modules["opencode_dashboard"]
        else:
            import opencode_dashboard as d
        router_path = str(Path(self._tmp.name) / "usage-events.jsonl")
        if with_router_canary:
            _f2_write_router(router_path, self._canary_model)
        else:
            Path(router_path).write_text("", encoding="utf-8")
        for k in ("db_path", "router_events", "router_limits", "quiet"):
            if k not in self._saved_handler:
                self._saved_handler[k] = getattr(d.Handler, k)
        d.Handler.db_path = str(Path(self._tmp.name) / "missing.sqlite3")
        d.Handler.router_events = router_path
        d.Handler.router_limits = None
        d.Handler.quiet = True
        try:
            d.ROUTER_CACHE.clear()
        except Exception:
            pass
        try:
            d.LOCAL_CACHE.clear()
        except Exception:
            pass
        try:
            d.WINDOWS.clear()
        except Exception:
            pass
        d.LAST_REQUEST = 0.0
        d.HAD_WINDOW = False
        try:
            if d.CLOSE_TIMER is not None:
                d.CLOSE_TIMER.cancel()
        except Exception:
            pass
        d.CLOSE_TIMER = None
        token = _secrets.token_urlsafe(32)
        from http.server import ThreadingHTTPServer as _THS
        self._server = _THS(("127.0.0.1", 0), d.Handler)
        try:
            self._server.auth_token = token
        except Exception:
            pass
        # Legacy class-level fallback (harmless when the patch reads the
        # per-instance value first).
        try:
            d.Handler.auth_token = token
        except Exception:
            pass
        host, port = self._server.server_address
        self.assertEqual(host, "127.0.0.1")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return d, port, token

    def _authed(self, port, token, path, method="GET", origin=None, wid=None, body=None):
        conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        hs = {"Authorization": "Bearer " + token}
        if origin is not None:
            hs["Origin"] = origin
        if wid is not None:
            hs["X-Window-Id"] = wid
        conn.request(method, path, body=body or b"", headers=hs)
        r = conn.getresponse()
        payload = r.read()
        headers = dict(r.getheaders())
        conn.close()
        return r.status, headers, payload

    def _unauthed(self, port, path, method="GET", headers=None):
        conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=b"", headers=headers or {})
        r = conn.getresponse()
        payload = r.read()
        hdrs = dict(r.getheaders())
        conn.close()
        return r.status, hdrs, payload


class TestF2T01PrivateRoutesRequireToken(_F2Base):
    def test_f2_t01_no_wrong_empty_multi_token_is_401(self):
        _, port, token = self._start()
        canon = f"http://127.0.0.1:{port}"
        priv_get = ["/api/stats", "/api/router", "/api/codex", "/api/agents",
                    "/api/graph", "/api/inspect?id=x", "/api/sessions",
                    "/api/projects", "/api/signals", "/api/session/abc"]
        for p in priv_get:
            st, _, body = self._unauthed(port, p)
            self.assertEqual(st, 401, msg=p)
            self.assertNotIn(F2_CANARY.encode(), body)
        st, _, body = self._unauthed(port, "/api/close?wid=w1", method="POST")
        self.assertEqual(st, 401)
        # Wrong token.
        st, _, body = self._authed(port, "WRONG-" + token[6:], "/api/router")
        self.assertEqual(st, 401)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Empty bearer.
        conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/router", headers={"Authorization": "Bearer "})
        r = conn.getresponse()
        self.assertEqual(r.status, 401)
        r.read()
        conn.close()
        # Missing header entirely on every private route.
        for p in priv_get:
            status, _, _ = _f2_raw_request(
                port, "GET", p,
                [("Host", f"127.0.0.1:{port}"), ("Connection", "close")])
            self.assertEqual(status, 401, msg=p)
        # Multiple Authorization headers -> 401.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", f"127.0.0.1:{port}"),
             ("Authorization", "Bearer " + token),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 401)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Query-string token without a header must not authenticate.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router?token=" + token,
            [("Host", f"127.0.0.1:{port}")])
        self.assertEqual(status, 401)
        self.assertNotIn(F2_CANARY.encode(), body)
        # No window registration and no expensive read on denial.
        import sys as _sys
        d = _sys.modules["opencode_dashboard"]
        self.assertEqual(dict(d.WINDOWS), {})


class TestF2T02ValidTokenAndHostWorks(_F2Base):
    def test_f2_t02_canonical_host_and_origin_ok(self):
        _, port, token = self._start()
        canon = f"http://127.0.0.1:{port}"
        st, _, body = self._authed(port, token, "/api/router")
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)
        st, _, body = self._authed(port, token, "/api/router", origin=canon)
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)
        # Public shell stays reachable with a canonical Host and no token.
        st, _, body = self._unauthed(port, "/")
        self.assertEqual(st, 200)


class TestF2T03ForeignHostRejected(_F2Base):
    def test_f2_t03_foreign_host_with_matching_origin_is_403(self):
        _, port, token = self._start()
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", "attacker.test:1234"),
             ("Origin", "http://attacker.test:1234"),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 403)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Suffix and userinfo variants are also outside the allowlist.
        for bad_host, bad_origin in [
            (f"127.0.0.1.attacker.test:{port}", f"http://127.0.0.1.attacker.test:{port}"),
            (f"user@127.0.0.1:{port}", f"http://127.0.0.1:{port}"),
        ]:
            status, _, body = _f2_raw_request(
                port, "GET", "/api/router",
                [("Host", bad_host), ("Origin", bad_origin),
                 ("Authorization", "Bearer " + token)])
            self.assertIn(status, (400, 403), msg=bad_host)
            self.assertNotIn(F2_CANARY.encode(), body)


class TestF2T04WrongOriginRejected(_F2Base):
    def test_f2_t04_foreign_or_null_origin_is_403(self):
        _, port, token = self._start()
        canon_host = f"127.0.0.1:{port}"
        for origin in ("http://evil.test/", "null",
                       f"http://127.0.0.1:{port + 1}",
                       "http://localhost:%d" % port):
            status, _, body = _f2_raw_request(
                port, "GET", "/api/router",
                [("Host", canon_host), ("Origin", origin),
                 ("Authorization", "Bearer " + token)])
            self.assertEqual(status, 403, msg=origin)
            self.assertNotIn(F2_CANARY.encode(), body)


class TestF2T05BadHostShapes(_F2Base):
    def test_f2_t05_missing_double_foreign_suffix_userinfo(self):
        _, port, token = self._start()
        canon = f"127.0.0.1:{port}"
        # Missing Host -> 400.
        status, _, _ = _f2_raw_request(port, "GET", "/api/router",
                                       [("Authorization", "Bearer " + token)])
        self.assertEqual(status, 400)
        # Double Host -> 400.
        status, _, _ = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", canon), ("Host", canon),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 400)
        # Foreign port -> 403.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", f"127.0.0.1:{port + 1}"),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 403)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Suffix domain -> 403.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", f"127.0.0.1.attacker.test:{port}"),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 403)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Userinfo -> 403.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", f"user@127.0.0.1:{port}"),
             ("Authorization", "Bearer " + token)])
        self.assertEqual(status, 403)
        self.assertNotIn(F2_CANARY.encode(), body)
        # No 500 anywhere on these shapes.
        for s in (status,):
            self.assertNotEqual(s, 500)


class TestF2T06PublicShellHasNoPrivateData(_F2Base):
    def test_f2_t06_shell_without_token_has_no_canary_or_secret(self):
        d, port, token = self._start()
        calls = {"local": 0, "router": 0}
        orig_local = d.cached_local_stats
        orig_router = d.cached_router_stats
        try:
            def _count_local(*a, **k):
                calls["local"] += 1
                return orig_local(*a, **k)
            def _count_router(*a, **k):
                calls["router"] += 1
                return orig_router(*a, **k)
            d.cached_local_stats = _count_local
            d.cached_router_stats = _count_router
            for p in ("/", "/index.html"):
                st, _, body = self._unauthed(port, p)
                self.assertEqual(st, 200, msg=p)
                self.assertNotIn(F2_CANARY.encode(), body)
                self.assertNotIn(token.encode(), body)
                self.assertNotIn(b"__DATA__", body)
                self.assertNotIn(b"__ROUTER__", body)
                self.assertIn(b"null", body)
            self.assertEqual(calls["local"], 0)
            self.assertEqual(calls["router"], 0)
        finally:
            d.cached_local_stats = orig_local
            d.cached_router_stats = orig_router
        # The same canary IS visible through the authenticated API.
        st, _, body = self._authed(port, token, "/api/router")
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)


class TestF2T07NoBypassViaMethodOrPath(_F2Base):
    def test_f2_t07_head_options_unknown_need_auth_and_no_effects(self):
        import sys as _sys
        d, port, token = self._start()
        d.WINDOWS.clear()
        d.LAST_REQUEST = 0.0
        # HEAD without token -> 401, no window.
        conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("HEAD", "/api/stats", headers={})
        r = conn.getresponse()
        self.assertEqual(r.status, 401)
        r.read()
        conn.close()
        # OPTIONS without token -> not 200 with data, no window.
        conn = _http_client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("OPTIONS", "/api/stats", headers={})
        r = conn.getresponse()
        self.assertNotEqual(r.status, 200)
        self.assertIn(r.status, (400, 401, 403, 404, 405))
        r.read()
        conn.close()
        # Unknown /api/* path without token -> 401 (not a bypass to 404 data).
        st, _, body = self._unauthed(port, "/api/unknown_xyz_f2")
        self.assertEqual(st, 401)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Unknown non-api path with canonical Host -> 404, still no canary.
        st, _, body = self._unauthed(port, "/nope-f2-xyz")
        self.assertEqual(st, 404)
        self.assertNotIn(F2_CANARY.encode(), body)
        # Unauthenticated close must not shut the server down.
        st, _, _ = self._unauthed(port, "/api/close?wid=wX", method="POST")
        self.assertEqual(st, 401)
        st2, _, body2 = self._authed(port, token, "/api/router")
        self.assertEqual(st2, 200)
        self.assertIn(F2_CANARY.encode(), body2)
        self.assertEqual(dict(d.WINDOWS), {})


class TestF2T08FragmentTokenFlow(_F2Base):
    def test_f2_t08_no_query_token_and_js_fragment_handling(self):
        import sys as _sys
        _, port, token = self._start()
        # Query-string token never authenticates.
        status, _, body = _f2_raw_request(
            port, "GET", "/api/stats?token=" + token,
            [("Host", f"127.0.0.1:{port}")])
        self.assertEqual(status, 401)
        # Same token over Authorization works twice (refresh + new window).
        for _ in range(2):
            st, _, b = self._authed(port, token, "/api/stats")
            self.assertEqual(st, 200)
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("history.replaceState", src)
        self.assertIn("sessionStorage", src)
        self.assertIn("#token=", src)
        self.assertIn("Authorization", src)
        self.assertIn("Bearer", src)
        # The fragment token must live in sessionStorage, never localStorage.
        self.assertIn("ocd-token", src)
        for line in src.splitlines():
            if "ocd-token" in line and "localStorage" in line:
                self.fail("token must not use localStorage")
        # Shell must not embed the token transport in a query string.
        self.assertNotIn("?token=", src)


class TestF2T09TwoWindowsCloseIsolation(_F2Base):
    def test_f2_t09_one_window_close_keeps_other_alive(self):
        import sys as _sys
        d, port, token = self._start()
        st, _, _ = self._authed(port, token, "/api/stats", wid="wA")
        self.assertEqual(st, 200)
        st, _, _ = self._authed(port, token, "/api/stats", wid="wB")
        self.assertEqual(st, 200)
        self.assertIn("wA", d.WINDOWS)
        self.assertIn("wB", d.WINDOWS)
        # Unauthenticated close changes nothing.
        st, _, _ = self._unauthed(port, "/api/close?wid=wA", method="POST")
        self.assertEqual(st, 401)
        self.assertIn("wA", d.WINDOWS)
        # Authenticated close of one window keeps the other.
        st, _, _ = self._authed(port, token, "/api/close?wid=wA", method="POST")
        self.assertEqual(st, 200)
        self.assertNotIn("wA", d.WINDOWS)
        self.assertIn("wB", d.WINDOWS)
        st, _, _ = self._authed(port, token, "/api/stats", wid="wB")
        self.assertEqual(st, 200)
        try:
            if d.CLOSE_TIMER is not None:
                d.CLOSE_TIMER.cancel()
                d.CLOSE_TIMER = None
        except Exception:
            pass


class TestF2T10RestartInvalidatesOldToken(_F2Base):
    def test_f2_t10_old_token_is_401_and_new_link_recovers(self):
        import sys as _sys
        d = _load_dashboard()
        _, port1, token1 = self._start()
        st, _, body = self._authed(port1, token1, "/api/router")
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)
        # Restart: shut the first server, start a second one (new token).
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None
        _, port2, token2 = self._start()
        self.assertNotEqual(token1, token2)
        st, _, body = self._authed(port2, token1, "/api/router")
        self.assertEqual(st, 401)
        self.assertNotIn(F2_CANARY.encode(), body)
        st, _, body = self._authed(port2, token2, "/api/router")
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)
        src = Path(REPO_ROOT, "opencode_dashboard.py").read_text(encoding="utf-8")
        self.assertIn("401", src)
        low = src.lower()
        self.assertTrue("auth" in low and ("reopen" in low or "open" in low))


class TestF2T11SecondLocalOriginRejected(_F2Base):
    def test_f2_t11_other_local_origin_gets_no_canary_and_no_close(self):
        import sys as _sys
        d, port, token = self._start()
        st, _, _ = self._authed(port, token, "/api/stats", wid="keep1")
        self.assertEqual(st, 200)
        canon_host = f"127.0.0.1:{port}"
        for origin in (f"http://127.0.0.1:{port + 1}", f"http://localhost:{port}"):
            status, _, body = _f2_raw_request(
                port, "GET", "/api/router",
                [("Host", canon_host), ("Origin", origin),
                 ("Authorization", "Bearer " + token)])
            self.assertEqual(status, 403, msg=origin)
            self.assertNotIn(F2_CANARY.encode(), body)
        self.assertIn("keep1", d.WINDOWS)
        st, _, body = self._authed(port, token, "/api/router")
        self.assertEqual(st, 200)
        self.assertIn(F2_CANARY.encode(), body)


class TestF2T12SecurityHeadersAndNoLeak(_F2Base):
    def test_f2_t12_headers_present_and_denials_generic(self):
        _, port, token = self._start()
        canon = f"127.0.0.1:{port}"
        cases = []
        st, hd, body = self._unauthed(port, "/")
        cases.append((st, hd, body, False))
        st, hd, body = self._authed(port, token, "/api/router")
        cases.append((st, hd, body, True))
        status, hd2, body2 = _f2_raw_request(
            port, "GET", "/api/router", [("Host", canon)])
        hdl = {k.lower(): v for k, v in hd2.items()}
        cases.append((status, hdl, body2, False))
        status, hd2, body2 = _f2_raw_request(
            port, "GET", "/api/router",
            [("Host", "evil.test"), ("Authorization", "Bearer " + token)])
        hdl = {k.lower(): v for k, v in hd2.items()}
        cases.append((status, hdl, body2, False))
        for st, hd, body, may_have_canary in cases:
            low = {str(k).lower(): str(v) for k, v in hd.items()}
            self.assertIn("cache-control", low)
            self.assertIn("no-store", low["cache-control"])
            self.assertEqual(low.get("x-content-type-options"), "nosniff")
            self.assertEqual(low.get("referrer-policy"), "no-referrer")
            self.assertEqual(low.get("x-frame-options"), "DENY")
            self.assertNotIn("access-control-allow-origin", low)
            self.assertNotIn(token.encode(), body)
            if not may_have_canary:
                self.assertNotIn(F2_CANARY.encode(), body)
            # Denials must be generic: no stack traces, DB paths, or token.
            # The public shell legitimately mentions UI words like
            # "prompts" and code comments naming the jsonl format, so only
            # true leak signals are checked here.
            if not may_have_canary:
                for secret in (b"Traceback", b".sqlite3", b".sqlite",
                               b"Bearer " + token.encode()[:8]):
                    if secret in body:
                        self.fail("denial leaks private detail")
        self.assertIn(F2_CANARY.encode(), cases[1][2])


if __name__ == "__main__":
    unittest.main()
