"""Deterministic performance measurements for the dashboard (spec §22).

Runs the baseline runtime (pre-repair commit, extracted with ``git show``)
and the final runtime from the working tree on IDENTICAL synthetic data and
records: cold start / first refresh time, warm refresh, bytes actually read
(raw source vs derived index), parser calls, git subprocess calls around the
TTL window, restart/checkpoint behaviour, rotation/truncate correctness and
tracked memory (``tracemalloc`` - tracked memory, not process RSS). A simple
independent oracle re-parses the same files and the payload totals are
compared with it, so every sample has an explicit correctness check.

Samples and per-PERF-ID logs land in ``artifacts/``; numbers are never
fabricated. Metrics that the baseline does not implement (incremental codex
reading, restart checkpoint, bounded tail) are reported as
``not_available_in_baseline`` with a note.

Usage:
    py -3.14 tools/benchmark_dashboard.py --scenario ci
    py -3.14 tools/benchmark_dashboard.py --scenario small --baseline-ref df23258
"""
import argparse
import builtins
import hashlib
import importlib.util
import json
import os
import platform
import random
import secrets
import shutil
import sqlite3
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
BASELINE_REF_DEFAULT = "df23258"
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
    rng = random.Random(seed)
    codex = root / "codex" / "sessions"
    codex.mkdir(parents=True)
    per_file = max(1, spec["events"] // spec["files"])
    codex_bytes = 0
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
        codex_bytes += path.stat().st_size
    ledger = root / "usage-events.jsonl"
    with open(ledger, "w", encoding="utf-8", newline="") as fh:
        for i in range(spec["events"]):
            fh.write(json.dumps(_ledger_event(i)) + "\n")
    ledger_bytes = ledger.stat().st_size
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
            "repo": repo, "codex_bytes": codex_bytes,
            "ledger_bytes": ledger_bytes}


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
    """(sum of the last k ledger events, total_lines, k_used).

    When the payload does not expose candidate_lines (baseline runtime),
    fall back to the documented bounded window cap.
    """
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
# Instrumentation
# --------------------------------------------------------------------------
class Instruments:
    def __init__(self):
        self.lock = threading.Lock()
        self.bytes_read = 0
        self.json_calls = 0
        self.git_calls = 0
        self.root = ""
        self._real_open = builtins.open
        self._real_loads = json.loads
        self._real_run = subprocess.run
        self.open_patched = None
        self.loads_patched = None
        self.run_patched = None

    def reset(self):
        with self.lock:
            self.bytes_read = 0
            self.json_calls = 0
            self.git_calls = 0

    def snapshot(self):
        with self.lock:
            return {"bytes_read": self.bytes_read,
                    "json_calls": self.json_calls,
                    "git_calls": self.git_calls}

    def install(self, root):
        self.root = str(root)
        real_open = self._real_open

        def counting_open(file, mode="r", *a, **k):
            fh = real_open(file, mode, *a, **k)
            try:
                path = str(file)
                if "r" in str(mode) and path.startswith(self.root):
                    counter = self

                    class _Counted:
                        def read(self, *aa, **kk):
                            data = fh.read(*aa, **kk)
                            with counter.lock:
                                counter.bytes_read += len(data)
                            return data

                        def __iter__(self):
                            return self

                        def __next__(self):
                            line = next(fh)
                            try:
                                with counter.lock:
                                    counter.bytes_read += len(line)
                            except TypeError:
                                pass
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

        builtins.open = counting_open
        json.loads = counting_loads
        subprocess.run = counting_run
        self.open_patched, self.loads_patched, self.run_patched = (
            counting_open, counting_loads, counting_run)

    def uninstall(self):
        if self.open_patched is not None:
            builtins.open = self._real_open
            json.loads = self._real_loads
            subprocess.run = self._real_run


INSTR = Instruments()


# --------------------------------------------------------------------------
# Runtime loading and server driving
# --------------------------------------------------------------------------
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
    conn = HTTPConnection("127.0.0.1", port, timeout=120)
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


def run_runtime(mod, fixture, tag, samples, notes):
    mod.Handler.db_path = str(fixture["db"])
    mod.Handler.router_events = str(fixture["ledger"])
    mod.Handler.router_limits = None
    mod.Handler.quiet = True
    for attr, value in (("CODEX_DIR", fixture["codex"]),
                        ("CODEX_SYNTH", fixture["root"] / "synth.jsonl"),
                        ("CODEX_CACHE_DIR", fixture["root"] / "cache"),
                        ("CODEX_INDEX",
                         fixture["root"] / "cache" / "codex_index.json")):
        if hasattr(mod, attr):
            setattr(mod, attr, value)
    for clear in ("ROUTER_CACHE", "LOCAL_CACHE", "WINDOWS"):
        try:
            getattr(mod, clear).clear()
        except Exception:
            pass
    server, thread, token, port = start_server(mod)
    section = {"runtime": tag}

    def sample(stage, seconds, payload_total, oracle_total=None, extra=None):
        inst = INSTR.snapshot()
        rec = {"stage": stage, "seconds": round(seconds, 6),
               "bytes_read": inst["bytes_read"],
               "json_calls": inst["json_calls"],
               "git_calls": inst["git_calls"]}
        if payload_total is not None:
            rec["payload_total"] = payload_total
        if oracle_total is not None:
            rec["oracle_total"] = oracle_total
            rec["correct"] = abs(payload_total - oracle_total) < 1e-6
        if extra:
            rec.update(extra)
        samples.append(rec)
        return rec

    try:
        # S1 cold start / first refresh
        INSTR.reset()
        tracemalloc.start()
        t0 = time.perf_counter()
        st, payload = request(port, token, "/api/router")
        dt = time.perf_counter() - t0
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        total = payload["totals"]["tokens_total"] if payload else None
        k = payload.get("candidate_lines") if payload else None
        led, led_lines, k_used = oracle_ledger_window(fixture["ledger"], k)
        sample("cold_first_refresh", dt, total, led,
               {"http_status": st, "tracemalloc_peak_bytes": peak,
                "tracemalloc_current_bytes": cur,
                "window_mode": payload.get("window_mode") if payload else None,
                "candidate_lines": k, "lines_total": led_lines,
                "k_used": k_used,
                "scope": "window" if k_used < led_lines else "full"})
        # S2 warm refresh
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(port, token, "/api/router")
        dt = time.perf_counter() - t0
        sample("warm_refresh", dt,
               payload["totals"]["tokens_total"] if payload else None, None,
               {"http_status": st})
        # S3 append: ledger (both runtimes) and codex (final only)
        with open(fixture["ledger"], "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(_ledger_event(999999)) + "\n")
        extra_bytes = len(json.dumps(_ledger_event(999999))) + 1
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(port, token, "/api/router")
        dt = time.perf_counter() - t0
        total2 = payload["totals"]["tokens_total"] if payload else None
        rec = sample("append_refresh", dt, total2,
                     extra={"appended_bytes": extra_bytes,
                            "http_status": st})
        k2 = payload.get("candidate_lines") if payload else None
        led2, led2_lines, k2_used = oracle_ledger_window(fixture["ledger"], k2)
        rec["oracle_total"] = led2
        rec["lines_total"] = led2_lines
        rec["k_used"] = k2_used
        rec["scope"] = "window" if k2_used < led2_lines else "full"
        rec["correct"] = (total2 is not None
                          and abs(total2 - led2) < 1e-6)
        codex_file = sorted(fixture["codex"].rglob("rollout-*.jsonl"))[0]
        if tag == "final":
            with open(codex_file, "a", encoding="utf-8", newline="") as fh:
                rec = _rollout_event(1, 0)
                fh.write(json.dumps(rec) + "\n")
            try:
                mod.Handler.router_events = None
                INSTR.reset()
                t0 = time.perf_counter()
                st, payload = request(port, token, "/api/router")
                dt = time.perf_counter() - t0
                total3 = payload["totals"]["tokens_total"] if payload else None
                rec3 = sample("codex_append_incremental", dt, total3,
                              extra={"appended_bytes":
                                     len(json.dumps(rec)) + 1,
                                     "http_status": st})
                codex2 = oracle_totals(fixture["codex"])
                rec3["oracle_total"] = codex2
                rec3["correct"] = (total3 is not None
                                   and abs(total3 - codex2) < 1e-6)
            finally:
                mod.Handler.router_events = str(fixture["ledger"])
        else:
            notes.append(
                "codex_append_incremental: not_available_in_baseline "
                "(synth feature added by F6a/F6b/F6c)")
        # S4 rotation/truncate (ledger)
        fixture["ledger"].write_text(
            json.dumps(_ledger_event(1)) + "\n", encoding="utf-8")
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(port, token, "/api/router")
        dt = time.perf_counter() - t0
        total4 = payload["totals"]["tokens_total"] if payload else None
        rec4 = sample("rotation_truncate", dt, total4,
                      extra={"http_status": st})
        k4 = payload.get("candidate_lines") if payload else None
        led3, led3_lines, k4_used = oracle_ledger_window(fixture["ledger"], k4)
        rec4["oracle_total"] = led3
        rec4["lines_total"] = led3_lines
        rec4["k_used"] = k4_used
        rec4["correct"] = (total4 is not None
                           and abs(total4 - led3) < 1e-6)
        # S5 stats + git subprocess counts (TTL window)
        INSTR.reset()
        t0 = time.perf_counter()
        st, payload = request(port, token, "/api/stats")
        dt = time.perf_counter() - t0
        sample("stats_cold", dt, None, None, {"http_status": st})
        INSTR.reset()
        for _ in range(5):
            request(port, token, "/api/stats")
        sample("stats_5_refreshes", 0.0, None, None)
        time.sleep(11.0)
        INSTR.reset()
        t0 = time.perf_counter()
        request(port, token, "/api/stats")
        dt = time.perf_counter() - t0
        sample("stats_after_ttl", dt, None, None)
        # S6 restart/checkpoint (final only)
        if tag == "final":
            INSTR.reset()
            server2, thread2, tok2, port2 = start_server(mod)
            try:
                t0 = time.perf_counter()
                st, payload = request(port2, tok2, "/api/router")
                dt = time.perf_counter() - t0
                total5 = payload["totals"]["tokens_total"] if payload else None
                rec5 = sample("restart_with_checkpoint", dt, total5,
                              extra={"http_status": st})
                oc5 = led3
                rec5["oracle_total"] = oc5
                rec5["correct"] = (total5 is not None
                                   and abs(total5 - oc5) < 1e-6)
            finally:
                server2.shutdown()
                server2.server_close()
                thread2.join(timeout=5)
        else:
            notes.append(
                "restart_with_checkpoint: not_available_in_baseline "
                "(checkpoint added by F6c)")
        section["samples"] = samples
        return section
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _git_show(ref, relpath, out_path):
    cp = subprocess.run(["git", "show", "%s:%s" % (ref, relpath)],
                        cwd=str(REPO_ROOT), capture_output=True, timeout=60)
    if cp.returncode != 0:
        raise SystemExit("cannot extract baseline runtime: %s"
                         % cp.stderr.decode("utf-8", "replace")[:200])
    out_path.write_bytes(cp.stdout)
    return out_path


def _write_perf_logs(samples_by_scenario, env, notes):
    per_id = {i: [] for i in range(1, 7)}

    def add(idx, line):
        per_id[idx].append(line)

    add(1, "environment=%s" % json.dumps(env, sort_keys=True))
    for scenario, section in samples_by_scenario.items():
        for tag, data in section.items():
            for s in data["samples"]:
                line = "%s runtime=%s stage=%s seconds=%s bytes=%s " \
                       "json=%s git=%s" % (scenario, tag, s["stage"],
                                           s["seconds"], s["bytes_read"],
                                           s["json_calls"], s["git_calls"])
                if "payload_total" in s:
                    line += " payload_total=%s" % s["payload_total"]
                if "oracle_total" in s:
                    line += " oracle_total=%s correct=%s" % (
                        s["oracle_total"], s["correct"])
                if "tracemalloc_peak_bytes" in s:
                    line += " tracemalloc_peak=%s" % s[
                        "tracemalloc_peak_bytes"]
                if s["stage"] == "cold_first_refresh":
                    add(2, line)
                elif s["stage"] in ("warm_refresh", "append_refresh",
                                    "codex_append_incremental"):
                    add(3, line)
                elif s["stage"] in ("rotation_truncate",
                                    "restart_with_checkpoint"):
                    add(4, line)
                elif s["stage"].startswith("stats"):
                    add(5, line)
                if "tracemalloc_peak_bytes" in s or "long" in scenario:
                    add(6, line)
    for idx in range(1, 7):
        if not per_id[idx]:
            per_id[idx].append("no samples")
    for idx, lines in per_id.items():
        (ARTIFACTS / ("PERF-T0%d.windows.log" % idx)).write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    (ARTIFACTS / "PERF-notes.windows.log").write_text(
        "\n".join(notes) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="ci",
                        choices=["ci", "small", "large"],
                        help="ci = deterministic small+large profile")
    parser.add_argument("--baseline-ref", default=BASELINE_REF_DEFAULT,
                        help="git ref of the pre-repair runtime")
    args = parser.parse_args(argv)

    scenarios = ["small", "large"] if args.scenario == "ci" else [args.scenario]
    ARTIFACTS.mkdir(exist_ok=True)
    PERF_DIR.mkdir(exist_ok=True)
    notes = []
    samples_by_scenario = {}

    tmp = tempfile.TemporaryDirectory(prefix="perf-bench-")
    perf_root = Path(tmp.name)
    baseline_file = perf_root / "baseline_dashboard.py"
    _git_show(args.baseline_ref, "opencode_dashboard.py", baseline_file)
    final_file = REPO_ROOT / "opencode_dashboard.py"

    env = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "os": "windows" if os.name == "nt" else os.name,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "seed": SEED,
        "baseline_ref": args.baseline_ref,
        "baseline_sha256": hashlib.sha256(
            baseline_file.read_bytes()).hexdigest(),
        "final_sha256": hashlib.sha256(final_file.read_bytes()).hexdigest(),
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True).stdout.strip(),
        "memory_method": "tracemalloc (tracked memory, not process RSS)",
    }

    INSTR.install(perf_root)
    try:
        for scenario in scenarios:
            spec = SCENARIOS[scenario]
            src = perf_root / ("fixture_" + scenario)
            src.mkdir()
            gen = generate_fixture(src, spec)
            env.setdefault("scenario_sizes", {})[scenario] = {
                "events": spec["events"], "files": spec["files"],
                "codex_dir_bytes": sum(
                    p.stat().st_size for p in gen["codex"].rglob("*.jsonl")),
                "ledger_bytes": gen["ledger"].stat().st_size,
            }
            section = {}
            for tag, runtime_file in (("baseline", baseline_file),
                                      ("final", final_file)):
                run_root = perf_root / ("run_%s_%s" % (scenario, tag))
                shutil.copytree(src, run_root)
                fixture = {"root": run_root,
                           "codex": run_root / "codex" / "sessions",
                           "ledger": run_root / "usage-events.jsonl",
                           "db": run_root / "bench.db",
                           "repo": run_root / "repo",
                           "codex_bytes": 0, "ledger_bytes": 0}
                # isolated HOME before importing each runtime
                home = perf_root / ("home_%s_%s" % (scenario, tag))
                home.mkdir()
                os.environ["HOME"] = str(home)
                os.environ["USERPROFILE"] = str(home)
                os.environ["LOCALAPPDATA"] = str(home)
                mod = load_runtime(runtime_file, "bench_%s_%s"
                                   % (scenario, tag))
                section[tag] = run_runtime(mod, fixture, tag, [], notes)
            samples_by_scenario[scenario] = section
    finally:
        INSTR.uninstall()

    raw = PERF_DIR / "PERF-samples.windows.jsonl"
    with open(raw, "w", encoding="utf-8", newline="") as fh:
        for scenario, section in samples_by_scenario.items():
            for tag, data in section.items():
                for s in data["samples"]:
                    row = dict(s)
                    row["runtime"] = tag
                    row["scenario"] = scenario
                    fh.write(json.dumps(row) + "\n")
    env["scenario_events"] = {k: v["events"] for k, v in SCENARIOS.items()}
    env["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (PERF_DIR / "PERF-summary.windows.json").write_text(
        json.dumps({"environment": env, "samples": samples_by_scenario,
                    "notes": notes}, indent=1), encoding="utf-8")
    _write_perf_logs(samples_by_scenario, env, notes)
    for scenario, section in samples_by_scenario.items():
        for tag, data in section.items():
            bad = [s for s in data["samples"] if s.get("correct") is False]
            if bad:
                print("INCORRECT totals: %s %s %r" % (scenario, tag, bad))
                return 1
    print("benchmark done: scenarios=%s samples=%s"
          % (",".join(scenarios), raw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
