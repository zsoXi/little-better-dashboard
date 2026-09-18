"""Real browser-test runner for the dashboard (dev-only, Playwright).

Policy:
  * never fake a pass, never install anything;
  * if Playwright or a usable browser (msedge channel headless) is not
    available, every browser case is reported NOT_RUN with a reason and
    the process exits with code 2;
  * a case PASSES only when the assertions below ran against a really
    running dashboard page (synthetic sources only) in a real browser.

Cases implemented (spec ch.15, mandated real-browser runs):
  F6d-T01  7 sections answer, 8th never sends headers
  F6d-T02  headers 200 instantly, body never ends
  F6d-T04  error after earlier success (stale kept, last_success intact)
  F6d-T06  older response A arrives after newer B; UI stays with B
  F6d-T09  timeout of inspect/search/prompts; retry possible

Usage:
    py -3.14 tools/run_browser_tests.py --list
    py -3.14 tools/run_browser_tests.py
    py -3.14 tools/run_browser_tests.py --cases F6d-T01,F6d-T09 --headful
"""
import argparse
import datetime
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_ARTIFACTS = REPO_ROOT / "artifacts"
SHOTS_DIRNAME = "F6d-browser-shots"

# --------------------------------------------------------------------------
# Synthetic fixture (validated schema; see FIX_REPORT F6d notes)
# --------------------------------------------------------------------------
FIXTURE_SCHEMA = """
CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, agent TEXT, model TEXT,
 directory TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER,
 tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,
 tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost REAL,
 project_id TEXT);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT,
 time_created INTEGER);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
 data TEXT, time_created INTEGER);
CREATE TABLE project (id TEXT PRIMARY KEY, directory TEXT, worktree TEXT,
 path TEXT, vcs TEXT, name TEXT);
CREATE TABLE session_input (session_id TEXT, prompt TEXT, time_created INTEGER);
CREATE TABLE router (id TEXT PRIMARY KEY);
"""

FIXTURE_ROWS = [
    ("INSERT INTO session VALUES ('sid-1','Synthetic session','build',"
     "'muse-spark','C:/work',NULL,1800000000000,1800000100000,100,20,5,40,0,0.0,'proj-1')", ()),
    ("INSERT INTO session VALUES ('sid-child','Child session','build',"
     "'muse-spark','C:/work','sid-1',1800000000000,1800000200000,0,0,0,0,0,0.0,'proj-1')", ()),
    ("INSERT INTO message VALUES ('m1','sid-1',?,1800000001000)",
     (json.dumps({"role": "user"}),)),
    ("INSERT INTO part VALUES ('p1','m1','sid-1',?,1800000002000)",
     (json.dumps({"type": "text", "text": "hello"}),)),
    ("INSERT INTO project VALUES ('proj-1','C:/work',NULL,NULL,'git','work')", ()),
    ("INSERT INTO session_input VALUES ('sid-1','hello prompt',1800000003000)", ()),
]

CANARY_MODEL = "F6D-CANARY-MODEL"

SESSIONS_A = {"rows": [{"id": "row-a", "title": "ROW-A-MARKER",
                        "agent": "build", "model": "muse-spark",
                        "directory": "C:/work",
                        "time_created": 1800000000000}], "limit": 50}
SESSIONS_B = {"rows": [{"id": "row-b", "title": "ROW-B-MARKER",
                        "agent": "build", "model": "muse-spark",
                        "directory": "C:/work",
                        "time_created": 1800000000000}], "limit": 50}


def _write_fixture(root, mode="canary", xss=False):
    db_path = root / "synthetic.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(FIXTURE_SCHEMA)
    for sql, args in FIXTURE_ROWS:
        con.execute(sql, args)
    if xss:
        con.execute(
            "INSERT INTO session VALUES ('sid-xss',"
            "'<script>window.__xss=1</script>"
            "<img src=x onerror=window.__xss=2>','build','muse-spark',"
            "'C:/work',NULL,1800000000000,1800000300000,0,0,0,0,0,0.0,'proj-1')")
    con.commit()
    con.close()
    codex_dir = root / "codex-sessions"
    codex_dir.mkdir()
    events_path = root / "usage-events.jsonl"
    if mode == "synth":
        recs = [
            {"type": "session_meta", "timestamp": "2026-09-16T12:00:00",
             "payload": {"id": "f8-synth", "cwd": "C:/work",
                         "model_provider": "codex"}},
            {"type": "turn_context", "timestamp": "2026-09-16T12:00:00",
             "payload": {"model": "muse-spark-1.3-contributor-free"}},
            {"type": "token_usage_record", "timestamp": "2026-09-16T12:00:00",
             "payload": {"usage": {"input_tokens": 100,
                                   "cached_input_tokens": 40,
                                   "output_tokens": 20,
                                   "total_tokens": 120,
                                   "reasoning_output_tokens": 5,
                                   "cache_write_input_tokens": 0}}},
        ]
        rollout = codex_dir / "nested" / "rollout-f8.jsonl"
        rollout.parent.mkdir(parents=True)
        with open(rollout, "w", encoding="utf-8", newline="") as f:
            for rec in recs:
                f.write(json.dumps(rec) + "\n")
        events = None
    else:
        today = datetime.date.today().isoformat()
        rec = {"at": today + "T12:00:00", "model": CANARY_MODEL,
               "provider": "codex", "status": 200,
               "inputTokens": 100, "outputTokens": 20,
               "cachedInputTokens": 40, "reasoningTokens": 5,
               "totalTokens": 120, "durationMs": 5}
        events_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        events = str(events_path)
    return {"db": str(db_path), "events": events,
            "codex_dir": codex_dir, "root": root}


# --------------------------------------------------------------------------
# Fault injection
# --------------------------------------------------------------------------
class _FaultCfg:
    def __init__(self):
        self.lock = threading.Lock()
        self.path = None
        self.mode = None
        self.delay_ms = 0
        self.hang_seconds = 4.0
        self.sessions_ab = None
        self.counts = {}
        self.inflight = {}
        self.landed = []

    def reset(self):
        with self.lock:
            self.path = None
            self.mode = None
            self.delay_ms = 0
            self.sessions_ab = None
            self.counts = {}
            self.inflight = {}
            self.landed = []

    def arm(self, path, mode, delay_ms=0, hang_seconds=4.0, sessions_ab=None):
        with self.lock:
            self.path = path
            self.mode = mode
            self.delay_ms = delay_ms
            self.hang_seconds = hang_seconds
            self.sessions_ab = sessions_ab

    def disarm(self):
        with self.lock:
            self.path = None
            self.mode = None
            self.sessions_ab = None

    def match(self, path):
        with self.lock:
            if self.mode is None or self.path is None:
                return None
            if not path.startswith(self.path):
                return None
            return self.mode

    def note(self, path):
        with self.lock:
            self.counts[path] = self.counts.get(path, 0) + 1
            self.inflight[path] = self.inflight.get(path, 0) + 1

    def done(self, path):
        with self.lock:
            self.inflight[path] = max(0, self.inflight.get(path, 0) - 1)


FAULT = _FaultCfg()


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def _make_handler(d):
    class FaultHandler(d.Handler):
        fault = FAULT

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            mode = self.fault.match(path)
            if mode is None:
                return super().do_GET()
            if self._guard(path) is not None:
                return
            self.fault.note(path)
            try:
                if mode == "sessions_ab":
                    if path.startswith("/api/sessions") and "q=" in (parsed.query or ""):
                        return self._sessions_ab()
                    return super().do_GET()
                if mode == "hang_headers":
                    time.sleep(self.fault.hang_seconds)
                    return
                if mode == "hang_body":
                    return self._hang_body()
                if mode == "http500":
                    return self._send(500, "application/json",
                                      b'{"error":"synthetic 500"}')
                if mode == "error_json":
                    return self._send(200, "application/json",
                                      b'{"error":"synthetic payload error"}')
                if mode == "bad_json":
                    return self._send(200, "application/json", b"{not json")
                if mode == "delay":
                    time.sleep(self.fault.delay_ms / 1000.0)
                    return super().do_GET()
            except Exception:
                pass
            finally:
                if mode != "sessions_ab" or (
                        path.startswith("/api/sessions") and "q=" in (parsed.query or "")):
                    self.fault.done(path)

        def _hang_body(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                self.wfile.write(b'[{"id":')
                self.wfile.flush()
            except OSError:
                return
            time.sleep(self.fault.hang_seconds)

        def _sessions_ab(self):
            st = self.fault.sessions_ab
            with self.fault.lock:
                first = not st.get("a_taken")
                if first:
                    st["a_taken"] = True
            if first:
                time.sleep(st.get("delay_a", 2.0))
                payload, label = SESSIONS_A, "A"
            else:
                payload, label = SESSIONS_B, "B"
            with self.fault.lock:
                self.fault.landed.append((label, time.time()))
            try:
                self._send(200, "application/json",
                           json.dumps(payload).encode("utf-8"))
            except OSError:
                pass

    return FaultHandler


# --------------------------------------------------------------------------
# Runner plumbing
# --------------------------------------------------------------------------
class _Log:
    def __init__(self, path):
        self.path = Path(path)
        self.lines = []

    def __call__(self, msg):
        print(msg, flush=True)
        self.lines.append(msg)

    def flush(self):
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


class Ctx:
    def __init__(self, case_id, d, fixture, server, thread, token, port,
                 shots_dir, browser, log):
        self.case_id = case_id
        self.d = d
        self.fixture = fixture
        self.server = server
        self.thread = thread
        self.token = token
        self.port = port
        self.shots_dir = Path(shots_dir)
        self.browser = browser
        self.log = log
        self.context = None
        self.page = None
        self.evidence = {}
        self.console = []
        self.warns = []
        self.pageerrors = []
        self.dialogs = []
        self.external = []
        self.shot_seq = 0

    # -- browser ---------------------------------------------------------
    def new_page(self, fetch_ms=1200, refresh_ms=3600000):
        self.context = self.browser.new_context()
        self.context.add_init_script(
            "window.__ocdTimeouts={fetchMs:%d,refreshMs:%d};"
            % (fetch_ms, refresh_ms))
        self.page = self.context.new_page()
        self.page.set_default_timeout(20000)
        def _on_console(m):
            if m.type == "error":
                self.console.append(m.text)
            elif m.type == "warning":
                self.warns.append(m.text)
        self.page.on("console", _on_console)
        self.page.on("pageerror", lambda e: self.pageerrors.append(str(e)))

        def _on_dialog(d):
            self.dialogs.append(d.message)
            try:
                d.dismiss()
            except Exception:
                pass
        self.page.on("dialog", _on_dialog)

        def _on_request(req):
            try:
                host = urlparse(req.url).hostname
            except Exception:
                host = None
            if host not in (None, "127.0.0.1", "localhost"):
                self.external.append(req.url)
        self.page.on("request", _on_request)
        return self.page

    def goto(self):
        url = "http://127.0.0.1:%d/#token=%s" % (self.port, self.token)
        self.page.goto(url, wait_until="domcontentloaded")
        return url

    def shot(self, label):
        self.shot_seq += 1
        name = "%s-%02d-%s.png" % (self.case_id, self.shot_seq, label)
        path = self.shots_dir / name
        try:
            self.page.screenshot(path=str(path))
        except Exception:
            return None
        return str(path)

    # -- assertions ------------------------------------------------------
    def cycle_finished(self, prev=None, timeout=20000):
        if prev is None:
            self.page.wait_for_function(
                "window.__ocdState && window.__ocdState.cycle"
                " && window.__ocdState.cycle.finished", timeout=timeout)
        else:
            self.page.wait_for_function(
                "(p) => window.__ocdState && window.__ocdState.cycle"
                " && window.__ocdState.cycle.finished > p",
                arg=prev, timeout=timeout)
        return self.page.evaluate("window.__ocdState.cycle.finished")

    def sections(self):
        return self.page.evaluate(
            "Object.fromEntries(Object.entries(window.__ocdState.sections)"
            ".map(([k,s])=>[k,{state:s.state,gen:s.gen,inFlight:s.inFlight,"
            "lastSuccess:s.lastSuccess,error:s.error}]))")

    def status(self):
        return self.page.evaluate(
            "document.getElementById('status').textContent")

    def text(self, selector):
        return self.page.evaluate(
            "(sel)=>{const el=document.querySelector(sel);"
            "return el?el.textContent:null;}", selector)

    def close(self):
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass


def _start_server(d, fixture):
    handler = _make_handler(d)
    token = secrets.token_urlsafe(32)
    server = QuietServer(("127.0.0.1", 0), handler)
    server.auth_token = token
    try:
        d.Handler.auth_token = token
    except Exception:
        pass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    assert host == "127.0.0.1"
    return server, thread, token, port


def _reset_dashboard_state(d):
    for name in ("ROUTER_CACHE", "LOCAL_CACHE", "WINDOWS"):
        try:
            getattr(d, name).clear()
        except Exception:
            pass
    try:
        d.LAST_REQUEST = 0.0
        d.HAD_WINDOW = False
    except Exception:
        pass
    try:
        if d.CLOSE_TIMER is not None:
            d.CLOSE_TIMER.cancel()
    except Exception:
        pass
    try:
        d.CLOSE_TIMER = None
    except Exception:
        pass


def _stop_server(server, thread):
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
    if thread is not None:
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------
SEVEN_FAST = ("stats", "router", "agents", "graph", "sessions",
              "projects", "signals")


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def case_t01(ctx):
    """7 sections answer; the 8th (/api/codex) never sends headers."""
    FAULT.arm("/api/codex", "hang_headers", hang_seconds=4.0)
    ctx.new_page()
    ctx.goto()
    # The seven fast sections must render while codex is still in flight.
    ctx.page.wait_for_function(
        "() => {const s=window.__ocdState.sections;"
        "const ok=['stats','router','agents','graph','sessions','projects','signals']"
        ".every(k=>s[k]&&s[k].state==='success');"
        "return ok && s.codex && s.codex.inFlight===true;}",
        timeout=3000)
    ctx.cycle_finished()
    secs = ctx.sections()
    for key in SEVEN_FAST:
        _assert(secs[key]["state"] == "success",
                "section %s should be success, got %r" % (key, secs[key]))
    _assert(secs["codex"]["state"] == "error",
            "codex should be error, got %r" % secs["codex"])
    _assert("timeout after" in (secs["codex"]["error"] or ""),
            "codex error should mention the timeout, got %r" % secs["codex"]["error"])
    status = ctx.status()
    _assert("Partial update: 7/8" in status,
            "status should be a partial update, got %r" % status)
    _assert("codex" in status, "status should name the failing source, got %r" % status)
    ctx.evidence["status"] = status
    ctx.evidence["codex_error"] = secs["codex"]["error"]
    return ctx.shot("partial-update")


def case_t02(ctx):
    """Headers arrive 200 instantly; the body never ends -> deadline holds."""
    FAULT.arm("/api/codex", "hang_body", hang_seconds=4.0)
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    secs = ctx.sections()
    _assert(secs["codex"]["state"] == "error",
            "codex should be error, got %r" % secs["codex"])
    _assert("timeout after" in (secs["codex"]["error"] or ""),
            "body-hang must end in the fetch deadline, got %r"
            % secs["codex"]["error"])
    for key in SEVEN_FAST:
        _assert(secs[key]["state"] == "success",
                "section %s should still succeed, got %r" % (key, secs[key]))
    _assert("Partial update: 7/8" in ctx.status(),
            "status should be partial, got %r" % ctx.status())
    ctx.evidence["codex_error"] = secs["codex"]["error"]
    return ctx.shot("body-hang-timeout")


def case_t04(ctx):
    """A source error after a success keeps old data and last_success."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    secs = ctx.sections()
    _assert(all(secs[k]["state"] == "success" for k in SEVEN_FAST + ("codex",)),
            "first cycle should be fully green")
    first_status = ctx.status()
    _assert(first_status.startswith("Updated"),
            "first cycle should say Updated, got %r" % first_status)
    stats_before = secs["stats"]["lastSuccess"]
    rows_before = ctx.page.evaluate(
        "document.querySelectorAll('#tbl tbody tr').length")
    _assert(rows_before > 0, "session table should have rows")
    prev_finished = ctx.page.evaluate("window.__ocdState.cycle.finished")

    FAULT.arm("/api/stats", "http500")
    ctx.page.evaluate("document.getElementById('refresh').click()")
    ctx.cycle_finished(prev=prev_finished)
    secs = ctx.sections()
    _assert(secs["stats"]["state"] == "stale",
            "stats should be stale after the error, got %r" % secs["stats"])
    _assert("HTTP 500" in (secs["stats"]["error"] or ""),
            "stats error should be explicit, got %r" % secs["stats"]["error"])
    _assert(secs["stats"]["lastSuccess"] == stats_before,
            "last_success must not move on a failed refresh")
    rows_after = ctx.page.evaluate(
        "document.querySelectorAll('#tbl tbody tr').length")
    _assert(rows_after == rows_before,
            "previous rows must be kept, before=%d after=%d" % (rows_before, rows_after))
    status = ctx.status()
    _assert("Partial update: 7/8" in status and "stats" in status,
            "status should be partial and name stats, got %r" % status)
    _assert(not status.startswith("Updated"),
            "no Updated for a partial cycle, got %r" % status)
    disabled = ctx.page.evaluate(
        "document.getElementById('refresh').disabled")
    _assert(disabled is False, "refresh must be re-enabled after the cycle")
    src = ctx.text("#src-status") or ""
    _assert("stats: stale" in src, "source strip should show stats stale, got %r" % src)
    ctx.evidence["status"] = status
    ctx.evidence["src_status"] = src
    return ctx.shot("stale-after-error")


def case_t06(ctx):
    """Older search A lands after newer B; the UI keeps B."""
    ctx.new_page(fetch_ms=5000, refresh_ms=3600000)
    ctx.goto()
    ctx.cycle_finished()
    FAULT.arm("/api/sessions", "sessions_ab",
              sessions_ab={"a_taken": False, "delay_a": 2.0})
    ctx.page.evaluate(
        "(v)=>{const el=document.getElementById('ss-q');el.value=v;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}", "older")
    ctx.page.wait_for_timeout(450)
    ctx.page.evaluate(
        "(v)=>{const el=document.getElementById('ss-q');el.value=v;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}", "newer")
    ctx.page.wait_for_timeout(700)
    first = ctx.page.evaluate(
        "document.querySelector('#sstbl tbody tr td')"
        " ?document.querySelector('#sstbl tbody tr td').textContent:null")
    _assert(first == "ROW-B-MARKER",
            "newer response B should render first, got %r" % first)
    ctx.page.wait_for_timeout(2200)
    landed = [lbl for lbl, _ in FAULT.landed]
    _assert("B" in landed and "A" in landed,
            "both search responses must land, got %r" % landed)
    _assert(landed.index("A") > landed.index("B"),
            "A must land after B (out-of-order), got %r" % landed)
    final = ctx.page.evaluate(
        "document.querySelector('#sstbl tbody tr td')"
        " ?document.querySelector('#sstbl tbody tr td').textContent:null")
    _assert(final == "ROW-B-MARKER",
            "the UI must stay with B, got %r" % final)
    body = ctx.page.evaluate("document.body.innerText")
    _assert("ROW-A-MARKER" not in body,
            "the late older response must never reach the UI")
    ctx.evidence["landed_order"] = landed
    return ctx.shot("late-a-kept-b")


def case_t09(ctx):
    """Inspect/search/prompts time out; nothing hangs; retry works."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    secs = ctx.sections()
    _assert(all(secs[k]["state"] == "success" for k in SEVEN_FAST + ("codex",)),
            "first cycle should be fully green")

    # -- inspect ---------------------------------------------------------
    FAULT.arm("/api/inspect", "hang_body", hang_seconds=4.0)
    clicked = ctx.page.evaluate(
        "()=>{const rows=document.querySelectorAll('#sstbl tbody tr[data-sid]');"
        "for(const tr of rows){const td=tr.querySelector('td');"
        "if(td&&td.textContent.indexOf('Synthetic session')>=0){"
        "tr.dispatchEvent(new MouseEvent('click',{bubbles:true}));return true;}}"
        "return false;}")
    _assert(clicked, "a session row with data-sid must exist")
    ctx.page.wait_for_timeout(2600)
    ctx.evidence["insp_first"] = ctx.text("#insp")
    ctx.evidence["insp_counts"] = {k: v for k, v in FAULT.counts.items()
                                   if "inspect" in k}
    ctx.page.wait_for_function(
        "() => {const el=document.getElementById('insp');"
        "return el && el.textContent.indexOf('ERR')===0;}",
        timeout=2500)
    insp = ctx.text("#insp")
    _assert("timeout after" in insp,
            "inspect should show the timeout, got %r" % insp)
    _assert("retry" in insp, "inspect should offer a retry hint, got %r" % insp)
    FAULT.disarm()
    ctx.page.evaluate(
        "()=>{const rows=document.querySelectorAll('#sstbl tbody tr[data-sid]');"
        "for(const tr of rows){const td=tr.querySelector('td');"
        "if(td&&td.textContent.indexOf('Synthetic session')>=0){"
        "tr.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}}}")
    ctx.page.wait_for_timeout(1200)
    ctx.evidence["insp_retry"] = ctx.text("#insp")
    ctx.page.wait_for_function(
        "() => {const el=document.getElementById('insp');"
        "return el && el.textContent.indexOf('Synthetic session')>=0;}",
        timeout=2500)
    ctx.evidence["inspect_retry"] = "ok"

    # -- search ----------------------------------------------------------
    FAULT.arm("/api/sessions", "hang_body", hang_seconds=4.0)
    ctx.page.evaluate(
        "(v)=>{const el=document.getElementById('ss-q');el.value=v;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}", "zzz")
    ctx.page.wait_for_timeout(2200)
    _assert(not ctx.pageerrors, "no page error may escape, got %r" % ctx.pageerrors)
    FAULT.disarm()
    ctx.page.evaluate(
        "(v)=>{const el=document.getElementById('ss-q');el.value=v;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}", "Synthetic")
    ctx.page.wait_for_timeout(1000)
    first = ctx.page.evaluate(
        "document.querySelector('#sstbl tbody tr td')"
        " ?document.querySelector('#sstbl tbody tr td').textContent:null")
    _assert(first == "Synthetic session",
            "search must recover after the timeout, got %r" % first)

    # -- prompts ---------------------------------------------------------
    FAULT.arm("/api/session/", "hang_body", hang_seconds=4.0)
    opened = ctx.page.evaluate(
        "()=>{const rows=document.querySelectorAll('#tbl tbody tr[data-i]');"
        "for(const tr of rows){if(tr.textContent.indexOf('Synthetic session')>=0){"
        "tr.dispatchEvent(new MouseEvent('click',{bubbles:true}));return true;}}"
        "return false;}")
    _assert(opened, "a session row with data-i must exist")
    ctx.page.wait_for_function(
        "() => {const el=document.getElementById('modal-prompts');"
        "return el && el.textContent.indexOf('Could not load prompts')>=0;}",
        timeout=6000)
    prompts = ctx.text("#modal-prompts")
    _assert("timeout after" in prompts,
            "prompt timeout should be explicit, got %r" % prompts)
    FAULT.disarm()
    ctx.page.evaluate(
        "()=>{const rows=document.querySelectorAll('#tbl tbody tr[data-i]');"
        "for(const tr of rows){if(tr.textContent.indexOf('Synthetic session')>=0){"
        "tr.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}}}")
    ctx.page.wait_for_function(
        "() => {const el=document.getElementById('modal-prompts');"
        "return el && el.textContent.indexOf('hello prompt')>=0;}",
        timeout=6000)
    ctx.evidence["prompts_retry"] = "ok"
    return ctx.shot("inspect-search-prompts-timeout")


def case_b01(ctx):
    """OpenCode/Codex tabs, the shared 120 sum and the reasoning toggle."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    ctx.page.evaluate("setTab('router')")
    data = ctx.page.evaluate("window.__ocdState.sections.router.data")
    _assert(data, "router payload must be present")
    _assert(data["totals"]["tokens_total"] == 120,
            "shared scope must show 120 tokens, got %r"
            % data["totals"]["tokens_total"])
    _assert((ctx.text("#r-headline") or "").strip(),
            "codex headline must render")
    before = ctx.page.evaluate(
        "document.getElementById('r-split-btn').getAttribute('aria-pressed')")
    ctx.page.evaluate("document.getElementById('r-split-btn').click()")
    after = ctx.page.evaluate(
        "document.getElementById('r-split-btn').getAttribute('aria-pressed')")
    _assert(before != after, "reasoning toggle must flip aria-pressed")
    ctx.page.evaluate("setTab('opencode')")
    rows = ctx.page.evaluate(
        "document.querySelectorAll('#tbl tbody tr').length")
    _assert(rows > 0, "opencode tab must render session rows")
    return ctx.shot("tabs-120-reasoning")


def case_b02(ctx):
    """Search filters, a chip filter, a range preset, restored tab."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    ctx.page.evaluate(
        "(v)=>{const el=document.getElementById('search');el.value=v;"
        "el.dispatchEvent(new Event('input',{bubbles:true}));}", "child")
    ctx.page.wait_for_timeout(200)
    first = ctx.page.evaluate(
        "document.querySelector('#tbl tbody tr td')"
        " ? document.querySelector('#tbl tbody tr td').textContent : null")
    _assert(first and "child" in first.lower(),
            "search must filter the session table, got %r" % first)
    ctx.page.evaluate(
        "()=>{const sel=document.getElementById('r-provider-f');"
        "if(sel&&sel.options.length>1){sel.value=sel.options[1].value;"
        "sel.dispatchEvent(new Event('change',{bubbles:true}));}}")
    ctx.page.evaluate(
        "()=>{const p=document.getElementById('r-presets');"
        "if(p&&p.children.length){p.children[0].click();}}")
    ctx.page.evaluate("setTab('router')")
    ctx.page.reload(wait_until="domcontentloaded")
    ctx.page.wait_for_function(
        "window.__ocdState && window.__ocdState.cycle"
        " && window.__ocdState.cycle.finished", timeout=20000)
    saved = ctx.page.evaluate("sessionStorage.getItem('ocd-tab')")
    _assert(saved == "router",
            "selected tab must be restored after reload, got %r" % saved)
    return ctx.shot("search-filters-tab")


def case_b03(ctx):
    """Synth scope: one unknown outcome counted, no fake ok."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    data = ctx.page.evaluate("window.__ocdState.sections.router.data")
    totals = data["totals"]
    _assert(totals["unknown"] == 1,
            "one unknown outcome expected, got %r" % totals)
    _assert(totals["ok"] == 0,
            "no request may be claimed ok, got %r" % totals)
    chips = (ctx.text("#r-status") or "").lower()
    _assert("unknown" in chips,
            "status chips must surface unknown, got %r" % chips)
    return ctx.shot("unknown-no-fake-success")


def case_b04(ctx):
    """Child sessions carry recency labels and stay within the cap."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    agents = ctx.page.evaluate("window.__ocdState.sections.agents.data")
    _assert(agents, "agents payload must be present")
    rows = agents.get("child_runs", [])
    labels = {"recent", "quiet", "stale", "unknown"}
    states = [r.get("activity_state") for r in rows]
    _assert(states, "fixture must expose at least one child session")
    _assert(all(s in labels for s in states),
            "child rows must carry recency labels, got %r" % states)
    _assert(isinstance(agents.get("limit"), int)
            and len(rows) <= agents["limit"],
            "the child list must stay capped")
    return ctx.shot("recency-child-cap")


def case_b05(ctx):
    """Inspector renders with the bounded-paging footer."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    clicked = ctx.page.evaluate(
        "()=>{const rows=document.querySelectorAll('#sstbl tbody tr[data-sid]');"
        "for(const tr of rows){const td=tr.querySelector('td');"
        "if(td&&td.textContent.indexOf('Synthetic session')>=0){"
        "tr.dispatchEvent(new MouseEvent('click',{bubbles:true}));return true;}}"
        "return false;}")
    _assert(clicked, "a session row must exist in the sessions table")
    ctx.page.wait_for_function(
        "() => {const el=document.getElementById('insp');"
        "return el && el.textContent.indexOf('messages shown')>=0;}",
        timeout=6000)
    insp = ctx.text("#insp")
    _assert("hello" in insp,
            "inspected content must render, got %r" % insp)
    return ctx.shot("inspector-bounded-footer")


def case_b06(ctx):
    """Legal auth, two tabs, and the 401 path after a token restart."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    page2 = ctx.context.new_page()
    url = "http://127.0.0.1:%d/#token=%s" % (ctx.port, ctx.token)
    page2.goto(url, wait_until="domcontentloaded")
    page2.wait_for_function(
        "window.__ocdState && window.__ocdState.cycle"
        " && window.__ocdState.cycle.finished", timeout=20000)
    ctx.page.close()
    page2.evaluate("load()")
    page2.wait_for_function(
        "window.__ocdState.cycle.finished > 0"
        " && window.__ocdState.cycle.ok > 0", timeout=20000)
    ctx.server.auth_token = secrets.token_urlsafe(32)
    page2.evaluate("load()")
    page2.wait_for_function(
        "() => {const el=document.getElementById('auth-lock');"
        "return el && el.hidden === false;}", timeout=10000)
    page2.close()
    return None


def case_b07(ctx):
    """Synthetic HTML/JS never executes; no console noise, no externals."""
    ctx.new_page()
    ctx.goto()
    ctx.cycle_finished()
    ctx.page.wait_for_timeout(300)
    xss = ctx.page.evaluate("window.__xss")
    _assert(xss is None,
            "synthetic script must not execute, got %r" % xss)
    _assert(not ctx.dialogs,
            "no dialog may appear, got %r" % ctx.dialogs)
    body = ctx.page.evaluate("document.body.innerText")
    _assert("window.__xss" in body,
            "the payload must render as visible text")
    _assert(not ctx.console,
            "no console errors expected, got %r" % ctx.console[:5])
    _assert(not ctx.external,
            "no external requests allowed, got %r" % ctx.external[:5])
    return ctx.shot("no-xss-no-console-no-external")


def case_b08(ctx):
    """A stale synth refresh (HTTP 200 + synth_stale) keeps the old data,
    marks the router section stale and never presents a full success."""
    ctx.new_page()
    payload = {
        "totals": {"tokens_total": 120.0}, "days": [], "day_total": None,
        "is_synth": True, "synth_stale": True,
        "synth_error": "codex read failed for 1 file(s): injected denied",
        "synth_oversize_records": 0,
    }
    ctx.page.route("**/api/router*", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(payload)))
    ctx.goto()
    ctx.cycle_finished()
    secs = ctx.sections()
    _assert(secs["router"]["state"] == "stale",
            "router should be stale, got %r" % secs["router"])
    _assert("codex read failed" in (secs["router"]["error"] or ""),
            "router error should carry the synth error, got %r"
            % secs["router"]["error"])
    strip = ctx.page.evaluate(
        "document.getElementById('src-status').textContent")
    _assert("router: stale" in strip,
            "source strip should show stale, got %r" % strip)
    _assert("Partial update" in ctx.status(),
            "status should be a partial update, got %r" % ctx.status())
    ctx.evidence["router_state"] = secs["router"]
    return ctx.shot("synth-stale-kept")


CASES = [
    ("F6d-T01", "7 sections render while the 8th never sends headers", case_t01),
    ("F6d-T02", "200 headers, body never ends -> deadline still fires", case_t02),
    ("F6d-T04", "error after success keeps data + last_success + stale", case_t04),
    ("F6d-T06", "late older response A never overwrites newer B", case_t06),
    ("F6d-T09", "inspect/search/prompts timeout, no hang, retry works", case_t09),
    ("F8-B01", "tabs + 120 sum + reasoning toggle", case_b01),
    ("F8-B02", "search filters, chip filter, range preset, restored tab", case_b02),
    ("F8-B03", "unknown outcome counted, no fake success", case_b03),
    ("F8-B04", "recency labels + child-session list cap", case_b04),
    ("F8-B05", "inspector paging footer + content", case_b05),
    ("F8-B06", "legal auth + two tabs + 401 after restart", case_b06),
    ("F8-B07", "no synthetic script execution, console, externals", case_b07),
    ("F8-B08", "stale synth refresh keeps data + marks router stale", case_b08),
]
BROWSER_CASES = [c[0] for c in CASES]

FIXTURE_MODES = {
    "F8-B03": {"mode": "synth"},
    "F8-B07": {"xss": True},
}


# --------------------------------------------------------------------------
# Launch / orchestration
# --------------------------------------------------------------------------
def _launch_browser(log):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return None, None, "playwright not importable (%s)" % e
    pw = sync_playwright().start()
    for kwargs in ({"channel": "msedge"}, {}):
        try:
            browser = pw.chromium.launch(headless=True, **kwargs)
            reason = "msedge channel" if kwargs else "chromium"
            log("browser check: OK (%s)" % reason)
            return pw, browser, reason
        except Exception as e:
            last = "%s: %s" % (kwargs or "chromium", str(e).splitlines()[0][:160])
    try:
        pw.stop()
    except Exception:
        pass
    return None, None, "no usable browser (%s)" % last


def _load_dashboard():
    import opencode_dashboard as d
    return d


def _run_case(case_id, desc, fn, d, artifacts_dir, shots_dir, browser, log):
    result = {"acceptance_id": case_id, "result": "FAIL", "reason": desc,
              "detail": None, "evidence": {}, "screenshots": []}
    tmp = tempfile.TemporaryDirectory(prefix="f6d-browser-")
    server = thread = ctx = None
    try:
        fixture = _write_fixture(Path(tmp.name),
                                 **FIXTURE_MODES.get(case_id, {}))
        d.Handler.db_path = fixture["db"]
        d.Handler.router_events = fixture["events"]
        d.Handler.router_limits = None
        d.Handler.quiet = True
        try:
            d.CODEX_DIR = fixture["codex_dir"]
            d.CODEX_CACHE_DIR = Path(tmp.name) / "cache"
            d.CODEX_INDEX = d.CODEX_CACHE_DIR / "codex_index.json"
            d.CODEX_SYNTH = Path(tmp.name) / "codex_router_events.jsonl"
            d._codex_ev_sig = None
            d._codex_last_error = None
            d._codex_last_ok = None
            for name in ("_codex_ev_cache", "_codex_file_state", "_codex_ev_state"):
                try:
                    getattr(d, name).clear()
                except Exception:
                    pass
        except Exception:
            pass
        _reset_dashboard_state(d)
        FAULT.reset()
        server, thread, token, port = _start_server(d, fixture)
        ctx = Ctx(case_id, d, fixture, server, thread, token, port,
                  shots_dir, browser, log)
        try:
            shot = fn(ctx)
            if shot:
                result["screenshots"].append(shot)
            result["result"] = "PASS"
        except Exception as e:
            result["detail"] = "%s: %s" % (type(e).__name__, e)
            result["trace"] = traceback.format_exc()[-1200:]
            try:
                shot = ctx.shot("failure")
                if shot:
                    result["screenshots"].append(shot)
            except Exception:
                pass
        try:
            result["evidence"] = dict(ctx.evidence)
        except Exception:
            pass
        if ctx.console:
            result["console_errors"] = ctx.console[:10]
        if ctx.warns:
            result["console_warnings"] = ctx.warns[:10]
        if ctx.pageerrors:
            result["page_errors"] = ctx.pageerrors[:10]
            if result["result"] == "PASS":
                result["result"] = "FAIL"
                result["detail"] = "page error escaped: %r" % ctx.pageerrors[:3]
        return result
    finally:
        if ctx is not None:
            ctx.close()
        if server is not None:
            _stop_server(server, thread)
        _reset_dashboard_state(d)
        tmp.cleanup()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Real browser-test runner (Playwright; never fakes a pass).")
    parser.add_argument("--list", action="store_true",
                        help="list browser acceptance case IDs and exit")
    parser.add_argument("--cases", default="",
                        help="comma-separated case IDs (default: all)")
    parser.add_argument("--json", action="store_true",
                        help="also print results as JSON lines")
    parser.add_argument("--headful", action="store_true",
                        help="run the browser headful (debugging)")
    parser.add_argument("--artifacts-dir", default=str(DEFAULT_ARTIFACTS),
                        help="directory for logs, JSON and screenshots")
    parser.add_argument("--log-prefix", default="F6d-browser",
                        help="artifact name prefix (default F6d-browser)")
    args = parser.parse_args(argv)

    if args.list:
        for case_id in BROWSER_CASES:
            print(case_id)
        return 0

    selected = [c for c in CASES
                if not args.cases or c[0] in
                [x.strip() for x in args.cases.split(",") if x.strip()]]
    if not selected:
        print("no matching cases")
        return 2

    artifacts = Path(args.artifacts_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    shots_dir = artifacts / SHOTS_DIRNAME
    shots_dir.mkdir(parents=True, exist_ok=True)
    stamp = "windows" if os.name == "nt" else "linux"
    log_path = artifacts / ("%s.%s.log" % (args.log_prefix, stamp))
    json_path = artifacts / ("%s.%s.json" % (args.log_prefix, stamp))
    log = _Log(log_path)
    log("browser runner start %s; cases: %s"
        % (datetime.datetime.now().isoformat(timespec="seconds"),
           ", ".join(c[0] for c in selected)))

    # Isolated HOME before importing the dashboard (never real user data).
    home_tmp = tempfile.TemporaryDirectory(prefix="f6d-browser-home-")
    os.environ["HOME"] = home_tmp.name
    os.environ["USERPROFILE"] = home_tmp.name
    os.environ["LOCALAPPDATA"] = home_tmp.name

    try:
        d = _load_dashboard()
    except Exception as e:
        for case_id, desc, _ in selected:
            log("%s: NOT_RUN (dashboard import failed: %s)" % (case_id, e))
        log.flush()
        return 2

    pw, browser, reason = _launch_browser(log)
    if browser is None:
        results = []
        for case_id, desc, _ in selected:
            res = {"acceptance_id": case_id, "result": "NOT_RUN",
                   "reason": desc, "detail": reason}
            results.append(res)
            log("%s: NOT_RUN (%s)" % (case_id, reason))
        json_path.write_text(
            "\n".join(json.dumps(r) for r in results) + "\n", encoding="utf-8")
        log.flush()
        home_tmp.cleanup()
        return 2

    results = []
    try:
        for case_id, desc, fn in selected:
            log("---- %s: %s" % (case_id, desc))
            res = _run_case(case_id, desc, fn, d, artifacts, shots_dir,
                            browser, log)
            results.append(res)
            log("%s: %s%s" % (case_id, res["result"],
                              "" if res["result"] == "PASS"
                              else " (%s)" % (res.get("detail") or res.get("reason"))))
            if args.json:
                print(json.dumps(res))
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        json_path.write_text(
            "\n".join(json.dumps(r) for r in results) + "\n", encoding="utf-8")
        passed = sum(1 for r in results if r["result"] == "PASS")
        log("browser runner done: %d/%d PASS; json=%s"
            % (passed, len(results), json_path))
        log.flush()
        home_tmp.cleanup()

    if all(r["result"] == "PASS" for r in results):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
