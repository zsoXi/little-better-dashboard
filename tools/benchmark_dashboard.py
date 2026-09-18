"""Deterministic performance measurements for the dashboard (spec §22 / W2).

Two measurement paths, kept apart on purpose:

  * router ledger path  - /api/router with --router-events pointing at a
    synthetic usage-events.jsonl (both the historical and the final runtime
    read this file);
  * codex synth path    - /api/router with no router ledger, so the built-in
    Codex synthesizer reads the rollout files (BOTH runtime versions contain
    a synthesizer; the historical one has no checkpoint and no incremental
    read, which the measurements expose).

Byte instrumentation classifies reads into raw source bytes (rollouts,
ledger), derived index bytes (codex_router_events.jsonl / synth files) and
checkpoint bytes (.cache/codex_index.json), so a "0 bytes" figure always has
an explicit scope. Restart is a REAL new process (child driver), not a dict
reset. Ordinary timing comparisons keep at least --repeats repetitions and
the summary reports observations, median and max (no p95 from 5 samples).
Memory is measured with tracemalloc (tracked Python allocations, not RSS)
for BOTH versions in the same cold scenarios.

Usage:
    py -3.14 tools/benchmark_dashboard.py --scenario ci
    py -3.14 tools/benchmark_dashboard.py --scenario small --baseline-file path/to/1a345a1_opencode_dashboard.py
"""
import argparse
import builtins
import hashlib
import importlib.util
import json
import os
import platform
import secrets
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO_ROOT / "artifacts"
PERF_DIR = ARTIFACTS / "performance"
# W1: main comparison baseline is the full 1a345a1 revision (it contains the
# Codex synthesizer: CODEX_SYNTH / _parse_codex_events / ensure_codex_synth).
# df23258 remains the historical comparison: its runtime differs only in
# typographic em-dash strings (202830 vs 202714 bytes, no logic change).
BASELINE_REF_DEFAULT = "1a345a10c2d8e8f16121e10e2206e57e76684ed0"
SEED = 20260917

SCENARIOS = {
    "small": {"events": 10000, "files": 3, "long_every": 500},
    "large": {"events": 100000, "files": 3, "long_every": 500},
}

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


# --------------------------------------------------------------------------
# Fixture generation (deterministic)
# --------------------------------------------------------------------------
def _rollout_event(i, long_every):
    payload = {"usage": {"input_tokens": 100, "cached_input_tokens": 40,
                         "output_tokens": 20, "total_tokens": 120,
                         "reasoning_output_tokens": 5,
                         "cache_write_input_tokens": 0}}
    if long_every and i % long_every == 0:
        payload["note"] = "x" * 4096
    return {"type": "token_usage_record",
            "timestamp": "2026-09-16T12:00:00", "payload": payload}


def _ledger_event(i):
    return {"at": "2026-09-16T12:00:00", "model": "bench-model-%d" % (i % 3),
            "provider": "codex", "status": 200,
            "inputTokens": 100, "outputTokens": 20,
            "cachedInputTokens": 40, "reasoningTokens": 5,
            "totalTokens": 120, "durationMs": 5}


def generate_fixture(root, spec, seed=SEED):
    codex = root / "codex" / "sessions"
    codex.mkdir(parents=True)
    per_file = max(1, spec["events"] // spec["files"])
    for f in range(spec["files"]):
        sub = codex / ("nested%d" % f)
        sub.mkdir()
        path = sub / ("rollout-%d.jsonl" % f)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps({"type": "session_meta",
                                 "timestamp": "2026-09-16T12:00:00",
                                 "payload": {"id": "bench-%d" % f,
                                             "cwd": "C:/bench",
                                             "model_provider": "codex"}}) + "\n")
            fh.write(json.dumps({"type": "turn_context",
                                 "timestamp": "2026-09-16T12:00:00",
                                 "payload": {"model": "muse-spark"}}) + "\n")
            for i in range(per_file):
                rec = _rollout_event(f * per_file + i, spec["long_every"])
                fh.write(json.dumps(rec) + "\n")
    ledger = root / "usage-events.jsonl"
    with open(ledger, "w", encoding="utf-8", newline="") as fh:
        for i in range(spec["events"]):
            fh.write(json.dumps(_ledger_event(i)) + "\n")
    db = root / "bench.db"
    con = sqlite3.connect(str(db))
    con.executescript(FIXTURE_SCHEMA)
    repo = root / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = "B"
    env["GIT_AUTHOR_EMAIL"] = "b@example.invalid"
    env["GIT_COMMITTER_NAME"] = "B"
    env["GIT_COMMITTER_EMAIL"] = "b@example.invalid"
    subprocess.run(["git", "init", "-q"], cwd=str(repo), env=env,
                   capture_output=True, timeout=60)
    (repo / "a.txt").write_text("bench\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), env=env,
                   capture_output=True, timeout=60)
    subprocess.run(["git", "commit", "-q", "-m", "bench"], cwd=str(repo),
                   env=env, capture_output=True, timeout=60)
    con.execute(
        "INSERT INTO session VALUES ('sid-b','Bench session','build',"
        "'muse-spark','C:/bench',NULL,1800000000000,1800000100000,"
        "100,20,5,40,0,0.0,'proj-b')")
    con.execute(
        "INSERT INTO message VALUES ('m1','sid-b',?,1800000001000)",
        (json.dumps({"role": "user"}),))
    con.execute(
        "INSERT INTO part VALUES ('p1','m1','sid-b',?,1800000002000)",
        (json.dumps({"type": "text", "text": "hello"}),))
    con.execute(
        "INSERT INTO project VALUES ('proj-b','C:/bench',?,NULL,'git','bench')",
        (str(repo),))
    con.commit()
    con.close()
    return {"root": root, "codex": codex, "ledger": ledger, "db": db,
            "repo": repo}


def fixture_counts(fixture, spec):
    rollout_files = sorted(fixture["codex"].rglob("rollout-*.jsonl"))
    lines = 0
    usage = 0
    meta = 0
    for p in rollout_files:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                lines += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("type") == "token_usage_record":
                    usage += 1
                else:
                    meta += 1
    ledger_lines = 0
    with open(fixture["ledger"], "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                ledger_lines += 1
    return {
        "events": spec["events"],
        "rollout_files": len(rollout_files),
        "rollout_lines": lines,
        "rollout_usage_records": usage,
        "rollout_metadata_records": meta,
        "rollout_bytes": sum(p.stat().st_size for p in rollout_files),
        "ledger_lines": ledger_lines,
        "ledger_bytes": fixture["ledger"].stat().st_size,
        "days": 1,
        "ledger_models": 3,
    }


def oracle_totals(path):
    total = 0.0
    p = Path(path)
    paths = sorted(p.rglob("rollout-*.jsonl")) if p.is_dir() else [p]
    for fp in paths:
        with open(fp, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                if rec.get("type") == "token_usage_record":
                    usage = (rec.get("payload") or {}).get("usage") or {}
                    total += float(usage.get("total_tokens") or 0)
                elif "totalTokens" in rec:
                    total += float(rec.get("totalTokens") or 0)
    return total


def oracle_ledger_window(path, k, cap=30000):
    """(sum of the last k ledger events, total_lines, k_used)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    total_lines = len(lines)
    if not k or int(k) <= 0:
        k = min(total_lines, cap)
    k = int(k)
    total = 0.0
    for line in lines[-k:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and "totalTokens" in rec:
            total += float(rec.get("totalTokens") or 0)
    return total, total_lines, k


# --------------------------------------------------------------------------
# Instrumentation (read bytes classified by scope)
# --------------------------------------------------------------------------
class Instruments:
    def __init__(self):
        self.lock = threading.Lock()
        self.kinds = {"raw": 0, "derived": 0, "checkpoint": 0, "other": 0}
        self.json_calls = 0
        self.git_calls = 0
        self.root = ""
        self.ledger = ""
        self.synth = ""
        self.cache = ""
        self._real_open = builtins.open
        self._real_loads = json.loads
        self._real_run = subprocess.run
        self._real_read_bytes = Path.read_bytes
        self._real_read_text = Path.read_text
        self._real_read_bytes = Path.read_bytes
        self._real_read_text = Path.read_text
        self.installed = False

    def reset(self):
        with self.lock:
            self.kinds = {"raw": 0, "derived": 0, "checkpoint": 0, "other": 0}
            self.json_calls = 0
            self.git_calls = 0

    def snapshot(self):
        with self.lock:
            kinds = dict(self.kinds)
            return {"bytes_raw": kinds["raw"],
                    "bytes_derived": kinds["derived"],
                    "bytes_checkpoint": kinds["checkpoint"],
                    "bytes_other": kinds["other"],
                    "bytes_total": sum(kinds.values()),
                    "json_calls": self.json_calls,
                    "git_calls": self.git_calls}

    def _kind(self, path):
        p = str(path)
        if p == self.ledger:
            return "raw"
        if p.startswith(self.cache):
            return "checkpoint"
        if p == self.synth or "synth" in Path(p).name:
            return "derived"
        if p.startswith(self.root):
            return "raw" if (Path(self.root) / "codex") in Path(p).parents \
                else "other"
        return None

    def install(self, root, ledger, synth, cache):
        if self.installed:
            self.uninstall()
        self.root = str(root)
        self.ledger = str(ledger)
        self.synth = str(synth)
        self.cache = str(cache)
        real_open = self._real_open
        counter = self

        def counting_open(file, mode="r", *a, **k):
            fh = real_open(file, mode, *a, **k)
            try:
                if "r" in str(mode):
                    kind = counter._kind(file)
                    if kind is not None:
                        class _Counted:
                            def read(self, *aa, **kk):
                                data = fh.read(*aa, **kk)
                                with counter.lock:
                                    counter.kinds[kind] += len(data)
                                return data

                            def __iter__(self):
                                return self

                            def __next__(self):
                                line = next(fh)
                                with counter.lock:
                                    counter.kinds[kind] += len(line)
                                return line

                            def __getattr__(self, name):
                                return getattr(fh, name)

                            def __enter__(self):
                                fh.__enter__()
                                return self

                            def __exit__(self, *exc):
                                return fh.__exit__(*exc)

                        return _Counted()
            except Exception:
                pass
            return fh

        def counting_loads(*a, **k):
            with self.lock:
                self.json_calls += 1
            return self._real_loads(*a, **k)

        def counting_run(*a, **k):
            cmd = a[0] if a else k.get("args")
            try:
                head = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else ""
            except Exception:
                head = ""
            if "git" in str(head):
                with self.lock:
                    self.git_calls += 1
            return self._real_run(*a, **k)

        def counting_read_bytes(p):
            data = counter._real_read_bytes(p)
            kind = counter._kind(p)
            if kind is not None:
                with counter.lock:
                    counter.kinds[kind] += len(data)
            return data

        def counting_read_text(p, *a, **k):
            txt = counter._real_read_text(p, *a, **k)
            kind = counter._kind(p)
            if kind is not None:
                with counter.lock:
                    counter.kinds[kind] += len(txt)
            return txt

        Path.read_bytes = counting_read_bytes
        Path.read_text = counting_read_text
        builtins.open = counting_open
        json.loads = counting_loads
        subprocess.run = counting_run
        self.installed = True

    def uninstall(self):
        if self.installed:
            builtins.open = self._real_open
            json.loads = self._real_loads
            subprocess.run = self._real_run
            Path.read_bytes = self._real_read_bytes
            Path.read_text = self._real_read_text
            self.installed = False


INSTR = Instruments()

_RESTART_DRIVER = r'''
import builtins, hashlib, importlib.util, json, os, secrets, statistics, sys, threading, time
import tracemalloc
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

runtime_file, run_root, home = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
os.environ["HOME"] = home
os.environ["USERPROFILE"] = home
os.environ["LOCALAPPDATA"] = home

counts = {"raw": 0, "derived": 0, "checkpoint": 0, "other": 0}
codex_dir = run_root / "codex" / "sessions"
synth = run_root / "synth.jsonl"
cache = run_root / "cache"
real_open = builtins.open

def kind_of(p):
    p = str(p)
    if p.startswith(str(cache)):
        return "checkpoint"
    if p == str(synth) or "synth" in Path(p).name:
        return "derived"
    if p.startswith(str(run_root)):
        return "raw" if (run_root / "codex") in Path(p).parents else "other"
    return None

def counting_open(file, mode="r", *a, **k):
    fh = real_open(file, mode, *a, **k)
    try:
        if "r" in str(mode):
            kind = kind_of(file)
            if kind is not None:
                class C:
                    def read(self, *aa, **kk):
                        d = fh.read(*aa, **kk); counts[kind] += len(d); return d
                    def __iter__(self):
                        return self
                    def __next__(self):
                        line = next(fh); counts[kind] += len(line); return line
                    def __getattr__(self, n):
                        return getattr(fh, n)
                    def __enter__(self):
                        fh.__enter__(); return self
                    def __exit__(self, *e):
                        return fh.__exit__(*e)
                return C()
    except Exception:
        pass
    return fh

builtins.open = counting_open

real_read_bytes = Path.read_bytes
real_read_text = Path.read_text

def counting_read_bytes(p):
    data = real_read_bytes(p)
    k = kind_of(p)
    if k is not None:
        counts[k] += len(data)
    return data

def counting_read_text(p, *a, **kk):
    txt = real_read_text(p, *a, **kk)
    k = kind_of(p)
    if k is not None:
        counts[k] += len(txt)
    return txt

Path.read_bytes = counting_read_bytes
Path.read_text = counting_read_text

spec = importlib.util.spec_from_file_location("restart_bench", runtime_file)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.Handler.db_path = str(run_root / "bench.db")
mod.Handler.router_events = None
mod.Handler.router_limits = None
mod.Handler.quiet = True
for attr, value in (("CODEX_DIR", codex_dir), ("CODEX_SYNTH", synth),
                    ("CODEX_CACHE_DIR", cache),
                    ("CODEX_INDEX", cache / "codex_index.json")):
    try:
        setattr(mod, attr, value)
    except Exception:
        pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
srv.auth_token = secrets.token_urlsafe(32)
try:
    mod.Handler.auth_token = srv.auth_token
except Exception:
    pass
threading.Thread(target=srv.serve_forever, daemon=True).start()
host, port = srv.server_address

total = 0.0
for f in sorted(codex_dir.rglob("rollout-*.jsonl")):
    with real_open(f, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("type") == "token_usage_record":
                u = (rec.get("payload") or {}).get("usage") or {}
                total += float(u.get("total_tokens") or 0)

tracemalloc.start()
t0 = time.perf_counter()
conn = HTTPConnection("127.0.0.1", port, timeout=300)
conn.request("GET", "/api/router", headers={"Authorization": "Bearer " + srv.auth_token, "X-Window-Id": "bench"})
resp = conn.getresponse()
body = resp.read()
conn.close()
dt = time.perf_counter() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
srv.shutdown()
srv.server_close()

try:
    payload = json.loads(body.decode("utf-8"))
    got = float(payload["totals"]["tokens_total"])
except Exception:
    got = None
print(json.dumps({
    "seconds": dt,
    "bytes_raw": counts["raw"],
    "bytes_derived": counts["derived"],
    "bytes_checkpoint": counts["checkpoint"],
    "bytes_other": counts["other"],
    "tracemalloc_peak_bytes": peak,
    "payload_total": got,
    "oracle_total": total,
    "correct": got is not None and abs(got - total) < 1e-6 and got > 0,
    "http_status": resp.status,
    "synth_present": synth.is_file(),
    "checkpoint_present": (cache / "codex_index.json").is_file(),
}))
'''


def load_runtime(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def start_server(mod):
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    token = secrets.token_urlsafe(32)
    try:
        server.auth_token = token
    except Exception:
        pass
    try:
        mod.Handler.auth_token = token
    except Exception:
        pass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    assert host == "127.0.0.1"
    return server, thread, token, port


def request(port, token, path):
    conn = HTTPConnection("127.0.0.1", port, timeout=300)
    headers = {"Authorization": "Bearer " + token, "X-Window-Id": "bench"}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        obj = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        obj = None
    return resp.status, obj


def _median(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def _max(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return max(vals) if vals else None


class RuntimeRun:
    """One runtime version against one isolated fixture copy."""

    def __init__(self, runtime_file, tag, scenario, run_root, cache_tag):
        self.runtime_file = runtime_file
        self.tag = tag
        self.scenario = scenario
        self.run_root = run_root
        self.cache_tag = cache_tag
        self.module_seq = 0
        self.server = None
        self.thread = None
        self.token = None
        self.port = None

    # -- lifecycle -----------------------------------------------------
    def stop(self):
        if self.server is not None:
            try:
                self.server.shutdown()
            except Exception:
                pass
            try:
                self.server.server_close()
            except Exception:
                pass
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def clean_derived(self):
        for p in self.run_root.glob("synth*.jsonl"):
            try:
                p.unlink()
            except OSError:
                pass
        shutil.rmtree(self.run_root / "cache", ignore_errors=True)

    def fresh(self, router_events):
        self.stop()
        self.module_seq += 1
        name = "bench_%s_%s_%s_%d" % (self.scenario, self.tag,
                                     self.cache_tag, self.module_seq)
        mod = load_runtime(self.runtime_file, name)
        mod.Handler.db_path = str(self.run_root / "bench.db")
        mod.Handler.router_events = (str(self.run_root / "usage-events.jsonl")
                                     if router_events else None)
        mod.Handler.router_limits = None
        mod.Handler.quiet = True
        for attr, value in (("CODEX_DIR",
                             self.run_root / "codex" / "sessions"),
                            ("CODEX_SYNTH", self.run_root / "synth.jsonl"),
                            ("CODEX_CACHE_DIR", self.run_root / "cache"),
                            ("CODEX_INDEX",
                             self.run_root / "cache" / "codex_index.json")):
            try:
                setattr(mod, attr, value)
            except Exception:
                pass
        for clear in ("ROUTER_CACHE", "LOCAL_CACHE", "WINDOWS"):
            try:
                getattr(mod, clear).clear()
            except Exception:
                pass
        self.server, self.thread, self.token, self.port = start_server(mod)
        return mod

    # -- observations --------------------------------------------------
    def obs_ledger_cold(self, memory=False):
        self.clean_derived()
        self.fresh(router_events=True)
        INSTR.reset()
        if memory:
            tracemalloc.start()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        peak = None
        if memory:
            cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        k = payload.get("candidate_lines") if payload else None
        led, led_lines, k_used = oracle_ledger_window(
            self.run_root / "usage-events.jsonl", k)
        rec.update({"stage": "ledger_cold", "seconds": dt,
                    "payload_total": total, "oracle_total": led,
                    "correct": total is not None and abs(total - led) < 1e-6,
                    "k_used": k_used, "lines_total": led_lines,
                    "scope": "window" if k_used < led_lines else "full",
                    "traced": bool(memory),
                    "http_status": st})
        if peak is not None:
            rec["tracemalloc_peak_bytes"] = peak
        return rec

    def obs_ledger_warm(self):
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        k = payload.get("candidate_lines") if payload else None
        led, led_lines, k_used = oracle_ledger_window(
            self.run_root / "usage-events.jsonl", k)
        rec.update({"stage": "ledger_warm", "seconds": dt,
                    "payload_total": total, "oracle_total": led,
                    "correct": total is not None and abs(total - led) < 1e-6,
                    "k_used": k_used, "traced": False,
                    "http_status": st})
        return rec

    def obs_ledger_append(self):
        with open(self.run_root / "usage-events.jsonl", "a",
                  encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(_ledger_event(999999)) + "\n")
        rec = INSTR.snapshot()
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        rec2 = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        k = payload.get("candidate_lines") if payload else None
        led, led_lines, k_used = oracle_ledger_window(
            self.run_root / "usage-events.jsonl", k)
        rec2.update({"stage": "ledger_append", "seconds": dt,
                     "payload_total": total, "oracle_total": led,
                     "correct": total is not None and abs(total - led) < 1e-6,
                     "k_used": k_used, "traced": False, "http_status": st})
        return rec2

    def obs_ledger_rotate(self):
        (self.run_root / "usage-events.jsonl").write_text(
            json.dumps(_ledger_event(1)) + "\n", encoding="utf-8")
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        k = payload.get("candidate_lines") if payload else None
        led, led_lines, k_used = oracle_ledger_window(
            self.run_root / "usage-events.jsonl", k)
        rec.update({"stage": "ledger_rotate", "seconds": dt,
                    "payload_total": total, "oracle_total": led,
                    "correct": total is not None and abs(total - led) < 1e-6,
                    "traced": False, "http_status": st})
        return rec

    def obs_stats_cold(self):
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/stats")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        rec.update({"stage": "stats_cold", "seconds": dt,
                    "correct": st == 200 and isinstance(payload, dict),
                    "check": "availability (HTTP 200 + payload present); "
                             "no independent oracle for this stage",
                    "traced": False,
                    "http_status": st})
        return rec

    def obs_stats_burst(self, n=5):
        INSTR.reset()
        oks = []
        for _ in range(n):
            st, payload = request(self.port, self.token, "/api/stats")
            oks.append(st == 200 and isinstance(payload, dict))
        rec = INSTR.snapshot()
        rec.update({"stage": "stats_%d_refreshes" % n, "seconds": 0.0,
                    "correct": all(oks),
                    "check": "availability across %d refreshes; "
                             "no independent oracle for this stage" % n,
                    "traced": False})
        return rec

    def obs_stats_after_ttl(self, wait=11.0):
        time.sleep(wait)
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/stats")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        rec.update({"stage": "stats_after_ttl", "seconds": dt,
                    "correct": st == 200 and isinstance(payload, dict),
                    "check": "availability after the cache TTL; "
                             "no independent oracle for this stage",
                    "traced": False,
                    "http_status": st})
        return rec

    def obs_synth_cold(self, memory=False):
        self.clean_derived()
        self.fresh(router_events=False)
        INSTR.reset()
        if memory:
            tracemalloc.start()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        peak = None
        if memory:
            cur, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        oracle = oracle_totals(self.run_root / "codex" / "sessions")
        rec.update({"stage": "synth_cold", "seconds": dt,
                    "payload_total": total, "oracle_total": oracle,
                    "correct": total is not None and abs(total - oracle) < 1e-6,
                    "http_status": st,
                    "payload_error": (payload.get("error")
                                      if isinstance(payload, dict) else None),
                    "is_synth": (payload.get("is_synth")
                                 if isinstance(payload, dict) else None),
                    "traced": bool(memory),
                    "synth_present": (self.run_root / "synth.jsonl").is_file(),
                    "checkpoint_present":
                        (self.run_root / "cache" / "codex_index.json").is_file()})
        if peak is not None:
            rec["tracemalloc_peak_bytes"] = peak
        return rec

    def obs_synth_warm(self):
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        oracle = oracle_totals(self.run_root / "codex" / "sessions")
        rec.update({"stage": "synth_warm", "seconds": dt,
                    "payload_total": total, "oracle_total": oracle,
                    "correct": total is not None and abs(total - oracle) < 1e-6,
                    "traced": False,
                    "http_status": st,
                    "payload_error": (payload.get("error")
                                      if isinstance(payload, dict) else None)})
        return rec

    def obs_synth_append(self):
        target = sorted((self.run_root / "codex" / "sessions")
                        .rglob("rollout-*.jsonl"))[0]
        with open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(_rollout_event(1, 0)) + "\n")
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(self.port, self.token, "/api/router")
        dt = time.perf_counter() - t0
        rec = INSTR.snapshot()
        total = payload["totals"]["tokens_total"] if payload else None
        oracle = oracle_totals(self.run_root / "codex" / "sessions")
        rec.update({"stage": "synth_append", "seconds": dt,
                    "payload_total": total, "oracle_total": oracle,
                    "correct": total is not None and abs(total - oracle) < 1e-6,
                    "http_status": st,
                    "payload_error": (payload.get("error")
                                      if isinstance(payload, dict) else None),
                    "traced": False,
                    "synth_present": (self.run_root / "synth.jsonl").is_file(),
                    "checkpoint_present":
                        (self.run_root / "cache" / "codex_index.json").is_file()})
        return rec

    def obs_synth_restart(self, driver, home):
        cp = subprocess.run([sys.executable, driver, self.runtime_file,
                             str(self.run_root), str(home)],
                            capture_output=True, text=True, timeout=600,
                            encoding="utf-8", errors="replace")
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        if cp.returncode != 0 or not out:
            return {"stage": "synth_restart", "correct": False,
                    "seconds": None,
                    "detail": "rc=%s stderr=%s stdout=%s"
                              % (cp.returncode, err[-300:], out[-300:])}
        try:
            rec = json.loads(out.splitlines()[-1])
        except ValueError:
            return {"stage": "synth_restart", "correct": False,
                    "seconds": None,
                    "detail": "unparsable child output: %s" % out[-300:]}
        rec["stage"] = "synth_restart"
        rec.setdefault("traced", False)
        if not rec.get("correct"):
            rec["detail"] = ("child: http=%s payload=%s oracle=%s synth=%s "
                             "checkpoint=%s" % (
                                 rec.get("http_status"),
                                 rec.get("payload_total"),
                                 rec.get("oracle_total"),
                                 rec.get("synth_present"),
                                 rec.get("checkpoint_present")))
        return rec


def run_version(runtime_file, tag, scenario, spec, run_root, driver, home,
                repeats, samples, notes):
    run = RuntimeRun(runtime_file, tag, scenario, run_root, tag)

    def push(rec, repeat=None):
        rec["scenario"] = scenario
        rec["runtime"] = tag
        if repeat is not None:
            rec["repeat"] = repeat
        samples.append(rec)
        return rec

    try:
        for i in range(repeats):
            push(run.obs_ledger_cold(memory=(i == 0)), i + 1)
        INSTR.reset()
        request(run.port, run.token, "/api/stats")  # settle caches
        for i in range(repeats):
            push(run.obs_ledger_warm(), i + 1)
        for i in range(repeats):
            push(run.obs_ledger_append(), i + 1)
        push(run.obs_ledger_rotate())
        push(run.obs_stats_cold())
        push(run.obs_stats_burst())
        push(run.obs_stats_after_ttl())
        for i in range(repeats):
            push(run.obs_synth_cold(memory=(i == 0)), i + 1)
        for i in range(repeats):
            push(run.obs_synth_warm(), i + 1)
        for i in range(repeats):
            push(run.obs_synth_append(), i + 1)
        run.stop()
        for i in range(repeats):
            push(run.obs_synth_restart(driver, home), i + 1)
        return run
    finally:
        run.stop()


def _git_show(ref, relpath, out_path):
    cp = subprocess.run(["git", "show", "%s:%s" % (ref, relpath)],
                        cwd=str(REPO_ROOT), capture_output=True, timeout=60)
    if cp.returncode != 0:
        raise SystemExit("cannot extract baseline runtime: %s"
                         % cp.stderr.decode("utf-8", "replace")[:200])
    out_path.write_bytes(cp.stdout)
    return out_path


def _write_perf_logs(summary, env, notes, out_dir):
    def block(stage):
        lines = []
        for scenario, per in summary.items():
            for tag in ("baseline", "final"):
                rows = per.get(tag, {}).get(stage)
                if not rows:
                    continue
                med = _median(rows, "seconds")
                mx = _max(rows, "seconds")
                r0 = rows[0]
                line = ("%s %s n=%d seconds_median=%s seconds_max=%s "
                        "bytes_raw=%s bytes_derived=%s bytes_checkpoint=%s "
                        "json=%s git=%s" % (
                            scenario, tag, len(rows), med, mx,
                            r0.get("bytes_raw"), r0.get("bytes_derived"),
                            r0.get("bytes_checkpoint"), r0.get("json_calls"),
                            r0.get("git_calls")))
                if r0.get("payload_total") is not None:
                    line += " payload=%s oracle=%s correct=%s" % (
                        r0.get("payload_total"), r0.get("oracle_total"),
                        r0.get("correct"))
                if r0.get("tracemalloc_peak_bytes") is not None:
                    line += " tracemalloc_peak_bytes=%s" % (
                        r0["tracemalloc_peak_bytes"])
                if r0.get("scope"):
                    line += " scope=%s" % r0["scope"]
                lines.append(line)
        return lines

    t01 = ["environment=%s" % json.dumps(env, sort_keys=True)]
    t02 = block("ledger_cold") + block("synth_cold")
    t03 = block("ledger_warm") + block("ledger_append") + \
        block("synth_warm") + block("synth_append")
    t04 = block("ledger_rotate") + block("synth_restart")
    t05 = block("stats_cold") + block("stats_5_refreshes") + \
        block("stats_after_ttl")
    t06 = block("ledger_cold") + block("synth_cold")
    for idx, lines in ((1, t01), (2, t02), (3, t03), (4, t04), (5, t05),
                       (6, t06)):
        (out_dir / ("PERF-T0%d.windows.log" % idx)).write_text(
            "\n".join(lines or ["no samples"]) + "\n", encoding="utf-8")
    (out_dir / "PERF-notes.windows.log").write_text(
        "\n".join(notes) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="ci",
                        choices=["ci", "small", "large"],
                        help="ci = deterministic small+large profile")
    parser.add_argument("--baseline-ref", default=BASELINE_REF_DEFAULT,
                        help="git ref of the historical runtime")
    parser.add_argument("--baseline-file", default="",
                        help="use this runtime file instead of git show "
                             "(needed when running from the review package)")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args(argv)

    scenarios = ["small", "large"] if args.scenario == "ci" else [args.scenario]
    ARTIFACTS.mkdir(exist_ok=True)
    PERF_DIR.mkdir(exist_ok=True)
    notes = []
    samples = []
    summary = {}

    tmp = tempfile.TemporaryDirectory(prefix="perf-bench-")
    perf_root = Path(tmp.name)
    baseline_file = perf_root / "baseline_dashboard.py"
    if args.baseline_file:
        shutil.copyfile(args.baseline_file, baseline_file)
        baseline_source = args.baseline_file
    else:
        _git_show(args.baseline_ref, "opencode_dashboard.py", baseline_file)
        baseline_source = "git show %s:opencode_dashboard.py" % args.baseline_ref
    final_file = REPO_ROOT / "opencode_dashboard.py"
    driver = perf_root / "restart_driver.py"
    driver.write_text(_RESTART_DRIVER, encoding="utf-8")

    env = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "os": "windows" if os.name == "nt" else os.name,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "seed": SEED,
        "repeats": args.repeats,
        "baseline_source": baseline_source,
        "baseline_sha256": hashlib.sha256(
            baseline_file.read_bytes()).hexdigest(),
        "final_sha256": hashlib.sha256(final_file.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(
            Path(__file__).read_bytes()).hexdigest(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True).stdout.strip(),
        "memory_method": "tracemalloc tracked Python allocations (not RSS)",
        "instrumentation": "builtins.open read counting classified into "
                           "raw source / derived index / checkpoint bytes",
    }

    INSTR.install(perf_root, perf_root / "unused-ledger.jsonl",
                  perf_root / "unused-synth.jsonl", perf_root / "unused-cache")
    try:
        for scenario in scenarios:
            spec = SCENARIOS[scenario]
            src = perf_root / ("fixture_" + scenario)
            src.mkdir()
            generate_fixture(src, spec)
            env.setdefault("scenario_sizes", {})[scenario] = fixture_counts(
                {"root": src, "codex": src / "codex" / "sessions",
                 "ledger": src / "usage-events.jsonl"}, spec)
            summary[scenario] = {}
            for tag, runtime_file in (("baseline", baseline_file),
                                      ("final", final_file)):
                run_root = perf_root / ("run_%s_%s" % (scenario, tag))
                shutil.copytree(src, run_root)
                home = perf_root / ("home_%s_%s" % (scenario, tag))
                home.mkdir()
                os.environ["HOME"] = str(home)
                os.environ["USERPROFILE"] = str(home)
                os.environ["LOCALAPPDATA"] = str(home)
                INSTR.install(run_root, run_root / "usage-events.jsonl",
                              run_root / "synth.jsonl", run_root / "cache")
                run_version(runtime_file, tag, scenario, spec, run_root,
                            driver, home, args.repeats, samples, notes)
                per = {}
                for s in samples:
                    if s.get("scenario") != scenario or s.get("runtime") != tag:
                        continue
                    per.setdefault(s["stage"], []).append(s)
                summary[scenario][tag] = per
    finally:
        INSTR.uninstall()

    raw = PERF_DIR / "PERF-samples.windows.jsonl"
    with open(raw, "w", encoding="utf-8", newline="") as fh:
        for scenario in scenarios:
            for tag in ("baseline", "final"):
                for s in samples:
                    if s.get("scenario") != scenario or s.get("runtime") != tag:
                        continue
                    fh.write(json.dumps(s) + "\n")
    env["finished_at"] = datetime.now().isoformat(timespec="seconds")
    correct_true = sum(1 for s in samples if s.get("correct") is True)
    correct_false = sum(1 for s in samples if s.get("correct") is False)
    correct_none = sum(1 for s in samples if "correct" not in s)
    notes = list(notes) + [
        "correct coverage: %d true, %d false, %d without a correctness "
        "check (stats stages check availability only; they have no "
        "independent oracle)" % (correct_true, correct_false, correct_none),
        "only the first cold sample of each runtime is measured with "
        "tracemalloc (traced=true); every other sample carries traced=false "
        "and no peak, so peak comparisons are per-runtime, not per-time",
    ]
    (PERF_DIR / "PERF-summary.windows.json").write_text(
        json.dumps({"environment": env, "summary": summary, "notes": notes,
                    "correct_counts": {"true": correct_true,
                                       "false": correct_false,
                                       "none": correct_none}},
                   indent=1), encoding="utf-8")
    _write_perf_logs(summary, env, notes, ARTIFACTS)
    bad = [s for s in samples if s.get("correct") is False]
    if bad:
        print("INCORRECT totals/shape: %d sample(s)" % len(bad))
        for s in bad[:5]:
            print(" ", s.get("scenario"), s.get("runtime"), s.get("stage"),
                  s.get("detail") or s.get("error") or "")
        return 1
    print("benchmark done: scenarios=%s repeats=%s samples=%d raw=%s"
          % (",".join(scenarios), args.repeats, len(samples), raw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
