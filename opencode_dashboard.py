import argparse
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import pathname2url

_HOME = Path.home()  # cross-platform: %USERPROFILE% on Windows, $HOME elsewhere
DB_PATH = _HOME / ".local/share/opencode/opencode.db"
ROUTER_EVENTS_DEFAULT = _HOME / ".codex/codex-router/usage-events.jsonl"
ROUTER_LIMITS_DEFAULT = _HOME / ".codex/codex-router/rate-limits.json"

# All cross-thread shared state is guarded by LOCK.
LOCK = threading.Lock()
LOCAL_CACHE = {"key": None, "stats": None}
ROUTER_CACHE = {"key": None, "stats": None}
MAX_ROUTER_EVENTS = 30000  # most-recent lines scanned from usage-events.jsonl
MAX_ROUTER_ROWS = 400  # recent requests kept in the /api/router payload
MAX_ROUTER_RECORD_BYTES = 8 * 1024 * 1024  # F6b: per-record read cap
_ROUTER_READ_BLOCK = 65536  # F6b: byte block size for tail reads/counts
_ROUTER_COUNT_CACHE = {}  # F6b: str(path) -> line count + fingerprint
SYNTH_SCHEMA = 3  # bump to force codex-synth rebuild when the writer changes

# What-if paid pricing per 1M tokens (input, output) for known *-free models.
# Actual free cost is always $0; this estimates what the same tokens would cost.
WHAT_IF_PRICING = {
    "muse-spark-1.3-contributor-free": (0.10, 0.20),
    "muse-spark-1.2-contributor-free": (0.10, 0.20),
    "muse-spark-1.2-contributor": (0.10, 0.20),
}
WINDOWS = {}  # window id -> last-seen (unix seconds); heartbeat registry
LAST_REQUEST = 0.0
HAD_WINDOW = False
CLOSE_TIMER = None
MAX_SESSION_ROWS = 500
ACTIVITY_DAYS = 364  # 52 weeks of daily cells, one column per day
COMMIT_WINDOW_HOURS = 24  # sessions in this window before a commit count toward it
MAX_COMMITS_PER_REPO = 200  # recent commits scanned per worktree
MAX_FILE_ROWS = 15  # top files/subsystems kept in the payload (+ implicit Other)
MAX_COMMIT_ROWS = 60  # commit rows kept in the payload, by tokens desc

# F2 access protection: per-instance bearer token + strict Host/Origin.
# The token is generated at startup (secrets.token_urlsafe(32)), kept only
# on the server instance, and never sent in query strings or logs.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,128}")


def _new_token():
    return secrets.token_urlsafe(32)


def _expected_host(server):
    try:
        port = server.server_address[1]
    except Exception:
        return ""
    return "127.0.0.1:%s" % port


def _expected_origin(server):
    try:
        port = server.server_address[1]
    except Exception:
        return ""
    return "http://127.0.0.1:%s" % port


def _header_all(handler, name):
    try:
        get_all = handler.headers.get_all
    except AttributeError:
        v = handler.headers.get(name)
        return [] if v is None else [v]
    try:
        vals = get_all(name)
    except Exception:
        v = handler.headers.get(name)
        return [] if v is None else [v]
    if vals is None:
        return []
    return list(vals)


def _is_private_path(path):
    return path == "/api/close" or path.startswith("/api/")


def connect(db_path):
    if not Path(db_path).exists():
        raise RuntimeError(f"Database not found: {db_path}")
    uri = f"file:{pathname2url(str(Path(db_path).resolve()))}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def json_html(obj):
    """Serialize for safe embedding inside <script>const S0 = ...;</script>.

    json.dumps leaves '<' unescaped, so a DB-sourced title containing
    '</script><script>...' could break out of the script tag. Escaping '<'
    (and the JS string terminators U+2028/U+2029) makes the payload inert.
    """
    out = json.dumps(obj, ensure_ascii=True).replace("<", "\\u003c")
    return out.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def zero_days_window():
    """Canonical 52-week daily window ending today (local time), zero-filled.

    Keys are 'YYYY-MM-DD' strings in local time, matching how per-day usage
    is bucketed elsewhere. Returns a list of day dicts.
    """
    today = datetime.now().date()
    start = today - timedelta(days=ACTIVITY_DAYS - 1)
    out = []
    d = start
    while d <= today:
        out.append(
            {
                "date": d.isoformat(),
                "msgs": 0,
                "sessions": 0,
                "ti": 0,
                "to": 0,
                "tr": 0,
                "cache": 0,
                "cost": 0.0,
            }
        )
        d += timedelta(days=1)
    return out


def pad_activity(days):
    """Overlay recorded per-day usage onto the canonical 52-week window.

    All numeric keys are carried over (not just the OpenCode set), so the
    Codex day breakdown, reqs/ok/err/err429/what_if, survives padding and
    stays consistent between the per-day table, the heatmap and the
    period rollups. Totals are recomputed by each caller with the correct
    token semantics (local includes the separate cache stream, Codex input
    already contains cached input).
    """
    window = zero_days_window()
    for slot in window:
        slot.setdefault("reqs", 0)
        slot.setdefault("ok", 0)
        slot.setdefault("err", 0)
    by_date = {d["date"]: d for d in days}
    for slot in window:
        entry = by_date.get(slot["date"])
        if not entry:
            continue
        for key, value in entry.items():
            if key == "date" or key == "total":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            slot[key] = slot.get(key, 0) + value
    return window


def day_total(d):
    """Total tokens for a day/slot dict (input+output+reasoning+cache)."""
    return (d.get("ti", 0) or 0) + (d.get("to", 0) or 0) + (d.get("tr", 0) or 0) + (d.get("cache", 0) or 0)


def cache_rate(ti, cache):
    """OpenCode cache share, where input excludes the separate cache stream."""
    denom = (ti or 0) + (cache or 0)
    return round((cache or 0) / denom * 100, 1) if denom else 0.0


def router_cache_rate(input_total, cached_input):
    """Codex cache share, where cached input is a subset of total input."""
    return round(min(cached_input or 0, input_total or 0) / (input_total or 1) * 100, 1) if input_total else 0.0


def _router_day_tokens(d):
    """Router day total: ti + to only.

    Cached input is a subset of input and reasoning is a subset of output,
    so neither is ever additive here. (Local OpenCode day_total is a
    different contract and is intentionally untouched.)
    """
    return (d.get("ti", 0) or 0) + (d.get("to", 0) or 0)


def compute_streaks(activity, tokens_only=False):
    """Longest + current active-day streaks from padded activity window.

    An empty today must not zero the current streak: when the last slot
    (today) is inactive, counting starts back from yesterday. With
    tokens_only=True (Codex tab) error-only / unmetered-zero days with
    requests but no tokens do not extend a streak.
    """
    def active(slot):
        if tokens_only:
            return _router_day_tokens(slot) > 0
        return (slot.get("msgs", 0) or 0) > 0 or day_total(slot) > 0

    slots = list(activity or [])
    if slots and not active(slots[-1]):
        slots = slots[:-1]  # today quiet so far, streak stands through yesterday
    cur = 0
    for slot in reversed(slots):
        if active(slot):
            cur += 1
        else:
            break
    best = run = 0
    for slot in activity or []:
        if active(slot):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return {"current": cur, "longest": best}


def parse_model(raw):
    if not raw:
        return "-"
    try:
        return json.loads(raw).get("id", raw)
    except (ValueError, AttributeError):
        return str(raw)


def parse_provider(raw):
    if not raw:
        return ""
    try:
        return json.loads(raw).get("providerID", "") or ""
    except (ValueError, AttributeError):
        return ""


def model_key(raw):
    prov = parse_provider(raw)
    model = parse_model(raw)
    return prov + "/" + model if prov else model


def short_dir(path):
    if not path:
        return "-"
    parts = [p for p in path.split("/") if p]
    if len(parts) > 2:
        return "/".join(parts[-2:])
    return path


def fmt_dur_min(minutes):
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{round(minutes)} min"
    return f"{round(minutes // 60)}h {round(minutes % 60)}m"


def _day(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else None


def subsystem_for_file(filepath, worktrees):
    """Map a touched file to a subsystem: first path segment under its worktree.

    The longest matching worktree wins, so sessions touching files across
    repos still bucket correctly. Files outside every known worktree (scratch
    dirs, /tmp) fall back to their parent folder name instead of vanishing.
    """
    fp = filepath or ""
    best = ""
    for wt in worktrees or []:
        w = (wt or "").rstrip("/")
        if w and (fp == w or fp.startswith(w + "/")) and len(w) > len(best):
            best = w
    if best:
        rel = fp[len(best):].lstrip("/")
        if not rel:
            return "(root)"
        return rel.split("/", 1)[0] or "(root)"
    parent = fp.rsplit("/", 1)[0] if "/" in fp else ""
    return (parent.rsplit("/", 1)[-1] if parent else "") or "(root)"


def db_worktrees(db_path):
    """[(display name, worktree)] from the project table; [] when unreadable."""
    try:
        con = connect(db_path)
        try:
            return [
                (r["name"] or short_dir(r["worktree"]), r["worktree"])
                for r in con.execute("SELECT name, worktree FROM project")
            ]
        finally:
            con.close()
    except Exception:
        return []


# F6f: per-worktree TTL caches for the expensive git calls. SQLite stats
# freshness stays cheap; HEAD/commit reads are shared between parallel
# requests and refreshed at most once per TTL window. Failures keep the last
# known value with a stale state; a fresh failure without history reads as
# unavailable. Args are always a list; nothing is shell-interpreted.
GIT_TTL_SECONDS = 10.0
_GIT_LOCK = threading.Lock()
_HEADS_CACHE = {}
_COMMITS_CACHE = {}


def _head_for(wt, now, force=False):
    with _GIT_LOCK:
        entry = _HEADS_CACHE.get(wt)
        if entry is not None and not force and (now - entry["at"]) < GIT_TTL_SECONDS:
            return entry["head"]
    head = None
    state = "unavailable"
    try:
        if Path(wt).is_dir():
            out = subprocess.run(
                ["git", "-C", wt, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                head = out.stdout.strip()
                state = "ok"
    except (OSError, ValueError, subprocess.SubprocessError):
        state = "unavailable"
    with _GIT_LOCK:
        prev = _HEADS_CACHE.get(wt)
        if state != "ok" and prev is not None and prev.get("head"):
            _HEADS_CACHE[wt] = {"head": prev["head"], "at": now,
                                "state": "stale"}
            return prev["head"]
        _HEADS_CACHE[wt] = {"head": head, "at": now, "state": state}
        return head


def repo_heads(worktrees, force=False):
    """Current HEAD per worktree, served from the per-worktree TTL cache."""
    heads = []
    seen = set()
    now = time.time()
    for _, wt in worktrees or []:
        if not wt or wt in seen:
            continue
        seen.add(wt)
        heads.append((wt, _head_for(wt, now, force)))
    return tuple(heads)


def _parse_repo_log(stdout, name):
    commits = []
    cur = None
    for line in stdout.splitlines():
        if "\x1f" in line:
            if cur:
                commits.append(cur)
            parts = line.split("\x1f")
            try:
                ts = int(parts[2])
            except (ValueError, IndexError):
                cur = None
                continue
            cur = {
                "sha": parts[0],
                "subject": parts[1] if len(parts) > 1 else "",
                "time": ts * 1000,
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
                "project": name,
                "files": 0,
                "add": 0,
                "del": 0,
            }
        elif cur and line.strip():
            bits = line.split()
            if len(bits) >= 3:
                cur["files"] += 1
                try:
                    cur["add"] += int(bits[0])
                except ValueError:
                    pass
                try:
                    cur["del"] += int(bits[1])
                except ValueError:
                    pass
    if cur:
        commits.append(cur)
    return commits


def _commits_for(name, wt, per_repo, now, force=False):
    with _GIT_LOCK:
        entry = _COMMITS_CACHE.get(wt)
        if entry is not None and not force and (now - entry["at"]) < GIT_TTL_SECONDS:
            return entry["commits"]
    parsed = []
    state = "unavailable"
    try:
        if Path(wt).is_dir():
            out = subprocess.run(
                ["git", "-C", wt, "log", f"-n{per_repo}",
                 "--format=%H\x1f%s\x1f%ct", "--numstat"],
                capture_output=True, text=True, timeout=15,
            )
            if out.returncode == 0:
                state = "ok"
                parsed = _parse_repo_log(out.stdout, name)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    with _GIT_LOCK:
        prev = _COMMITS_CACHE.get(wt)
        if state != "ok" and prev is not None and prev.get("commits"):
            _COMMITS_CACHE[wt] = {"commits": prev["commits"], "at": now,
                                  "state": "stale"}
            return prev["commits"]
        _COMMITS_CACHE[wt] = {"commits": parsed, "at": now, "state": state}
        return parsed


def collect_repo_commits(worktrees, per_repo=MAX_COMMITS_PER_REPO):
    """Recent commits per worktree, served from the per-worktree TTL cache.

    Skips missing dirs and non-git worktrees silently, the panels simply
    show fewer repos. Never raises for git failures.
    """
    commits = []
    seen = set()
    now = time.time()
    for name, wt in worktrees or []:
        if not wt or wt in seen:
            continue
        seen.add(wt)
        commits.extend(_commits_for(name, wt, per_repo, now))
    return commits


# Portable agent-definition lookup: project dirs (CWD at launch) + global config.
# Extra dirs via --agents-dir. Missing dirs are skipped silently.
AGENT_DIRS = [
    str(Path.cwd() / ".opencode" / "agent"),
    str(Path.cwd() / ".opencode" / "agents"),
    str(_HOME / ".config" / "opencode" / "agent"),
    str(_HOME / ".config" / "opencode" / "agents"),
]
BUILTIN_AGENTS = [
    {"name": "build", "mode": "primary", "model": "", "description": "Default coding agent", "source": "builtin"},
    {"name": "plan", "mode": "primary", "model": "", "description": "Planning mode agent", "source": "builtin"},
    {"name": "general", "mode": "subagent", "model": "", "description": "General-purpose agent", "source": "builtin"},
    {"name": "explore", "mode": "subagent", "model": "", "description": "Fast codebase exploration", "source": "builtin"},
]
_CFG_CACHE = {"mtime": 0, "defs": []}


def read_agent_defs():
    """Agent/subagent definitions: project .md frontmatter + builtins. Cached by dir mtime."""
    try:
        mtime = 0
        for d in AGENT_DIRS:
            try:
                mtime = max(mtime, os.path.getmtime(d))
                for f in os.listdir(d):
                    if f.endswith(".md"):
                        mtime = max(mtime, os.path.getmtime(os.path.join(d, f)))
            except OSError:
                pass
        if mtime and mtime == _CFG_CACHE["mtime"]:
            return _CFG_CACHE["defs"]
        defs = []
        for d in AGENT_DIRS:
            try:
                files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
            except OSError:
                continue
            for f in files:
                name = f[:-3]
                meta = {"description": "", "mode": "subagent", "model": ""}
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        lines = fh.read().splitlines()
                    if lines and lines[0].strip() == "---":
                        for ln in lines[1:]:
                            if ln.strip() in ("---", "..."):
                                break
                            if ":" in ln:
                                k, v = ln.split(":", 1)
                                k = k.strip()
                                if k in meta:
                                    meta[k] = v.strip()
                except OSError:
                    pass
                defs.append({"name": name, "mode": meta["mode"] or "subagent",
                             "model": meta["model"],
                             "description": meta["description"],
                             "source": "project"})
        defs = BUILTIN_AGENTS + sorted(defs, key=lambda x: x["name"])
        _CFG_CACHE["mtime"] = mtime
        _CFG_CACHE["defs"] = defs
        return defs
    except Exception:
        return []


def query_agents(con):
    """Child/related sessions (parent_id set) joined with parent title.
    activity_state comes from session updates only: recent (0-120 s),
    quiet (>120-900 s), stale (>900 s) or unknown (missing/invalid/future
    timestamp). It is a history signal, not execution status; runtime_status
    stays 'unknown' because this dashboard has no live agent API."""
    RECENT_SEC, QUIET_SEC, LIMIT = 120, 900, 100
    now_ms = int(time.time() * 1000)
    runs = []
    for r in con.execute(
        """
        SELECT s.id AS id, s.title AS title, s.agent AS agent, s.model AS model,
               s.directory AS directory, s.time_created AS time_created,
               s.time_updated AS time_updated, s.parent_id AS parent_id,
               p.title AS parent_title,
               (COALESCE(s.tokens_input,0)+COALESCE(s.tokens_output,0)
                +COALESCE(s.tokens_reasoning,0)+COALESCE(s.tokens_cache_read,0)
                +COALESCE(s.tokens_cache_write,0)) AS toks,
               (SELECT COUNT(*) FROM message m WHERE m.session_id=s.id) AS msgs
        FROM session s LEFT JOIN session p ON p.id=s.parent_id
        WHERE s.parent_id IS NOT NULL AND s.parent_id != ''
        ORDER BY s.time_updated DESC, s.id DESC LIMIT 100
        """
    ):
        upd = r["time_updated"]
        if not (isinstance(upd, int) and not isinstance(upd, bool)) or upd <= 0 or upd > now_ms:
            age, state, upd_ms = None, "unknown", 0
        else:
            age = (now_ms - upd) // 1000
            state = ("recent" if age <= RECENT_SEC
                     else ("quiet" if age <= QUIET_SEC else "stale"))
            upd_ms = upd
        runs.append({
            "id": r["id"], "title": r["title"] or "(untitled)",
            "agent": r["agent"] or "default", "model": parse_model(r["model"]),
            "provider": parse_provider(r["model"]),
            "directory": r["directory"] or "",
            "time_created": r["time_created"] or 0,
            "time_updated": upd_ms,
            "parent_id": r["parent_id"],
            "parent_title": r["parent_title"] or "(unknown parent)",
            "toks": r["toks"] or 0, "msgs": r["msgs"] or 0,
            "age_s": age, "activity_state": state,
            "runtime_status": "unknown", "relation": "child_session",
        })
    activity_counts = {"recent": 0, "quiet": 0, "stale": 0, "unknown": 0}
    for r in runs:
        activity_counts[r["activity_state"]] += 1
    per_agent = defaultdict(lambda: {"n": 0, "toks": 0})
    for r in con.execute(
        "SELECT agent, COUNT(*) AS n FROM session WHERE parent_id IS NOT NULL AND parent_id != '' GROUP BY agent"
    ):
        a = r["agent"] or "default"
        per_agent[a]["n"] = r["n"]
    for r in con.execute(
        """SELECT agent, COALESCE(SUM(tokens_input)+SUM(tokens_output)+SUM(tokens_reasoning)
           +SUM(tokens_cache_read)+SUM(tokens_cache_write),0) AS t FROM session
           WHERE parent_id IS NOT NULL AND parent_id != '' GROUP BY agent"""
    ):
        per_agent[r["agent"] or "default"]["toks"] = r["t"] or 0
    tops = sum(1 for r in con.execute(
        "SELECT 1 FROM session WHERE (parent_id IS NULL OR parent_id='') LIMIT 1"))
    all_agents = []
    for r in con.execute(
        """SELECT agent, COUNT(*) AS n,
           COALESCE(SUM(tokens_input)+SUM(tokens_output)+SUM(tokens_reasoning)
           +SUM(tokens_cache_read)+SUM(tokens_cache_write),0) AS t
           FROM session GROUP BY agent ORDER BY t DESC"""
    ):
        all_agents.append({"agent": r["agent"] or "(none)", "n": r["n"], "toks": r["t"] or 0})
    total_child = sum(v["n"] for v in per_agent.values())
    return {"child_runs": runs,
            "child_agents": sorted(
                [{"agent": a, "n": v["n"], "toks": v["toks"]} for a, v in per_agent.items()],
                key=lambda x: -x["toks"]),
            "child_total": total_child,
            "listed_count": len(runs),
            "total_child_sessions": total_child,
            "limit": LIMIT,
            "truncated": total_child > LIMIT,
            "activity_counts": activity_counts,
            "recent_sec": RECENT_SEC, "quiet_sec": QUIET_SEC, "now_ms": now_ms,
            "all_agents": all_agents,
            "config": read_agent_defs()}


GRAPH_LIMIT = 200
SESS_LIMIT = 100
INSP_PREVIEW = 1500
INSP_PAGE_DEFAULT = 50
INSP_PAGE_MAX = 200
INSP_PARTS_PER_MSG = 20
INSP_MSG_JSON_CAP = 16384
INSP_PART_DATA_CAP = 8192
INSP_BUDGET_BYTES = 1024 * 1024

def _row_dict(r):
    return {k: r[k] for k in r.keys()}

def _sstr(v):
    if isinstance(v, str):
        return v
    if v is None:
        return ""
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)

def query_graph(con, limit=GRAPH_LIMIT):
    nodes = []
    for r in con.execute("SELECT id, title, agent, parent_id, time_updated FROM session ORDER BY time_updated DESC LIMIT ?", (limit,)):
        nodes.append({"id": r["id"], "title": r["title"] or "(untitled)", "agent": r["agent"] or "(none)", "parent_id": r["parent_id"], "updated": r["time_updated"]})
    ids = set(n["id"] for n in nodes)
    kids = {}
    for n in nodes:
        p = n["parent_id"]
        if p and p in ids:
            kids.setdefault(p, []).append(n["id"])
    roots = [n["id"] for n in nodes if not (n["parent_id"] and n["parent_id"] in ids)]
    return {"nodes": dict((n["id"], n) for n in nodes), "kids": kids, "roots": roots, "limit": limit}

def query_sessions(con, q="", agent="", model="", limit=SESS_LIMIT):
    where = []
    args = []
    if q:
        where.append("(title LIKE ? OR directory LIKE ? OR id LIKE ?)")
        args += ["%" + q + "%", "%" + q + "%", "%" + q + "%"]
    if agent:
        where.append("agent = ?")
        args.append(agent)
    if model:
        where.append("model LIKE ?")
        args.append("%" + model + "%")
    sql = "SELECT id, title, agent, model, directory, parent_id, time_created, time_updated FROM session"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY time_updated DESC LIMIT ?"
    args.append(limit)
    rows = []
    for r in con.execute(sql, args):
        d = _row_dict(r)
        d["provider"] = parse_provider(d.get("model"))
        d["model"] = parse_model(d.get("model"))
        if not d.get("title"):
            d["title"] = "(untitled)"
        rows.append(d)
    return {"rows": rows, "limit": limit}

def query_inspect(con, sid, cursor=None, limit=None):
    """Bounded inspector page (spec ch.16).

    One page of messages ordered by (time_created, id) with a cursor, a
    hard limit, parts fetched in ONE batched query (capped per message),
    raw JSON reads bounded by substr()/LENGTH(), and a whole-response
    UTF-8 budget. Invalid input returns a controlled dict; read-only.
    """
    try:
        lim = int(limit) if limit is not None else INSP_PAGE_DEFAULT
    except (TypeError, ValueError):
        lim = INSP_PAGE_DEFAULT
    if lim < 1:
        lim = INSP_PAGE_DEFAULT
    if lim > INSP_PAGE_MAX:
        lim = INSP_PAGE_MAX
    s = con.execute("SELECT id, title, agent, model, directory, parent_id, time_created, time_updated FROM session WHERE id = ?", (sid,)).fetchone()
    if not s:
        return {"found": False, "id": sid}
    after = None
    if cursor:
        cparts = str(cursor).split("|", 1)
        if len(cparts) != 2:
            return {"found": False, "id": sid, "error": "invalid cursor"}
        try:
            after = (int(cparts[0]), cparts[1])
        except (TypeError, ValueError):
            return {"found": False, "id": sid, "error": "invalid cursor"}
    page_sql = ("SELECT id, substr(data,1,?) AS djson, LENGTH(data) AS dlen, "
                "time_created FROM message WHERE session_id = ?")
    args = [INSP_MSG_JSON_CAP, sid]
    if after is not None:
        page_sql += " AND (time_created > ? OR (time_created = ? AND id > ?))"
        args += [after[0], after[0], after[1]]
    page_sql += " ORDER BY time_created, id LIMIT ?"
    args.append(lim + 1)
    rows = list(con.execute(page_sql, args))
    has_more = len(rows) > lim
    rows = rows[:lim]
    msg_ids = [r["id"] for r in rows]
    parts_by_msg = {}
    parts_total = {}
    if msg_ids:
        qmarks = ",".join("?" for _ in msg_ids)
        parts_sql = ("SELECT id, message_id, substr(data,1,?) AS pjson, "
                     "LENGTH(data) AS size FROM (SELECT id, message_id, data, "
                     "ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY id) AS rn "
                     "FROM part WHERE message_id IN (" + qmarks + ")) WHERE rn <= ? "
                     "ORDER BY message_id, id")
        pargs = [INSP_PART_DATA_CAP] + msg_ids + [INSP_PARTS_PER_MSG]
        for p in con.execute(parts_sql, pargs):
            parts_by_msg.setdefault(p["message_id"], []).append(p)
        cnt_sql = ("SELECT message_id, COUNT(*) AS c FROM part "
                   "WHERE message_id IN (" + qmarks + ") GROUP BY message_id")
        for c in con.execute(cnt_sql, msg_ids):
            parts_total[c["message_id"]] = c["c"]
    msgs = []
    for r in rows:
        raw = r["djson"]
        try:
            md = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        mid = r["id"]
        plist = []
        for p in parts_by_msg.get(mid, []):
            praw = p["pjson"]
            size = p["size"] if isinstance(p["size"], int) else (len(praw) if isinstance(praw, str) else 0)
            truncated = size > INSP_PART_DATA_CAP
            raw_frag = False
            try:
                pd = json.loads(praw) if isinstance(praw, str) else {}
            except Exception:
                pd = {}
            if not isinstance(pd, dict):
                pd = {}
            ptype = pd.get("type", "?")
            prev = ""
            for k in ("text", "content", "reasoning", "summary"):
                v = pd.get(k)
                if isinstance(v, str) and v:
                    prev = v[:INSP_PREVIEW]
                    break
            if not prev and isinstance(praw, str):
                prev = praw[:500]
                raw_frag = truncated
            plist.append({"id": p["id"], "type": ptype, "size": size,
                          "preview": prev, "truncated": truncated,
                          "raw_fragment": raw_frag})
        mdict = {"id": mid, "role": md.get("role", "?"),
                 "agent": md.get("agent", ""), "model": md.get("model", ""),
                 "time": r["time_created"],
                 "summary": _sstr(md.get("summary", ""))[:500],
                 "parts": plist}
        omitted = max(0, parts_total.get(mid, 0) - INSP_PARTS_PER_MSG)
        if omitted:
            mdict["parts_omitted"] = omitted
        if isinstance(r["dlen"], int) and r["dlen"] > INSP_MSG_JSON_CAP:
            mdict["data_truncated"] = True
        msgs.append(mdict)
    out = _row_dict(s)
    out["provider"] = parse_provider(out.get("model"))
    out["model"] = parse_model(out.get("model"))
    out.update({"found": True, "messages": msgs, "has_more": has_more,
                "cursor": ((str(msgs[-1]["time"]) + "|" + str(msgs[-1]["id"]))
                           if msgs else None),
                "count": len(msgs), "limit": lim,
                "response_truncated": False,
                "parts_truncated": any(m.get("parts_omitted") for m in msgs)
                or any(p.get("truncated") for m in msgs for p in m["parts"])})
    while (len(json.dumps(out).encode("utf-8")) > INSP_BUDGET_BYTES
           and out["messages"]):
        out["messages"].pop()
        out["response_truncated"] = True
        out["has_more"] = True
        out["count"] = len(out["messages"])
        if out["messages"]:
            last = out["messages"][-1]
            out["cursor"] = str(last["time"]) + "|" + str(last["id"])
    if not out["messages"]:
        out["cursor"] = cursor
    return out

def query_projects(con):
    dirs = {}
    for r in con.execute("SELECT directory, COUNT(*) n, MAX(time_updated) lu FROM session GROUP BY directory"):
        dirs[r["directory"] or ""] = {"n": r["n"], "last": r["lu"]}
    projs = []
    try:
        for r in con.execute("SELECT * FROM project"):
            d = _row_dict(r)
            key = d.get("directory") or d.get("worktree") or d.get("path") or ""
            info = dirs.get(key, {"n": 0, "last": None})
            d["sessions"] = info["n"]
            d["last"] = info["last"]
            projs.append(d)
    except Exception as e:
        return {"error": str(e), "rows": []}
    return {"rows": projs}

def query_signals(con):
    sigs = []
    try:
        z = con.execute("SELECT COUNT(*) c FROM session WHERE id NOT IN (SELECT DISTINCT session_id FROM message)").fetchone()
        if z and z["c"]:
            sigs.append({"level": "info", "text": "Sessions without messages: " + str(z["c"])})
    except Exception:
        pass
    try:
        con.execute("SELECT * FROM router LIMIT 1").fetchone()
    except Exception as e:
        sigs.append({"level": "info", "text": "Router table unreadable: " + str(e)[:120]})
    import time as _time
    try:
        files, cstate, cskip = _codex_rollout_files()
        n_ok, newest, sskip = _codex_rollout_stats(files)
        skipped = cskip + sskip
        if cstate == "missing":
            sigs.append({"level": "warn", "text": "Codex sessions dir missing"})
        elif cstate == "unreadable":
            sigs.append({"level": "warn", "text": "Codex dir unreadable"})
        elif not n_ok:
            if skipped:
                sigs.append({"level": "warn", "text": "Codex: rollout files unreadable (skipped " + str(skipped) + ")"})
            else:
                sigs.append({"level": "warn", "text": "Codex sessions dir empty"})
        else:
            age_h = (_time.time() - newest) / 3600
            txt = "Codex: " + str(n_ok) + " files, newest " + ("%.1f" % age_h) + "h ago"
            if skipped:
                txt += " (partial: " + str(skipped) + " skipped)"
            sigs.append({"level": "ok" if (age_h < 72 and not skipped) else "warn",
                         "text": txt})
    except Exception as e:
        sigs.append({"level": "warn", "text": "Codex dir unreadable: " + str(e)[:120]})
    return {"signals": sigs}

CODEX_DIR = _HOME / ".codex" / "sessions"
_codex_cache = {}
_codex_sig = None

CODEX_PARSER_VERSION = 1
CODEX_CACHE_DIR = Path(__file__).with_name(".cache")
CODEX_INDEX = CODEX_CACHE_DIR / "codex_index.json"
_codex_file_state = {}
_codex_ev_state = {}
_codex_index_note = None
_CODEX_ANCHOR_LEN = 64
# A05: bounded record scans. Lines above the cap are skipped whole while
# reading (their remainder is discarded in block-sized pieces) and counted;
# the scan buffer never exceeds cap + one read block.
_CODEX_READ_BLOCK = 65536
_codex_oversize_records = 0

# F5b: one shared rollout enumeration for diagnostics, the session list and
# the synth reader - same root, same name pattern, same states.
CODEX_ROLLOUT_GLOB = "rollout-*.jsonl"

def _codex_rollout_files():
    # Returns (files, state, skipped). state: missing (no dir), unreadable
    # (listing failed), empty (no matching file), partial (some matching
    # entries vanished or were unreadable while enumerating), ok.
    try:
        if not CODEX_DIR.is_dir():
            return [], "missing", 0
    except OSError:
        return [], "missing", 0
    try:
        cand = sorted(CODEX_DIR.rglob(CODEX_ROLLOUT_GLOB))
    except OSError:
        return [], "unreadable", 0
    files = []
    skipped = 0
    for p in cand:
        try:
            ok = p.is_file()
        except OSError:
            ok = False
        if ok:
            files.append(p)
        else:
            skipped += 1
    if skipped:
        return files, ("partial" if files else "empty"), skipped
    return files, ("ok" if files else "empty"), 0

def _codex_rollout_stats(files):
    # File count and newest mtime over the same set the parsers read; stat
    # failures are counted as skipped (partial read), never as success.
    ok = 0
    newest = 0
    skipped = 0
    for p in files:
        try:
            st = p.stat()
        except OSError:
            skipped += 1
            continue
        ok += 1
        if st.st_mtime > newest:
            newest = st.st_mtime
    return ok, newest, skipped

def _ctext(blocks):
    if not isinstance(blocks, list):
        return ""
    out = []
    for b in blocks:
        if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"]:
            out.append(b["text"])
    return " ".join(out)

def _codex_new_row(p, st):
    return {"file": str(p), "name": p.stem, "n": 0, "tok": 0, "tools": {},
            "previews": [], "model": "", "provider": "", "cwd": "",
            "sid": "", "first": "", "last": "",
            "_sz": st.st_size, "_mt": int(st.st_mtime),
            "_invalid": 0, "_err": False}


def _codex_finish(row):
    out = dict(row)
    out["tools"] = sorted(row["tools"].items(), key=lambda kv: kv[1],
                          reverse=True)[:8]
    return out


def _codex_consume(row, line):
    line = line.strip()
    if not line:
        return
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if not isinstance(rec, dict):
        row["_invalid"] += 1
        return
    t = rec.get("type")
    pl = rec.get("payload")
    if not isinstance(pl, dict):
        pl = {}
    ts = rec.get("timestamp") or ""
    if ts:
        if not row["first"]:
            row["first"] = ts
        row["last"] = ts
    if t == "session_meta":
        row["sid"] = pl.get("id") or row["sid"]
        row["cwd"] = pl.get("cwd") or row["cwd"]
        row["provider"] = pl.get("model_provider") or row["provider"]
    elif t == "event_msg":
        row["n"] += 1
        txt = _ctext((pl.get("item") or {}).get("content"))
        if txt and len(row["previews"]) < 3:
            row["previews"].append(txt[:200])
    elif t == "response_item":
        nm = pl.get("name")
        if nm:
            row["tools"][nm] = row["tools"].get(nm, 0) + 1
        txt = _ctext(pl.get("content"))
        if txt and len(row["previews"]) < 3:
            row["previews"].append(txt[:200])
    elif t == "turn_context":
        if not row["model"] and pl.get("model"):
            row["model"] = pl["model"]
    elif t == "token_usage_record":
        u = pl.get("usage") or {}
        try:
            row["tok"] += int(u.get("total_tokens") or 0)
        except (TypeError, ValueError):
            pass


def _parse_codex_file(p, st, row=None):
    if row is None:
        row = _codex_new_row(p, st)
    try:
        fh = open(p, encoding="utf-8", errors="replace")
    except OSError:
        row["_err"] = True
        return _codex_finish(row)
    with fh:
        for line in fh:
            _codex_consume(row, line)
    row["_sz"] = st.st_size
    row["_mt"] = int(st.st_mtime)
    return _codex_finish(row)


def _codex_files_sig(files, schema=None):
    parts = []
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        parts.append((str(p), st.st_size, st.st_mtime_ns,
                      getattr(st, "st_ino", 0)))
    if schema is None:
        return (CODEX_PARSER_VERSION, tuple(sorted(parts)))
    return (schema, CODEX_PARSER_VERSION, tuple(sorted(parts)))


def _codex_scan_lines(fh, cap, initial_tail=b""):
    """Scan physical source lines with bounded memory (A05).

    Only newline-terminated lines are kept. A complete line above ``cap``
    bytes is skipped whole - its remainder is discarded while scanning for
    the newline - and counted in ``_codex_oversize_records``. An
    unterminated trailing fragment is ignored (the previous ``_codex_split``
    semantics). Returns ``(lines, used, tail)``: ``used`` is the absolute
    offset just past the last newline, ``tail`` the last bytes ending there.
    """
    global _codex_oversize_records
    oversize = 0
    block = _CODEX_READ_BLOCK
    lines = []
    tail = initial_tail
    buf = b""
    base = fh.tell()
    used = base
    overlong = False
    while True:
        chunk = fh.read(block)
        if not chunk:
            break
        buf += chunk
        base = fh.tell() - len(buf)
        pos = 0
        while True:
            nl = buf.find(b"\n", pos)
            if nl < 0:
                if overlong:
                    buf = b""
                    base = fh.tell()
                else:
                    buf = buf[pos:]
                    base = base + pos
                    if len(buf) > cap:
                        _codex_oversize_records += 1
                        oversize += 1
                        overlong = True
                        buf = b""
                        base = fh.tell()
                break
            if overlong:
                overlong = False
                tail = (buf[max(pos, nl - (_CODEX_ANCHOR_LEN - 1)):nl]
                        + b"\n")[-_CODEX_ANCHOR_LEN:]
            else:
                line = buf[pos:nl]
                if len(line) > cap:
                    _codex_oversize_records += 1
                    oversize += 1
                    tail = (line[-(_CODEX_ANCHOR_LEN - 1):]
                            + b"\n")[-_CODEX_ANCHOR_LEN:]
                else:
                    lines.append(line.decode("utf-8", "replace"))
                    tail = (tail + line + b"\n")[-_CODEX_ANCHOR_LEN:]
            used = base + nl + 1
            pos = nl + 1
    return lines, used, tail, oversize


def _codex_full_read(p, fp):
    with open(p, "rb") as fh:
        lines, used, tail, oversize = _codex_scan_lines(
            fh, MAX_ROUTER_RECORD_BYTES)
    return lines, used, fp, True, tail, oversize


def _codex_anchor_ok(p, off, tail):
    want = (tail or b"")[-_CODEX_ANCHOR_LEN:]
    begin = off - len(want)
    if begin < 0 or not want:
        return False
    with open(p, "rb") as fh:
        fh.seek(begin)
        got = fh.read(len(want))
    return got == want


def _codex_read_since(p, state):
    st = p.stat()
    fp = (st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0))
    if state is None:
        return _codex_full_read(p, fp)
    old_fp = state.get("fp") or (0, 0, 0)
    old_off = int(state.get("offset") or 0)
    old_tail = state.get("tail")
    rebuild = (old_tail is None or fp[0] < old_off or fp[2] != old_fp[2]
               or (old_fp[1] is not None and fp[1] < (old_fp[1] or 0)))
    if not rebuild and fp != old_fp and old_off > 0:
        if fp[0] == old_off:
            rebuild = True
        else:
            rebuild = not _codex_anchor_ok(p, old_off, old_tail)
    if rebuild:
        return _codex_full_read(p, fp)
    if fp[0] == old_off:
        return [], old_off, fp, False, old_tail, 0
    with open(p, "rb") as fh:
        fh.seek(old_off)
        lines, used_abs, tail, oversize = _codex_scan_lines(
            fh, MAX_ROUTER_RECORD_BYTES, old_tail)
    if used_abs <= old_off:
        return [], old_off, fp, False, old_tail, 0
    return lines, used_abs, fp, False, tail, oversize


def _codex_update_row(p, st, state):
    cur_fp = (st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0))
    if state is not None and state.get("fp") == cur_fp:
        return state
    try:
        lines, off, fp, rebuild, tail, oversize = _codex_read_since(p, state)
    except OSError:
        # Audit A01 (session list): a transient read error must neither wipe
        # the last good contribution nor be blessed as the current
        # fingerprint - the next plain call retries the read.
        if state is not None:
            row = dict(state.get("row") or {})
            row["_err"] = True
            return {"fp": state.get("fp"),
                    "offset": int(state.get("offset") or 0),
                    "row": row, "tail": state.get("tail") or b"",
                    "oversize": int(state.get("oversize") or 0)}
        row = _codex_new_row(p, st)
        row["_err"] = True
        return {"fp": None, "offset": 0, "row": row, "tail": b"",
                "oversize": 0}
    if state is not None and not rebuild:
        row = state["row"]
    else:
        row = _codex_new_row(p, st)
    for line in lines:
        _codex_consume(row, line)
    row.pop("_err", None)  # a successful read clears the transient error
    row["_sz"] = st.st_size
    row["_mt"] = int(st.st_mtime)
    return {"fp": fp, "offset": off, "row": row, "tail": tail,
            "oversize": ((int(state.get("oversize") or 0) + oversize)
                         if (state is not None and not rebuild)
                         else oversize)}


def _file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _write_codex_index(rows):
    # Checkpoint is a restart optimization, never a source of truth: a write
    # failure must not fail the publish or roll back published data.
    global _codex_index_note
    tmp = None
    try:
        CODEX_INDEX.parent.mkdir(parents=True, exist_ok=True)
        generation = _file_sha256(CODEX_SYNTH) if CODEX_SYNTH.is_file() else None
        files_doc = {}
        for key, state in _codex_ev_state.items():
            try:
                st = Path(key).stat()
            except OSError:
                continue
            files_doc[key] = {
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "ino": getattr(st, "st_ino", 0),
                "offset": int(state.get("offset") or 0),
                "model": state.get("model", ""),
                "provider": state.get("provider", "?"),
                "tail": (state.get("tail") or b"").hex(),
                "oversize": int(state.get("oversize") or 0),
                "events": state.get("events", []),
            }
        doc = {"version": CODEX_PARSER_VERSION, "schema": SYNTH_SCHEMA,
               "generation": generation, "files": files_doc,
               "oversize": int(_codex_last_oversize)}
        tmp = CODEX_INDEX.with_name(CODEX_INDEX.name + ".tmp.%d.%d" % (
            os.getpid(), threading.get_ident()))
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            # Single serialization call: json.dump streams hundreds of small
            # write() calls for a large index; one write keeps the checkpoint
            # cheap even on slow/filtered filesystems (and for tests whose
            # writer proxies count writes).
            f.write(json.dumps(doc))
            f.flush()
        os.replace(str(tmp), str(CODEX_INDEX))
        _codex_index_note = None
        return True
    except OSError:
        if tmp is not None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        _codex_index_note = "codex index write failed (restart optimization lost)"
        return False


def _load_codex_index(files):
    # Adopt a checkpoint only when it matches the published generation and the
    # sources still line up; otherwise reject the checkpoint (never the user
    # logs) and let the caller rebuild from scratch.
    global _codex_last_oversize
    if _codex_ev_state:
        return False
    try:
        doc = json.loads(CODEX_INDEX.read_bytes().decode("utf-8"))
        if doc.get("version") != CODEX_PARSER_VERSION:
            return False
        if doc.get("schema") != SYNTH_SCHEMA:
            return False
        if not CODEX_SYNTH.is_file():
            return False
        if doc.get("generation") != _file_sha256(CODEX_SYNTH):
            return False
        entries = doc.get("files")
        if not isinstance(entries, dict):
            return False
        adopted = {}
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            e = entries.get(str(p))
            if not isinstance(e, dict):
                continue
            try:
                off = int(e.get("offset") or 0)
            except (TypeError, ValueError):
                continue
            if off < 0 or st.st_size < off:
                continue
            saved_ino = e.get("ino") or 0
            ino = getattr(st, "st_ino", 0)
            if saved_ino and ino and saved_ino != ino:
                continue
            raw_tail = e.get("tail")
            if not isinstance(raw_tail, str):
                continue
            try:
                tail = bytes.fromhex(raw_tail)
            except ValueError:
                continue
            adopted[str(p)] = {
                "fp": (int(e.get("size") or 0), int(e.get("mtime_ns") or 0),
                       saved_ino or ino),
                "offset": off,
                "model": str(e.get("model") or ""),
                "provider": str(e.get("provider") or "?"),
                "oversize": int(e.get("oversize") or 0),
                "events": list(e.get("events") or []),
                "tail": tail,
            }
        if not adopted:
            return False
        _codex_ev_state.update(adopted)
        try:
            _codex_last_oversize = int(doc.get("oversize") or 0)
        except (TypeError, ValueError):
            _codex_last_oversize = 0
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def query_codex(force=False):
    global _codex_cache, _codex_sig
    files, _cstate, cskip = _codex_rollout_files()
    skipped = cskip
    sig = _codex_files_sig(files)
    if force or _codex_sig != sig:
        new_states = {}
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            key = str(p)
            new_states[key] = _codex_update_row(
                p, st, None if force else _codex_file_state.get(key))
        _codex_file_state.clear()
        _codex_file_state.update(new_states)
        # Audit A01: do not bless a signature that includes failed reads;
        # the next plain call retries them instead of serving a stale empty.
        if not any((s.get("row") or {}).get("_err")
                   for s in new_states.values()):
            _codex_sig = sig
    sess = []
    invalid = 0
    for key in sorted(_codex_file_state):
        row = _codex_file_state[key]["row"]
        if row.get("_err"):
            skipped += 1
        invalid += row.get("_invalid", 0)
        if row.get("n") or row.get("tok"):
            sess.append(_codex_finish(row))
    sess.sort(key=lambda r: r.get("last") or "", reverse=True)
    models = sorted({(r.get("model") or r.get("provider") or "?") for r in sess})
    tokens = sum(r.get("tok", 0) for r in sess)
    msgs = sum(r.get("n", 0) for r in sess)
    return {
        "ok": True,
        "total": len(sess),
        "files": len(files),
        "tokens": tokens,
        "msgs": msgs,
        "models": models,
        "invalid_records": invalid,
        "partial": bool(skipped),
        "sessions": sess[:200],
    }


CODEX_SYNTH = Path(__file__).with_name("codex_router_events.jsonl")
_codex_ev_cache = {}
_codex_ev_sig = None
_codex_last_error = None  # F5a: last synth publish failure text
_codex_last_ok = None  # F5a: path of the last successful publish
_codex_last_oversize = 0  # A05: oversize source lines skipped in the last publish
# A01: per-file read failures recorded by _parse_codex_events. A build with
# any recorded failure must not publish a new generation (no fake emptiness).
_codex_read_failures = {}
# F6a: publish-stage lock only; thread scope within this process. It is not
# held while parsing rollouts or writing the candidate tmp file.
_SYNTH_LOCK = threading.Lock()
_synth_tmp_active = set()
_synth_tmp_seq = 0
# F6d: single-build gate. Concurrent callers share one parse: waiters block
# on the builder's condition (bounded to 30 s) and then reuse the published
# snapshot; heavy work never multiplies per client request.
_CODEX_BUILD_LOCK = threading.Condition()
_codex_build_in_progress = False

def _numi(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0

def _parse_codex_events(p):
    key = str(p)
    state = _codex_ev_state.get(key)
    try:
        st = p.stat()
    except OSError as e:
        _codex_read_failures[key] = "%s: %s" % (type(e).__name__, e)
        return []
    fp = (st.st_size, st.st_mtime_ns, getattr(st, "st_ino", 0))
    if state is not None and state.get("fp") == fp:
        _codex_read_failures.pop(key, None)
        return list(state["events"])
    try:
        lines, off, fp, rebuild, tail, oversize = _codex_read_since(
            p, state)
    except OSError as e:
        _codex_read_failures[key] = "%s: %s" % (type(e).__name__, e)
        return []
    _codex_read_failures.pop(key, None)
    if state is not None and not rebuild:
        model = state.get("model", "")
        provider = state.get("provider", "?")
        events = state["events"]
    else:
        model = ""
        provider = "?"
        events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        pay = rec.get("payload")
        if not isinstance(pay, dict):
            continue
        t = rec.get("type")
        if t == "session_meta":
            provider = str(pay.get("model_provider") or pay.get("provider")
                           or provider)
        elif t == "turn_context":
            model = str(pay.get("model") or model)
        elif t == "token_usage_record":
            u = pay.get("usage")
            if not isinstance(u, dict):
                continue
            events.append({"at": rec.get("timestamp"), "model": model,
                           "provider": provider,
                           "ti": _numi(u.get("input_tokens")),
                           "cache": _numi(u.get("cached_input_tokens")),
                           "to": _numi(u.get("output_tokens")),
                           "total": _numi(u.get("total_tokens")),
                           "reasoning": _numi(u.get("reasoning_output_tokens")),
                           "cache_write": _numi(u.get("cache_write_input_tokens"))})
    _codex_ev_state[key] = {
        "fp": fp, "offset": off, "model": model,
        "provider": provider, "events": events, "tail": tail,
        "oversize": (int(state.get("oversize") or 0) + oversize)
                    if (state is not None and not rebuild) else oversize,
    }
    return list(events)


def _synth_sig_of(files):
    # Non-additive per-file fingerprint: identity, size, mtime_ns, inode.
    return _codex_files_sig(files, schema=SYNTH_SCHEMA)


def _cleanup_synth_tmps():
    # F6a: remove stale candidates matching our own naming pattern only.
    # Candidates currently being written in this process are spared.
    try:
        pat = CODEX_SYNTH.name + ".tmp."
        for p in CODEX_SYNTH.parent.glob(pat + "*"):
            if p.name in _synth_tmp_active:
                continue
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass

def _synth_next_seq():
    global _synth_tmp_seq
    _synth_tmp_seq += 1
    return _synth_tmp_seq

def ensure_codex_synth(force=False):
    # F5a/F6a: the candidate generation is parsed and written to a unique
    # tmp file, then published by os.replace under _SYNTH_LOCK; only after a
    # successful swap do the signature and parse cache become committed.
    # Returns (path, error): error is None on success; a publish failure
    # keeps the previous generation and returns its error; with no usable
    # previous generation a RuntimeError states the source is unavailable.
    # Readers are lock-free: os.replace yields a complete A or B snapshot.
    # Lock scope is thread-level within this process (single instance).
    global _codex_ev_cache, _codex_ev_sig, _codex_last_error, _codex_last_ok
    global _codex_last_oversize, _codex_oversize_records
    global _codex_build_in_progress
    _cleanup_synth_tmps()
    files, _cstate, _cskip = _codex_rollout_files()
    _load_codex_index(files)
    sig = _synth_sig_of(files)
    if not force and _codex_ev_sig == sig and CODEX_SYNTH.is_file():
        _codex_last_error = None
        return str(CODEX_SYNTH), None
    if not force:
        deadline = time.time() + 30.0
        with _CODEX_BUILD_LOCK:
            while True:
                if _codex_ev_sig == sig and CODEX_SYNTH.is_file():
                    _codex_last_error = None
                    return str(CODEX_SYNTH), None
                if not _codex_build_in_progress:
                    _codex_build_in_progress = True
                    break
                if time.time() > deadline:
                    msg = "codex synth build still in progress after 30 s"
                    _codex_last_error = msg
                    raise RuntimeError(msg)
                _CODEX_BUILD_LOCK.wait(
                    timeout=min(5.0, max(0.05, deadline - time.time())))
    try:
        rows = {}
        failures = {}
        # A05: oversize counts are per-file (state['oversize']); the published
        # generation reports their sum, so appends and restarts keep them.
        for p in files:
            key = str(p)
            try:
                st = p.stat()
            except OSError as e:
                _codex_read_failures[key] = "%s: %s" % (type(e).__name__, e)
                failures[key] = _codex_read_failures[key]
                continue
            rows[key] = (st.st_size, int(st.st_mtime), _parse_codex_events(p))
            if key in _codex_read_failures:
                failures[key] = _codex_read_failures[key]
        if failures:
            # A01: an unread source must not masquerade as an empty source.
            msg = "codex read failed for %d file(s): %s" % (
                len(failures),
                "; ".join(failures[k] for k in sorted(failures)[:3]))
            _codex_last_error = msg
            if CODEX_SYNTH.is_file():
                return str(CODEX_SYNTH), msg
            raise RuntimeError(msg)
        tmp = CODEX_SYNTH.with_name(CODEX_SYNTH.name + ".tmp.%d.%d.%d" % (
            os.getpid(), threading.get_ident(), _synth_next_seq()))
        _synth_tmp_active.add(tmp.name)
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                for key in sorted(rows):
                    for e in rows[key][2]:
                        f.write(json.dumps({"at": e["at"], "model": e["model"],
                            "provider": e["provider"], "status": None,
                            "outcome": "unknown",
                            "inputTokens": e["ti"], "cachedInputTokens": e["cache"],
                            "outputTokens": e["to"], "totalTokens": e["total"],
                            "reasoningTokens": e.get("reasoning", 0),
                            "cacheWriteInputTokens": e.get("cache_write", 0),
                            "durationMs": None}) + "\n")
                f.flush()
            with _SYNTH_LOCK:
                superseded = False
                if not force:
                    if _codex_ev_sig == sig:
                        superseded = CODEX_SYNTH.is_file()
                    elif _synth_sig_of(files) != sig and CODEX_SYNTH.is_file():
                        superseded = True
                if superseded:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    _codex_last_error = None
                    return str(CODEX_SYNTH), None
                os.replace(str(tmp), str(CODEX_SYNTH))
                _codex_ev_cache = rows
                _codex_ev_sig = sig
                _codex_last_ok = str(CODEX_SYNTH)
                _codex_last_oversize = sum(
                    int(_codex_ev_state.get(k, {}).get("oversize") or 0)
                    for k in rows)
                _codex_last_error = None
                _write_codex_index(rows)
                return str(CODEX_SYNTH), None
        except OSError as e:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            msg = "codex synth publish failed: %s: %s" % (type(e).__name__, e)
            _codex_last_error = msg
            if CODEX_SYNTH.is_file():
                return str(CODEX_SYNTH), msg
            raise RuntimeError(msg) from e
        finally:
            _synth_tmp_active.discard(tmp.name)
    finally:
        if not force:
            with _CODEX_BUILD_LOCK:
                _codex_build_in_progress = False
                _CODEX_BUILD_LOCK.notify_all()

def router_events_path(handler_events):
    try:
        if handler_events:
            rp = Path(handler_events)
            if rp.is_file() and rp.stat().st_size > 0:
                return str(rp), False
    except OSError:
        pass
    path, _err = ensure_codex_synth()
    return path, True

def query_stats(con):
    totals = dict(
        con.execute(
            """
            SELECT (SELECT COUNT(*) FROM session) AS sessions,
                   (SELECT COUNT(*) FROM message) AS messages,
                   (SELECT COALESCE(SUM(tokens_input),0) FROM session) AS tokens_input,
                   (SELECT COALESCE(SUM(tokens_output),0) FROM session) AS tokens_output,
                   (SELECT COALESCE(SUM(tokens_reasoning),0) FROM session) AS tokens_reasoning,
                   (SELECT COALESCE(SUM(tokens_cache_read),0) FROM session) AS tokens_cache_read,
                   (SELECT COALESCE(SUM(tokens_cache_write),0) FROM session) AS tokens_cache_write,
                   (SELECT COALESCE(SUM(cost),0) FROM session) AS cost,
                   (SELECT COALESCE(AVG((time_updated-time_created)/60000.0),0)
                      FROM session WHERE time_created>0 AND time_updated>time_created) AS avg_duration_min
            """
        ).fetchone()
    )

    # Single pass over step-finish parts: per-day token totals.
    # Part 'total' = input+output+reasoning+cache.read+cache.write, so the
    # derived cache (total-input-output-reasoning) is read+write combined,
    # matching the session rollups exactly.
    tokens_by_day = {}
    for r in con.execute(
        """
        SELECT time_created, data FROM part
        WHERE json_extract(data,'$.type')='step-finish'
        """
    ):
        try:
            data = json.loads(r["data"])
        except (ValueError, TypeError):
            data = {}
        t = data.get("tokens") or {}
        ti = num(t.get("input"))
        to = num(t.get("output"))
        tr = num(t.get("reasoning"))
        tt = num(t.get("total"))
        cr = num((t.get("cache") or {}).get("read"))
        cost = num(data.get("cost"))
        ms = r["time_created"]
        day = _day(ms)
        if not day:
            continue
        cache = max(0.0, tt - ti - to - tr) or cr
        entry = tokens_by_day.setdefault(
            day, {"ti": 0.0, "to": 0.0, "tr": 0.0, "cache": 0.0, "cost": 0.0}
        )
        entry["ti"] += ti
        entry["to"] += to
        entry["tr"] += tr
        entry["cache"] += cache
        entry["cost"] += cost

    # One scan of the message table gives per-day counts plus the legacy
    # per-hour message intensity series.
    day_msgs = defaultdict(int)
    hours = defaultdict(int)
    for r in con.execute(
        """
        SELECT date(time_created/1000,'unixepoch','localtime') AS d,
               CAST(strftime('%H',time_created/1000,'unixepoch','localtime') AS INTEGER) AS h,
               COUNT(*) AS c
        FROM message GROUP BY d,h
        """
    ):
        day_msgs[r["d"]] += r["c"]
        hours[r["h"]] += r["c"]

    day_sessions = defaultdict(int)
    for r in con.execute(
        "SELECT date(time_created/1000,'unixepoch','localtime') AS d, COUNT(*) AS c FROM session GROUP BY d"
    ):
        day_sessions[r["d"]] = r["c"]

    agents = defaultdict(lambda: {"n": 0, "toks": 0})
    for r in con.execute(
        "SELECT agent, COUNT(*) AS n, COALESCE(SUM(tokens_input)+SUM(tokens_output)+SUM(tokens_reasoning)+SUM(tokens_cache_read)+SUM(tokens_cache_write),0) AS t FROM session GROUP BY agent"
    ):
        a = r["agent"] or "default"
        agents[a]["n"] += r["n"]
        agents[a]["toks"] += r["t"]

    models = defaultdict(lambda: {"n": 0, "ti": 0, "to": 0, "tr": 0, "cache": 0, "cost": 0})
    for r in con.execute(
        """
        SELECT model, COUNT(*) AS n,
                COALESCE(SUM(tokens_input),0) AS ti,
                COALESCE(SUM(tokens_output),0) AS to_,
                COALESCE(SUM(tokens_reasoning),0) AS tr,
                COALESCE(SUM(tokens_cache_read),0)+COALESCE(SUM(tokens_cache_write),0) AS ca,
                COALESCE(SUM(cost),0) AS cost
        FROM session GROUP BY model
        """
    ):
        m = model_key(r["model"])
        models[m]["n"] += r["n"]
        models[m]["ti"] += r["ti"]
        models[m]["to"] += r["to_"]
        models[m]["tr"] += r["tr"]
        models[m]["cache"] += r["ca"]
        models[m]["cost"] += r["cost"]

    # Most recent sessions only, the payload stays bounded and the detail
    # endpoint fetches prompts on demand.
    sessions = []
    for r in con.execute(
        """
        SELECT s.id, s.title, s.agent, s.model, s.time_created, s.time_updated,
                s.tokens_input, s.tokens_output, s.tokens_reasoning,
                COALESCE(s.tokens_cache_read,0)+COALESCE(s.tokens_cache_write,0) AS cache_read, s.cost,
               p.worktree,
               (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) AS msgs
        FROM session s LEFT JOIN project p ON p.id = s.project_id
        ORDER BY s.time_created DESC LIMIT ?
        """,
        (MAX_SESSION_ROWS,),
    ):
        dur = max(0, (r["time_updated"] - r["time_created"]) / 60000.0)
        ti = r["tokens_input"] or 0
        to = r["tokens_output"] or 0
        tr = r["tokens_reasoning"] or 0
        ca = r["cache_read"] or 0
        total = ti + to + tr + ca
        sessions.append(
            {
                "id": r["id"],
                "title": r["title"],
                "agent": r["agent"] or "default",
                "model": parse_model(r["model"]),
                "provider": parse_provider(r["model"]),
                "time_created": r["time_created"],
                "time_updated": r["time_updated"],
                "duration_min": round(dur, 1),
                "tokens_input": ti,
                "tokens_output": to,
                "tokens_reasoning": tr,
                "tokens_cache": ca,
                "tokens_total": total,
                "burn": round(total / dur, 0) if dur >= 0.5 else total,
                "cost": r["cost"],
                "worktree": short_dir(r["worktree"]),
                "msgs": r["msgs"],
            }
        )

    projects = defaultdict(lambda: {"n": 0, "toks": 0})
    pid_info = {}
    for r in con.execute(
        """SELECT p.id, p.name, p.worktree, COUNT(s.id) AS n,
                   COALESCE(SUM(s.tokens_input)+SUM(s.tokens_output)+SUM(s.tokens_reasoning)+SUM(s.tokens_cache_read)+SUM(s.tokens_cache_write),0) AS t
            FROM project p LEFT JOIN session s ON s.project_id=p.id GROUP BY p.id"""
    ):
        key = r["name"] or short_dir(r["worktree"])
        pid_info[r["id"]] = (key, r["worktree"])
        if r["n"]:
            projects[key]["n"] += r["n"]
            projects[key]["toks"] += r["t"] or 0
    worktree_paths = [wt for _, wt in pid_info.values() if wt]

    # Full-table session map (not capped) for token attribution.
    sess_all = {}
    for r in con.execute(
        """SELECT id, project_id, time_updated,
                  COALESCE(tokens_input,0)+COALESCE(tokens_output,0)
                   +COALESCE(tokens_reasoning,0)+COALESCE(tokens_cache_read,0)
                   +COALESCE(tokens_cache_write,0) AS tot
           FROM session"""
    ):
        nm, wt = pid_info.get(r["project_id"], ("-", ""))
        sess_all[r["id"]] = (r["tot"] or 0, r["time_updated"] or 0, nm, wt)

    # Changed files from edit/write tool parts: distinct filePaths per session.
    sess_files = defaultdict(set)
    try:
        for r in con.execute(
            """SELECT session_id, json_extract(data,'$.state.input.filePath') AS fp
               FROM part
               WHERE json_extract(data,'$.type')='tool'
                 AND json_extract(data,'$.tool') IN ('edit','write')"""
        ):
            if r["fp"] and r["session_id"]:
                sess_files[r["session_id"]].add(r["fp"])
    except sqlite3.Error:
        pass

    # Equal-split each session's tokens across its touched files so table
    # sums reconcile with totals instead of double-counting shared sessions.
    file_agg = defaultdict(lambda: {"toks": 0.0, "sess": set()})
    subsys_agg = defaultdict(lambda: {"toks": 0.0, "sess": set(), "files": set()})
    file_subsys = {}
    for sid, files in sess_files.items():
        info = sess_all.get(sid)
        if not info or not files:
            continue
        tot = info[0]
        share = tot / len(files)
        for fp in files:
            fa = file_agg[fp]
            fa["toks"] += share
            fa["sess"].add(sid)
            sub = subsystem_for_file(fp, worktree_paths)
            file_subsys[fp] = sub
            sa = subsys_agg[sub]
            sa["toks"] += share
            sa["sess"].add(sid)
            sa["files"].add(fp)
    file_rows = sorted(
        ([fp, round(v["toks"], 1), len(v["sess"]),
          round(v["toks"] / len(v["sess"]), 0) if v["sess"] else 0]
         for fp, v in file_agg.items()),
        key=lambda r: -r[1],
    )[:MAX_FILE_ROWS]
    subsys_rows = sorted(
        ([k, round(v["toks"], 1), len(v["sess"]), len(v["files"])]
         for k, v in subsys_agg.items()),
        key=lambda r: -r[1],
    )[:MAX_FILE_ROWS]

    # Tokens per commit: sessions in the same project whose update time falls
    # in the 24h window before each commit.
    sess_by_proj = defaultdict(list)
    for sid, (tot, t_upd, nm, _wt) in sess_all.items():
        sess_by_proj[nm].append((t_upd, tot, sid))
    window_ms = COMMIT_WINDOW_HOURS * 3600 * 1000
    commit_rows = []
    commit_sessions = {}
    for c in collect_repo_commits([(nm, wt) for nm, wt in pid_info.values() if wt]):
        matched = [
            (t, tot, sid) for (t, tot, sid) in sess_by_proj.get(c["project"], [])
            if c["time"] - window_ms <= t <= c["time"]
        ]
        toks = sum(tot for _, tot, _ in matched)
        sids = [sid for _, _, sid in sorted(matched, reverse=True)[:25]]
        commit_rows.append(
            [c["sha"], c["subject"][:72], c["date"], c["project"],
             c["files"], c["add"], c["del"], round(toks, 1), len(matched)]
        )
        if sids:
            commit_sessions[c["sha"]] = sids
    commit_rows.sort(key=lambda r: -r[7])
    commit_rows = commit_rows[:MAX_COMMIT_ROWS]
    keep_shas = {r[0] for r in commit_rows}
    commit_sessions = {k: v for k, v in commit_sessions.items() if k in keep_shas}

    days = sorted(set(day_msgs) | set(day_sessions) | set(tokens_by_day))
    day_entries = [
        {
            "date": d,
            "msgs": day_msgs.get(d, 0),
            "sessions": day_sessions.get(d, 0),
            **{k: round(v, 4) for k, v in tokens_by_day.get(d, {"ti": 0, "to": 0, "tr": 0, "cache": 0, "cost": 0}).items()},
        }
        for d in days
    ]
    for e in day_entries:
        e["total"] = round(e["ti"] + e["to"] + e["tr"] + e["cache"], 1)
    activity = pad_activity(day_entries)
    for slot in activity:
        slot["total"] = round(slot["ti"] + slot["to"] + slot["tr"] + slot["cache"], 1)

    # Cache = read + write everywhere locally: part 'total' fields already
    # contain both (total = input+output+reasoning+read+write), so the
    # derived per-day cache matches the session rollups exactly.
    total_tokens = (totals["tokens_input"] + totals["tokens_output"]
                    + totals["tokens_reasoning"] + totals["tokens_cache_read"]
                    + totals["tokens_cache_write"])
    totals["tokens_total"] = total_tokens
    totals["cache_rate"] = cache_rate(totals["tokens_input"], totals["tokens_cache_read"])
    totals["avg_tokens_per_session"] = round(total_tokens / totals["sessions"], 0) if totals["sessions"] else 0

    # Records: biggest day / session / burn / streaks, pure tokens; nice to know.
    # Biggest session + fastest burn come from full-table queries (not the
    # capped recent-sessions list) so large histories still report all-time
    # bests accurately.
    biggest_day = max(day_entries, key=lambda d: d["total"]) if day_entries else None
    biggest_row = con.execute(
        """
        SELECT title, model,
               COALESCE(tokens_input,0)+COALESCE(tokens_output,0)
                 +COALESCE(tokens_reasoning,0)+COALESCE(tokens_cache_read,0)
                 +COALESCE(tokens_cache_write,0) AS tot
        FROM session ORDER BY tot DESC LIMIT 1
        """
    ).fetchone()
    biggest_session = (
        {"title": biggest_row["title"], "tokens_total": biggest_row["tot"],
         "model": parse_model(biggest_row["model"]),
         "provider": parse_provider(biggest_row["model"])}
        if biggest_row else None
    )
    fastest_row = con.execute(
        """
        SELECT title,
               (COALESCE(tokens_input,0)+COALESCE(tokens_output,0)
                 +COALESCE(tokens_reasoning,0)+COALESCE(tokens_cache_read,0)
                 +COALESCE(tokens_cache_write,0)) AS tot,
               (time_updated-time_created)/60000.0 AS mins
        FROM session
        WHERE time_created>0 AND time_updated>time_created
          AND (time_updated-time_created)/60000.0 >= 0.5
        ORDER BY tot/((time_updated-time_created)/60000.0) DESC LIMIT 1
        """
    ).fetchone()
    fastest = (
        {"title": fastest_row["title"],
         "burn": round(fastest_row["tot"] / fastest_row["mins"], 0)}
        if fastest_row else None
    )
    streaks = compute_streaks(activity)
    busiest = biggest_day["date"] if biggest_day else None
    top_model = sorted(models.items(), key=lambda kv: -(kv[1]["ti"] + kv[1]["to"] + kv[1]["tr"] + kv[1]["cache"]))[0] if models else None
    top_agent = sorted(agents.items(), key=lambda kv: -kv[1]["n"])[0] if agents else ("-", {"n": 0})
    longest = con.execute(
        """
        SELECT title, (time_updated-time_created)/60000.0 AS mins FROM session
        WHERE time_created>0 AND time_updated>time_created
        ORDER BY (time_updated-time_created) DESC LIMIT 1
        """
    ).fetchone()

    insights = [
        f"Across {totals['sessions']} sessions you used {total_tokens:,.0f} tokens in {totals['messages']:,} messages.",
        f"Your biggest token day was {busiest}, {biggest_day['total']:,.0f} tokens across {biggest_day['sessions']} sessions."
        if biggest_day else "No activity recorded yet.",
        f"Most-used model: {top_model[0]}, {top_model[1]['n']:,} sessions, {top_model[1]['ti'] + top_model[1]['to'] + top_model[1]['tr'] + top_model[1]['cache']:,.0f} tokens."
        if top_model else "",
        f"The {top_agent[0]} agent did the heavy lifting with {top_agent[1]['n']} sessions and {top_agent[1]['toks']:,} tokens."
        if agents else "",
        f"Top subsystem: {subsys_rows[0][0]}, {subsys_rows[0][1]:,.0f} tokens across {subsys_rows[0][2]} sessions."
        if subsys_rows else "",
        f"Most usage before a commit: “{(commit_rows[0][1] or '')[:60]}”, {commit_rows[0][7]:,.0f} tokens in the 24h window (not commit cost)."
        if commit_rows and commit_rows[0][7] else "",
        f"Longest session: “{longest['title']}”, {fmt_dur_min(longest['mins'])}."
        if longest else "",
        (
            f"Cache hit rate is {totals['cache_rate']}%, {totals['tokens_cache_read'] + totals['tokens_cache_write']:,.0f} cache tokens reused."
            if totals["tokens_cache_read"] else ""
        ),
    ]
    insights = [i for i in insights if i]

    model_rows = []
    for k, v in models.items():
        tot = v["ti"] + v["to"] + v["tr"] + v["cache"]
        model_rows.append(
            [k, v["n"], tot, v["ti"], v["to"], v["tr"], v["cache"], v["cost"],
             round(tot / v["n"], 0) if v["n"] else 0,
             cache_rate(v["ti"], v["cache"])]
        )
    model_rows.sort(key=lambda kv: -kv[2])

    recent_ids = {s["id"] for s in sessions}
    session_files = {
        sid: sorted(files)[:20] for sid, files in sess_files.items() if sid in recent_ids
    }

    stats = {
        "totals": totals,
        "sessions": sessions,
        "tools": [],
        "agents": sorted([[k, v["n"], v["toks"]] for k, v in agents.items()], key=lambda kv: -kv[1]),
        "models": model_rows,
        "projects": sorted([[k, v["n"], v["toks"]] for k, v in projects.items()], key=lambda kv: -kv[2]),
        "files": file_rows,
        "subsystems": subsys_rows,
        "file_subsys": file_subsys,
        "commits": commit_rows,
        "commit_windows": {"method": "time_window", "window_hours": COMMIT_WINDOW_HOURS, "scope": "project_time_window", "overlap_possible": True, "additive": False, "note": "Project-matched time window; windows overlap; rows must not be summed; not commit cost."},
        "session_files": session_files,
        "commit_sessions": commit_sessions,
        "days": day_entries,
        "hours": [hours.get(h, 0) for h in range(24)],
        "activity": activity,
        "insights": insights,
        "records": {
            "biggest_day": {"date": biggest_day["date"], "total": biggest_day["total"], "sessions": biggest_day["sessions"]} if biggest_day else None,
            "biggest_session": {"title": biggest_session["title"], "total": biggest_session["tokens_total"], "model": biggest_session["model"], "provider": biggest_session["provider"]} if biggest_session else None,
            "fastest_burn": {"title": fastest["title"], "burn": fastest["burn"]} if fastest else None,
            "streaks": streaks,
        },
        "range": f"{days[0]} → {days[-1]}" if days else "no activity",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    stats["source"] = "local"
    stats["source_label"] = "Local OpenCode data"
    return stats


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


ZERO_TOTALS = {
    "sessions": 0,
    "messages": 0,
    "tokens_input": 0,
    "tokens_output": 0,
    "tokens_reasoning": 0,
    "tokens_cache_read": 0,
    "tokens_cache_write": 0,
    "tokens_total": 0,
    "cache_rate": 0.0,
    "avg_tokens_per_session": 0,
    "cost": 0,
    "avg_duration_min": 0,
}


def blank_stats(source="local", source_label="Local OpenCode data", error=None, insight=None):
    """Well-formed zero/empty stats skeleton (local-only, token-first)."""
    now = datetime.now().isoformat(timespec="seconds")
    stats = {
        "source": source,
        "source_label": source_label,
        "totals": dict(ZERO_TOTALS),
        "sessions": [],
        "tools": [],
        "agents": [],
        "models": [],
        "projects": [],
        "files": [],
        "subsystems": [],
        "file_subsys": {},
        "commits": [],
        "session_files": {},
        "commit_sessions": {},
        "days": [],
        "hours": [0] * 24,
        "activity": zero_days_window(),
        "insights": [],
        "records": {
            "biggest_day": None,
            "biggest_session": None,
            "fastest_burn": None,
            "streaks": {"current": 0, "longest": 0},
        },
        "range": "no activity",
        "generated_at": now,
    }
    if insight:
        stats["insights"] = [insight]
    if error:
        stats["error"] = error
    return stats


def _fingerprint_paths(paths):
    """mtime+size fingerprint covering SQLite sidecars.

    In WAL mode all writes land in `<db>-wal` while the main file may keep
    the same mtime/size for long stretches. Fingerprinting only the main
    file would then serve stale stats, so -wal/-shm are included.
    """
    fps = []
    for p in paths:
        candidates = [Path(p)]
        if Path(p).suffix in ("", ".db"):
            candidates += [Path(str(p) + "-wal"), Path(str(p) + "-shm")]
        for q in candidates:
            try:
                st = q.stat()
                fps.append((str(q), st.st_mtime_ns, st.st_size))
            except OSError:
                fps.append((str(q), None, None))
    return tuple(fps)


def cached_local_stats(db_path):
    path = Path(db_path)
    try:
        path.stat()
    except OSError:
        raise RuntimeError(
            f"Database not found: {db_path}\nRun OpenCode at least once to create it."
        ) from None
    # Git HEADs join the fingerprint so fresh commits refresh the per-commit
    # panel even when the database itself is untouched.
    fingerprint = (_fingerprint_paths([path]), repo_heads(db_worktrees(db_path)))
    with LOCK:
        if LOCAL_CACHE.get("key") == fingerprint and LOCAL_CACHE.get("stats") is not None:
            return LOCAL_CACHE["stats"]
    con = connect(db_path)
    try:
        stats = query_stats(con)
    finally:
        con.close()
    with LOCK:
        LOCAL_CACHE["key"] = fingerprint
        LOCAL_CACHE["stats"] = stats
    return stats


def router_short(model):
    """Short display name: last path segment, e.g. 'a/b' -> 'b'."""
    if not model:
        return "-"
    return str(model).split("/")[-1] or str(model)


def router_is_free(model):
    return str(model or "").endswith("-free")


def what_if_cost(short, ti, to):
    """What-if paid cost for free-model tokens; 0.0 when pricing unknown."""
    pricing = WHAT_IF_PRICING.get(short or "")
    if not pricing:
        return 0.0
    pi, po = pricing
    return round((ti or 0) / 1e6 * pi + (to or 0) / 1e6 * po, 6)


def _router_event_day_hour(at):
    """Parse usage-events 'at' ISO timestamp -> (local date, local hour)."""
    if not at:
        return None, None
    try:
        s = str(at).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.date().isoformat(), dt.hour
    except (ValueError, TypeError, OverflowError):
        return None, None


def _router_token(value, present):
    """Sanitize one router token counter -> (float_value, valid).

    Only real JSON numbers are measurements. A missing (or explicit null)
    field is absent, not invalid. Present-but-invalid values -- bools,
    NaN/Infinity, negatives, strings, objects/lists -- sanitize to 0.0 and
    are reported via the invalid_records problem counter instead of
    poisoning the aggregates or the JSON payload. Router-path only; the
    local OpenCode num() helper is intentionally untouched.
    """
    if not present or value is None:
        return 0.0, True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0, False
    if not math.isfinite(value) or value < 0:
        return 0.0, False
    return float(value), True


def _router_outcome(status_value, status_present, outcome_value=None):
    """Classify a router record into success/error/unknown -> (status, outcome).

    Router-path only. Strict: only a real JSON int (never bool) in 200-299
    observed from the router is success, 400-599 is error. Missing, null,
    0, bools, floats, strings, lists/dicts, informational (1xx) and
    redirects (3xx) without a final result are unknown. An explicit
    ``outcome`` field of success/error/unknown wins when present (the
    codex synth writer emits status None + outcome unknown). Status is an
    int or None; never the string "unknown" and never a coerced 0 that
    would read as success. Callers must branch on outcome, never on
    ``not ok`` alone, because a third state exists.
    """
    if isinstance(outcome_value, str) and outcome_value in ("success", "error", "unknown"):
        if isinstance(status_value, bool):
            st = None
        elif isinstance(status_value, int):
            st = status_value if status_value != 0 else None
        else:
            st = None
        # Unknown must never carry a fake 200; success/error keep real codes.
        if outcome_value == "unknown" and st == 200:
            # Only trust an explicit unknown with a null/missing status as
            # honest; a bare 200 without provenance is handled by the
            # synth-context override in the caller, not here.
            pass
        return st, outcome_value
    if isinstance(status_value, bool):
        return None, "unknown"
    if not status_present or status_value is None:
        return None, "unknown"
    if isinstance(status_value, int):
        v = status_value
        if 200 <= v <= 299:
            return v, "success"
        if 400 <= v <= 599:
            return v, "error"
        if v == 0:
            return None, "unknown"
        return v, "unknown"
    return None, "unknown"


class _Unterminated(str):
    """A source line that reached EOF without a trailing newline."""


class _OverlongLine(object):
    """Marker for a source line above MAX_ROUTER_RECORD_BYTES."""


_OVERLONG = _OverlongLine()


def _router_count_lines(path, fh=None):
    """Exact physical line count for the usage file, cached and incremental.

    Returns (lines, cached). Cold reads stream the file in bounded chunks
    (never ``readlines``); a warm read with an unchanged fingerprint reuses
    the count; an append counts only the newly written byte range; a shrink
    or an identity change (rotation) falls back to a cold recount.
    """
    p = Path(path)
    key = str(p)
    try:
        # A06: identity always comes from the bound handle when one is given.
        st = os.fstat(fh.fileno()) if fh is not None else p.stat()
    except OSError:
        raise RuntimeError(f"Router usage file not found: {path}")
    size, mtime = st.st_size, st.st_mtime_ns
    ent = _ROUTER_COUNT_CACHE.get(key)
    if ent and ent["size"] == size and ent["mtime_ns"] == mtime:
        return ent["lines"], True
    block = _ROUTER_READ_BLOCK
    cached = False
    newlines = 0
    last = b""
    f = fh
    try:
        if f is None:
            f = open(str(p), "rb")
        try:
            if ent and getattr(st, "st_ino", 0) == ent.get("ino") and size > ent["size"]:
                added = size - ent["size"]
                f.seek(ent["size"])
                while added > 0:
                    chunk = f.read(block if block < added else added)
                    if not chunk:
                        break
                    added -= len(chunk)
                    newlines += chunk.count(b"\n")
                    last = chunk[-1:]
                newlines += ent["newlines"]
                cached = True
            else:
                f.seek(0)
                while True:
                    chunk = f.readline(block)
                    if not chunk:
                        break
                    newlines += chunk.count(b"\n")
                    last = chunk[-1:]
        finally:
            if fh is None and f is not None:
                f.close()
    except OSError:
        raise RuntimeError(f"Router usage file not found: {path}")
    lines = newlines + (1 if size > 0 and last != b"\n" else 0)
    _ROUTER_COUNT_CACHE[key] = {
        "size": size, "mtime_ns": mtime, "ino": getattr(st, "st_ino", 0),
        "newlines": newlines, "lines": lines,
    }
    return lines, cached


def _router_read_tail(path, limit, cap, fh=None):
    """Last ``limit`` physical source lines of the usage file, as text.

    Reads backwards in bounded byte blocks (never ``readlines``): only the
    requested window is ever held - at most ``limit`` complete lines of at
    most ``cap`` bytes each - and the read cost is the tail region, not the
    file. A line above ``cap`` bytes yields the _OVERLONG marker; while
    backtracking such a line its head is discarded in block-sized pieces
    without accumulating. The final line without a newline is marked via
    _Unterminated (unless oversize). meta records partial_head (always
    False: the window starts on a line boundary) and ends_with_newline.
    """
    p = Path(path)
    meta = {"partial_head": False, "ends_with_newline": True}
    try:
        # A06: the bound handle defines the generation being read.
        size = (os.fstat(fh.fileno()) if fh is not None else p.stat()).st_size
    except OSError:
        raise RuntimeError(f"Router usage file not found: {path}")
    if not size:
        return [], meta
    block = _ROUTER_READ_BLOCK
    kept = []           # newest first while scanning, reversed before return
    cur = b""           # bytes of the item currently being assembled
    overlong = False    # discarding the head of an oversize line
    first = True        # the file's own last line is still to be completed
    skip_phantom = False  # set once the trailing newline is known

    def _complete(line):
        if len(line) > cap:
            kept.append(_OVERLONG)
        else:
            kept.append(line.decode("utf-8", "replace"))

    f = fh
    try:
        if f is None:
            f = open(str(p), "rb")
        try:
            f.seek(size - 1)
            ends = f.read(1) == b"\n"
            meta["ends_with_newline"] = ends
            skip_phantom = ends
            pos = size
            while pos > 0 and len(kept) < limit:
                step = block if pos >= block else pos
                pos -= step
                f.seek(pos)
                chunk = f.read(step)
                end = len(chunk)
                while end > 0 and len(kept) < limit:
                    nl = chunk.rfind(b"\n", 0, end)
                    if nl < 0:
                        if not overlong:
                            cur = chunk[:end] + cur
                            if len(cur) > cap:
                                overlong = True
                                cur = b""
                        break
                    seg = chunk[nl + 1:end]
                    if overlong:
                        overlong = False
                        if not (skip_phantom and not seg and not cur):
                            kept.append(_OVERLONG)
                    elif skip_phantom and not seg and not cur:
                        pass  # empty span after the trailing newline
                    else:
                        if first:
                            first = False
                            if not ends:
                                if len(seg + cur) > cap:
                                    kept.append(_OVERLONG)
                                else:
                                    kept.append(_Unterminated(
                                        (seg + cur).decode("utf-8", "replace")))
                            else:
                                _complete(seg + cur)
                        else:
                            _complete(seg + cur)
                    skip_phantom = False
                    cur = b""
                    end = nl
            if pos == 0 and len(kept) < limit:
                if overlong:
                    kept.append(_OVERLONG)
                elif cur:
                    if first and not ends:
                        if len(cur) > cap:
                            kept.append(_OVERLONG)
                        else:
                            kept.append(_Unterminated(
                                cur.decode("utf-8", "replace")))
                    else:
                        _complete(cur)
        finally:
            if fh is None and f is not None:
                f.close()
    except OSError:
        raise RuntimeError(f"Router usage file not found: {path}")
    kept.reverse()
    return kept, meta


def _router_stream_lines(path, cap, fh=None):
    """Lazily yield every physical source line of the usage file as text.

    Memory stays bounded: blocks are read at a fixed size and lines are
    assembled before decoding; a line above ``cap`` bytes yields _OVERLONG
    once and the remainder of that line is discarded. The final line, when
    EOF is reached without a newline, yields _Unterminated text. Returns
    (generator, meta); meta['last_terminated'] mirrors the newline state.
    """
    p = Path(path)
    meta = {"last_terminated": True, "partial_head": False}
    if fh is not None:
        f = fh
    else:
        try:
            f = open(str(p), "rb")
        except OSError:
            raise RuntimeError(f"Router usage file not found: {path}")

    def gen():
        buf = b""
        overlong = False
        block = _ROUTER_READ_BLOCK
        try:
            while True:
                chunk = f.readline(block)
                if not chunk:
                    break
                buf += chunk
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        if overlong:
                            buf = b""
                            break
                        if len(buf) > cap:
                            overlong = True
                            yield _OVERLONG
                            buf = b""
                        break
                    if overlong:
                        overlong = False
                        buf = buf[nl + 1:]
                        continue
                    line = buf[:nl]
                    buf = buf[nl + 1:]
                    if len(line) > cap:
                        yield _OVERLONG
                    else:
                        yield line.decode("utf-8", errors="replace")
            if buf:
                meta["last_terminated"] = False
                if not overlong:
                    if len(buf) > cap:
                        yield _OVERLONG
                    else:
                        yield _Unterminated(buf.decode("utf-8", errors="replace"))
        finally:
            f.close()

    return gen(), meta


def parse_router_events(path, limit=MAX_ROUTER_EVENTS, synth_context=False):
    """Read usage-events.jsonl tail; return (events, problems).

    Each event: {at, date, hour, model, short, provider, status, outcome,
    ok, ti, cache, to, total, reasoning, cache_write, ms, free, what_if,
    total_reported, usage_partial, usage_known}. Status is an int or None;
    outcome is success/error/unknown. ``ok`` is kept as outcome==success
    for compatibility but must not be used as ``not ok`` == error because
    a third state exists. usage_known separates measurement availability
    from outcome: a correct token read in an unknown event still counts
    toward usage; known usage in an error event is not erased. Missing
    token fields become 0 and are never estimated, actuals only.

    Total contract (router path): total = inputTokens + outputTokens.
    Cached input is a subset of input, reasoning a subset of output --
    never additive. A declared totalTokens conflicting with complete
    components is normalized to ti + to and counted in
    problems["total_conflicts"]; total_reported keeps the source value.
    A record with only a declared total keeps that sum in every aggregate
    with usage_partial=True (components are never invented). problems also
    carries invalid_records (records with a present-but-invalid token
    field, sanitized to 0 without aborting the read).

    When synth_context is True (codex-synth generation), records without
    an explicit success/error outcome are forced to unknown even if they
    carry a legacy status 200, so a stale pre-migration index can never
    read as confirmed successes.

    Reading is bounded (F6b): a numeric ``limit`` scans only that many
    physical source lines read from the end of the file in byte blocks
    (never ``readlines`` of the whole file); ``limit=None`` streams the
    whole file for synthesis. ``problems`` carries read metadata beside the
    token problem counters: ``candidate_lines`` (source lines considered in
    the scanned window), ``valid_records`` (parsed events),
    ``oversize_records`` (lines above MAX_ROUTER_RECORD_BYTES, skipped but
    counted), ``pending_tail_line`` (an unterminated trailing fragment that
    is not valid JSON yet), ``partial_head``, ``window_mode``
    ('full'|'physical_tail'), ``lines_total`` and ``count_cached``.
    """
    p = Path(path)
    problems = {
        "invalid_records": 0,
        "total_conflicts": 0,
        "candidate_lines": 0,
        "valid_records": 0,
        "oversize_records": 0,
        "pending_tail_line": False,
        "partial_head": False,
        "window_mode": "full",
        "lines_total": 0,
        "count_cached": False,
    }
    events = []
    if limit:
        try:
            fh = open(str(p), "rb")
        except OSError:
            raise RuntimeError(f"Router usage file not found: {path}")
        try:
            # A06: count and tail read share one handle, so line metadata and
            # scanned content always describe the same file generation even
            # when the name is atomically replaced mid-call.
            lines_total, count_cached = _router_count_lines(p, fh)
            problems["lines_total"] = lines_total
            problems["count_cached"] = count_cached
            lines, meta = _router_read_tail(
                p, limit, MAX_ROUTER_RECORD_BYTES, fh)
        finally:
            fh.close()
        problems["partial_head"] = meta["partial_head"]
        problems["window_mode"] = (
            "full" if len(lines) >= lines_total else "physical_tail")
    else:
        try:
            fh = open(str(p), "rb")
        except OSError:
            raise RuntimeError(f"Router usage file not found: {path}")
        try:
            # A06: count and full scan share one handle, so line metadata and
            # scanned content always describe the same file generation even
            # when the name is atomically replaced mid-call.
            lines_total, count_cached = _router_count_lines(p, fh)
            problems["lines_total"] = lines_total
            problems["count_cached"] = count_cached
            fh.seek(0)
            lines, meta = _router_stream_lines(p, MAX_ROUTER_RECORD_BYTES, fh)
        except BaseException:
            fh.close()
            raise
    for line in lines:
        if line is _OVERLONG:
            problems["oversize_records"] += 1
            problems["candidate_lines"] += 1
            continue
        unterminated = isinstance(line, _Unterminated)
        problems["candidate_lines"] += 1
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            if unterminated:
                problems["pending_tail_line"] = True
            continue
        if not isinstance(e, dict):
            continue
        model = e.get("model") or "-"
        short = router_short(model)
        raw_status = e.get("status")
        status_present = "status" in e
        raw_outcome = e.get("outcome")
        status, outcome = _router_outcome(raw_status, status_present, raw_outcome)
        if synth_context and raw_outcome not in ("success", "error"):
            # Synth has no confirmed HTTP outcomes; never trust a legacy
            # baked-in 200 from a pre-migration index.
            status, outcome = None, "unknown"
        ok = outcome == "success"
        ti, ti_ok = _router_token(e.get("inputTokens"), "inputTokens" in e)
        cache, cache_ok = _router_token(e.get("cachedInputTokens"), "cachedInputTokens" in e)
        to, to_ok = _router_token(e.get("outputTokens"), "outputTokens" in e)
        total_declared, total_ok = _router_token(e.get("totalTokens"), "totalTokens" in e)
        reasoning, reasoning_ok = _router_token(e.get("reasoningTokens"), "reasoningTokens" in e)
        cache_write, cw_ok = _router_token(e.get("cacheWriteInputTokens"), "cacheWriteInputTokens" in e)
        if not (ti_ok and cache_ok and to_ok and total_ok and reasoning_ok and cw_ok):
            problems["invalid_records"] += 1
        has_ti = "inputTokens" in e and e.get("inputTokens") is not None
        has_to = "outputTokens" in e and e.get("outputTokens") is not None
        has_total = "totalTokens" in e and e.get("totalTokens") is not None
        usage_partial = False
        usage_known = False
        if ti_ok and to_ok and has_ti and has_to:
            total = ti + to
            usage_known = True
            if total_ok and has_total and total_declared != total:
                problems["total_conflicts"] += 1
        elif total_ok and has_total:
            total = total_declared
            usage_known = True
            usage_partial = True
        else:
            total = 0.0
            usage_known = False
            usage_partial = True
        try:
            ms = float(e.get("durationMs") or 0)
        except (TypeError, ValueError):
            ms = 0.0
        if not math.isfinite(ms) or ms < 0:
            ms = 0.0
        date, hour = _router_event_day_hour(e.get("at"))
        events.append(
            {
                "at": e.get("at"),
                "date": date,
                "hour": hour,
                "model": str(model),
                "short": short,
                "provider": str(e.get("provider") or "-"),
                "status": status,
                "outcome": outcome,
                "ok": ok,
                "ti": ti,
                "cache": cache,
                "to": to,
                "total": total,
                "reasoning": reasoning,
                "cache_write": cache_write,
                "ms": ms,
                "free": router_is_free(model),
                "what_if": what_if_cost(short, ti, to),
                "total_reported": total_declared if total_ok and has_total else None,
                "usage_partial": usage_partial,
                "usage_known": usage_known,
            }
        )
    problems["valid_records"] = len(events)
    return events, problems


def _router_time_key(at):
    """Parse a usage-events 'at' timestamp to epoch seconds for ordering.

    Falls back to 0.0 for missing/unparseable values (sorted last).
    Naive datetimes are interpreted in local time, aware ones converted.
    """
    if not at:
        return 0.0
    try:
        s = str(at).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def read_rate_limits(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def query_router_stats(events_path, limits_path=None, worktrees=None, full_scan=False):
    """Aggregate Codex Router usage-events into a dashboard payload (all models).

    When full_scan is true (synthesized sources) every line is scanned and
    truncated is always False. Otherwise only the most recent
    MAX_ROUTER_EVENTS lines are scanned; when the file is longer the payload
    reports truncated=True with scanned/total_lines counts so the window is
    never silently understated. Recent-requests table stays capped at
    MAX_ROUTER_ROWS either way.

    `worktrees` ([(name, path)]) feeds the tokens-per-commit panel: router
    events carry no project info, so attribution is timestamp-only, request
    tokens in the 24h window before each commit.
    """
    if full_scan:
        scanned_events, problems = parse_router_events(events_path, None, synth_context=True)
    else:
        scanned_events, problems = parse_router_events(events_path, MAX_ROUTER_EVENTS, synth_context=False)
    total_lines = problems["lines_total"]
    truncated = (not full_scan) and problems["window_mode"] == "physical_tail"
    # Events with no usage measurement (usage_known False: explicit zero
    # counts as measured) are excluded from every count and average -
    # never estimated, and reported separately as `unmetered`.
    # F4: three outcomes. usage_known (a real token read) is independent
    # of outcome, so unknown/error events with tokens still count.
    # A05: one pass over the scanned events; no per-subset copies beyond the
    # scanned list itself (counters and running sums instead of full lists).
    events = scanned_events
    ok_count = err_count = measured_count = unmetered = 0
    err429 = err500 = 0
    ti = cache = to = toks_total = 0.0
    tr = cw = 0.0
    what_if = 0.0
    free_unpriced = 0
    ms_sum = ms_count = 0
    ok_times = []
    biggest_req = None
    for e in events:
        outcome = e["outcome"]
        if outcome == "success":
            ok_count += 1
            at_ms = _router_time_key(e.get("at"))
            if at_ms > 0:
                ok_times.append((at_ms * 1000, e["total"]))
            if biggest_req is None or e["total"] > biggest_req["total"]:
                biggest_req = e
        elif outcome == "error":
            err_count += 1
        if e["status"] == 429:
            err429 += 1
        if type(e["status"]) is int and e["status"] >= 500:
            err500 += 1
        if e.get("usage_known"):
            measured_count += 1
            ti += e["ti"]
            cache += e["cache"]
            to += e["to"]
            # Headline usage is the normalized per-event total (ti + to for
            # complete records, the reported sum for total-only records), so
            # every aggregate shares one definition.
            toks_total += e["total"]
            tr += e.get("reasoning", 0)
            cw += e.get("cache_write", 0)
            what_if += e["what_if"]
            if e["free"] and not e["what_if"]:
                free_unpriced += 1
            # Latency over measured requests only: fast 429/401 rejects would
            # otherwise drag the average down and misrepresent model speed.
            if e["ms"] > 0:
                ms_sum += e["ms"]
                ms_count += 1
        else:
            unmetered += 1
    what_if = round(what_if, 4)
    avg_ms = round(ms_sum / ms_count, 1) if ms_count else None
    ok_times.sort()

    by_model = {}
    for e in events:
        m = by_model.setdefault(
            e["model"],
            {"provider": e["provider"], "reqs": 0, "ok": 0, "err": 0,
             "unknown": 0, "measured": 0,
             "ti": 0.0, "to": 0.0, "cache": 0.0, "total": 0.0,
             "reasoning": 0.0,
             "what_if": 0.0, "ms": 0.0, "free": e["free"]},
        )
        m["reqs"] += 1
        if e["outcome"] == "success":
            m["ok"] += 1
        elif e["outcome"] == "error":
            m["err"] += 1
        else:
            m["unknown"] += 1
        if e.get("usage_known"):
            m["measured"] += 1
            m["ti"] += e["ti"]
            m["to"] += e["to"]
            m["cache"] += e["cache"]
            m["total"] += e["total"]
            m["reasoning"] += e.get("reasoning", 0)
            m["what_if"] += e["what_if"]
        m["ms"] += e["ms"]

    model_rows = []
    for name, m in by_model.items():
        tot = m["total"]
        model_rows.append(
            [name, m["reqs"], tot, m["ti"], m["to"], m["cache"], m["ok"], m["err"],
             round(tot / m["measured"], 0) if m["measured"] else 0,
             router_cache_rate(m["ti"], m["cache"]), m["provider"],
             m["free"], round(m["what_if"], 4), round(m["reasoning"], 1),
             m["unknown"]]
        )
    model_rows.sort(key=lambda r: -r[2])

    by_provider = defaultdict(lambda: {"reqs": 0, "toks": 0.0})
    for e in events:
        by_provider[e["provider"]]["reqs"] += 1
        if e.get("usage_known"):
            by_provider[e["provider"]]["toks"] += e["total"]

    day_buckets = {}
    hour_reqs = defaultdict(int)
    hour_tokens = defaultdict(float)
    for e in events:
        if e["hour"] is not None:
            hour_reqs[e["hour"]] += 1
            if e.get("usage_known"):
                hour_tokens[e["hour"]] += e["total"]
        if not e["date"]:
            continue
        b = day_buckets.setdefault(
            e["date"], {"reqs": 0, "ok": 0, "err": 0, "unknown": 0, "err429": 0, "err500": 0, "ti": 0.0, "to": 0.0,
                        "cache": 0.0, "tr": 0.0, "total": 0.0, "what_if": 0.0}
        )
        b["reqs"] += 1
        if e["status"] == 429:
            b["err429"] += 1
        if type(e["status"]) is int and e["status"] >= 500:
            b["err500"] += 1
        if e["outcome"] == "success":
            b["ok"] += 1
        elif e["outcome"] == "error":
            b["err"] += 1
        else:
            b["unknown"] += 1
        if e.get("usage_known"):
            b["ti"] += e["ti"]
            b["to"] += e["to"]
            b["cache"] += e["cache"]
            b["tr"] += e.get("reasoning", 0)
            b["total"] += e["total"]
            b["what_if"] += e["what_if"]

    day_entries = []
    for d in sorted(day_buckets):
        b = day_buckets[d]
        # Router inputTokens already includes cachedInputTokens, and output
        # already includes reasoning: the day total is the normalized
        # per-event total (ti + to, or the reported sum for total-only
        # records), never ti + to + tr.
        tot = b["total"]
        day_entries.append(
            {"date": d, "msgs": b["reqs"], "sessions": b["ok"], "reqs": b["reqs"],
             "ok": b["ok"], "err": b["err"], "unknown": b["unknown"], "err429": b["err429"], "err500": b["err500"],
             "ti": round(b["ti"], 1),
             "to": round(b["to"], 1), "tr": round(b["tr"], 1), "cache": round(b["cache"], 1),
             "cost": round(b["what_if"], 4), "what_if": round(b["what_if"], 4),
             "total": round(tot, 1)}
        )
    activity = pad_activity(day_entries)
    day_total_by_date = {de["date"]: de["total"] for de in day_entries}
    for slot in activity:
        # Heatmap cells reuse the authoritative day total (which includes
        # reported-only sums); empty window days fall back to ti + to = 0.
        slot["total"] = round(day_total_by_date.get(slot["date"], slot["ti"] + slot["to"]), 1)
        slot.setdefault("reqs", slot.get("msgs", 0))

    recent = sorted(events, key=lambda e: (_router_time_key(e.get("at")), str(e.get("at") or "")),
                      reverse=True)[:MAX_ROUTER_ROWS]

    # Tokens per commit from router request tokens (timestamp-only join: the
    # usage-events stream has no project/file info). Same commit list and
    # 24h window as the local tab so both sides stay comparable.
    window_ms = COMMIT_WINDOW_HOURS * 3600 * 1000
    r_commit_rows = []
    for c in collect_repo_commits(worktrees or []):
        lo = c["time"] - window_ms
        toks = reqs = 0
        for t_ms, tot in ok_times:
            if t_ms > c["time"]:
                break
            if t_ms >= lo:
                toks += tot
                reqs += 1
        r_commit_rows.append(
            [c["sha"], c["subject"][:72], c["date"], c["project"],
             c["files"], c["add"], c["del"], round(toks, 1), reqs]
        )
    r_commit_rows.sort(key=lambda r: -r[7])
    r_commit_rows = r_commit_rows[:MAX_COMMIT_ROWS]

    avg_per_session = (round(toks_total / measured_count, 0)
                       if measured_count else 0)
    known = ok_count + err_count
    totals = {
        "requests": len(events),
        "ok": ok_count,
        "errors": err_count,
        "unknown": len(events) - known,
        "known_outcomes": known,
        "success_rate": round(ok_count / known, 4) if known else None,
        "coverage": {"known": known, "total": len(events)},
        "err429": err429,
        "err500": err500,
        "unmetered": unmetered,
        "invalid_records": problems["invalid_records"],
        "total_conflicts": problems["total_conflicts"],
        "tokens_input": ti,
        "tokens_output": to,
        "tokens_reasoning": tr,
        "tokens_cache_read": cache,
        "tokens_cache_write": cw,
        "tokens_total": toks_total,
        "cache_rate": router_cache_rate(ti, cache),
        "models": len(by_model),
        "providers": len(by_provider),
        "what_if_cost": what_if,
        "avg_ms": avg_ms,
        "sessions": ok_count,
        "messages": len(events),
        "cost": 0,
        "avg_duration_min": 0,
        "avg_tokens_per_session": avg_per_session,
        "avg_tokens_per_metered": avg_per_session,
    }

    biggest_day = max(day_entries, key=lambda d: d["total"]) if day_entries else None
    top_model = model_rows[0] if model_rows else None
    peak_hour = max(range(24), key=lambda h: hour_tokens.get(h, 0)) if any(hour_tokens.values()) else None
    req_word = "Usage events" if full_scan else "requests"
    insights = [
        f"Codex made {len(events):,} {req_word} across {len(by_model)} models, {ok_count:,} ok, {err_count:,} errors.",
        (f"Top model: {router_short(top_model[0])}, {top_model[2]:,.0f} tokens over {top_model[1]:,} requests."
         if top_model else ""),
        (f"Busiest day: {biggest_day['date']}, {biggest_day['total']:,.0f} tokens, {biggest_day['reqs']} requests."
         if biggest_day else "No Codex activity recorded yet."),
    ]
    if err429:
        insights.append(f"{err429} requests hit 429 rate limits, usually free-model capacity, not token size.")
    if unmetered:
        insights.append(
            f"{unmetered:,} requests reported no usage measurement "
            f"(unmetered), excluded from all counts and averages."
        )
    if free_unpriced:
        insights.append(
            f"What-if paid ${what_if:.2f} covers only models with known pricing "
            f"(muse-spark), {free_unpriced:,} other free-model requests have no rate on file."
        )
    if truncated:
        insights.append(
            f"Showing the most recent {len(scanned_events):,} of {total_lines:,} events, "
            f"totals cover the scanned window only."
        )
    insights = [i for i in insights if i]

    limits = read_rate_limits(limits_path) if limits_path else {}
    stats = {
        "source": "router",
        "source_label": "Codex data",
        "is_synth": full_scan,
        "req_word": req_word,
        "req_word_short": "calls" if full_scan else "req",
        "latency_na": full_scan,
        "totals": totals,
        "models": model_rows,
        "providers": sorted([[k, v["reqs"], round(v["toks"], 1)] for k, v in by_provider.items()],
                            key=lambda kv: -kv[2]),
        "days": day_entries,
        "hours": [hour_reqs.get(h, 0) for h in range(24)],
        "commits": r_commit_rows,
        "commit_windows": {"method": "time_window", "window_hours": COMMIT_WINDOW_HOURS, "scope": "global", "overlap_possible": True, "additive": False, "note": "Global time window; may include other projects. Windows overlap. Rows must not be summed."},
        "activity": activity,
        "requests": recent,
        "rate_limits": limits,
        "insights": insights,
        "records": {
            "biggest_day": {"date": biggest_day["date"], "total": biggest_day["total"],
                            "sessions": biggest_day["reqs"]} if biggest_day else None,
            "biggest_session": None,
            "fastest_burn": None,
            "biggest_model": {"name": top_model[0], "total": top_model[2],
                              "requests": top_model[1]} if top_model else None,
            "biggest_request": {"at": biggest_req["at"], "model": biggest_req["model"],
                                "total": biggest_req["total"]} if biggest_req else None,
            "peak_hour": peak_hour,
            "streaks": compute_streaks(activity, tokens_only=True),
        },
        "range": f"{day_entries[0]['date']} → {day_entries[-1]['date']}" if day_entries else "no activity",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scanned": len(scanned_events),
        "total_lines": total_lines,
        "truncated": truncated,
        "window_mode": problems["window_mode"],
        "candidate_lines": problems["candidate_lines"],
        "pending_tail_line": problems["pending_tail_line"],
        "oversize_records": problems["oversize_records"],
    }
    return stats


def blank_router_stats(error=None):
    """Well-formed empty router payload (same shape as query_router_stats)."""
    now = datetime.now().isoformat(timespec="seconds")
    stats = {
        "source": "router",
        "source_label": "Codex data",
        "totals": {"requests": 0, "ok": 0, "errors": 0, "unknown": 0,
                   "known_outcomes": 0, "success_rate": None,
                   "coverage": {"known": 0, "total": 0},
                   "err429": 0, "err500": 0,
                   "unmetered": 0,
                   "invalid_records": 0, "total_conflicts": 0,
                   "tokens_input": 0, "tokens_output": 0, "tokens_reasoning": 0,
                   "tokens_cache_read": 0, "tokens_cache_write": 0, "tokens_total": 0,
                   "cache_rate": 0.0, "models": 0, "providers": 0, "what_if_cost": 0,
                   "avg_ms": 0, "sessions": 0, "messages": 0, "cost": 0,
                   "avg_duration_min": 0, "avg_tokens_per_session": 0,
                   "avg_tokens_per_metered": 0},
        "models": [],
        "providers": [],
        "days": [],
        "hours": [0] * 24,
        "commits": [],
        "commit_windows": {"method": "time_window", "window_hours": COMMIT_WINDOW_HOURS, "scope": "global", "overlap_possible": True, "additive": False, "note": "Global time window; may include other projects. Windows overlap. Rows must not be summed."},
        "activity": zero_days_window(),
        "requests": [],
        "rate_limits": {},
        "is_synth": False,
        "req_word": "requests",
        "req_word_short": "req",
        "latency_na": False,
        "insights": ["No Codex activity found."],
        "records": {"biggest_day": None, "biggest_session": None,
                    "fastest_burn": None, "biggest_model": None,
                    "biggest_request": None, "peak_hour": None,
                    "streaks": {"current": 0, "longest": 0}},
        "range": "no activity",
        "generated_at": now,
        "scanned": 0,
        "total_lines": 0,
        "truncated": False,
        "window_mode": "full",
        "candidate_lines": 0,
        "pending_tail_line": False,
        "oversize_records": 0,
    }
    if error:
        stats["error"] = error
    return stats


def cached_router_stats(events_path, limits_path=None, worktrees=None, full_scan=False):
    paths = [Path(events_path)]
    if limits_path:
        paths.append(Path(limits_path))
    try:
        fps = []
        for p in paths:
            st = p.stat()
            fps.append((str(p), st.st_mtime_ns, st.st_size))
        fingerprint = (full_scan, tuple(fps), repo_heads(worktrees))
    except OSError:
        raise RuntimeError(f"Router usage file not found: {events_path}")
    with LOCK:
        if ROUTER_CACHE.get("key") == fingerprint and ROUTER_CACHE.get("stats") is not None:
            return ROUTER_CACHE["stats"]
    stats = query_router_stats(events_path, limits_path, worktrees, full_scan)
    with LOCK:
        ROUTER_CACHE["key"] = fingerprint
        ROUTER_CACHE["stats"] = stats
    return stats


def session_prompts(con, session_id):
    """Prompts for one session, from session_input when available, otherwise
    reconstructed from user text parts (older opencode versions)."""
    rows = [
        {"t": r["time_created"], "p": r["prompt"]}
        for r in con.execute(
            "SELECT time_created, prompt FROM session_input "
            "WHERE session_id=? ORDER BY time_created",
            (session_id,),
        )
    ]
    if rows:
        return rows[-50:]
    rows = []
    for r in con.execute(
        """
        SELECT p.time_created, json_extract(p.data,'$.text') AS txt
        FROM part p JOIN message m ON m.id = p.message_id
        WHERE p.session_id = ?
          AND json_extract(p.data,'$.type')='text'
          AND json_extract(m.data,'$.role')='user'
        ORDER BY p.time_created
        """,
        (session_id,),
    ):
        txt = (r["txt"] or "").strip()
        if txt:
            rows.append({"t": r["time_created"], "p": txt})
    return rows[-50:]


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode Dashboard</title>
<link rel="icon" type="image/png" href="/logo.png">
<meta name="author" content="zX">
<!-- made by zX -->
  
<style>
:root{
  --bg:#faf6ec; --panel:#ffffff; --panel2:#f5eedd; --line:#e4d5ae; --line2:#efe3c8;
  --text:#1a1006; --muted:#7a6a4a; --subtle:#a89468;
  --accent:#a6761d; --accent2:#c9a84c; --accent3:#7a5c14; --cache:#c07f1a;
  --good:#16a34a; --bad:#dc2626; --chip:#f3e9cf;
  --shadow:0 1px 2px rgba(28,28,26,.05),0 8px 24px rgba(28,28,26,.06);
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
}
[data-theme="dark"]{
  --bg:#0a0a0a; --panel:#1a1006; --panel2:#241a08; --line:#4a3a17; --line2:#33270e;
  --text:#ffffff; --muted:#c9bfa8; --subtle:#8a7a5c;
  --accent:#c9a84c; --accent2:#f4d78d; --accent3:#d8ad44; --cache:#d99a26;
  --good:#4ade80; --bad:#f87171; --chip:#241a08;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.5;
  transition:background .25s ease,color .25s ease}
.wrap{max-width:1760px;margin:0 auto;padding:0 28px 70px}
.mono{font-family:var(--mono)}

/* top bar */
.topbar{position:sticky;top:0;z-index:40;background:var(--bg);border-bottom:1px solid var(--line)}
.topbar-in{max-width:1760px;margin:0 auto;padding:12px 28px;display:flex;align-items:center;gap:14px}
.brand{display:flex;align-items:center;gap:9px;font-weight:800;font-size:15px;letter-spacing:-.02em}
.brand .dot{width:9px;height:9px;border-radius:3px;background:linear-gradient(135deg,var(--accent),var(--accent2))}
.brand small{color:var(--subtle);font-weight:600}
.brand-logo{width:26px;height:26px;border-radius:7px}
.spacer{flex:1}
.tbtn{appearance:none;background:var(--chip);border:1px solid var(--line);color:var(--muted);border-radius:9px;
  height:34px;padding:0 12px;font-family:var(--sans);font-size:12.5px;font-weight:600;cursor:pointer;transition:.15s}
.tbtn:hover{color:var(--text);border-color:var(--subtle)}
.tbtn:active{transform:scale(.97)}
.tbtn:disabled{opacity:.5;cursor:wait}
#status{font-family:var(--mono);font-size:11px;color:var(--subtle);white-space:nowrap}

/* hero */
.hero{padding:52px 0 14px}
.hero .eyebrow{font-family:var(--mono);font-size:11.5px;color:var(--subtle);letter-spacing:.12em;text-transform:uppercase}
.hero h1{font-size:clamp(26px,4vw,38px);font-weight:800;letter-spacing:-.035em;line-height:1.12;margin-top:10px;max-width:720px}
.hero h1 b{font-weight:800;background:linear-gradient(100deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;color:transparent}
.hero .pills{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
.pill{font-family:var(--mono);font-size:11px;background:var(--panel);border:1px solid var(--line);color:var(--muted);
  padding:5px 12px;border-radius:999px}

/* KPI strip */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px;margin-top:26px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;box-shadow:var(--shadow)}
.kpi h3{font-size:10.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--subtle);margin-bottom:10px}
.kpi .big{font-family:var(--mono);font-size:26px;font-weight:700;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.kpi .big small{font-size:11.5px;color:var(--subtle);font-weight:500}
.tokbar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--chip);margin:12px 0 10px}
.tokbar i{height:100%}
.krow{display:flex;justify-content:space-between;align-items:center;gap:10px;font-size:12.5px;color:var(--muted);padding:3.5px 0;font-variant-numeric:tabular-nums}
.krow b{color:var(--text);font-family:var(--mono);font-weight:600}
.krow .lbl{display:flex;align-items:center;min-width:0}
.krow .dot{width:8px;height:8px;border-radius:3px;margin-right:8px;flex-shrink:0}
.krow .dim{color:var(--subtle);font-size:11px;margin-left:5px}

/* sections */
h2.sec{font-size:17px;font-weight:700;letter-spacing:-.02em;margin:34px 0 14px;display:flex;align-items:baseline;gap:10px;scroll-margin-top:70px}
h2.sec .hint{font-family:var(--mono);font-size:11px;color:var(--subtle);font-weight:500}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px;box-shadow:var(--shadow)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.chart-card .ctitle{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--subtle);margin-bottom:12px}
.chart-card .ctitle small{font-weight:500;letter-spacing:0;text-transform:none;color:var(--subtle);margin-left:8px}

/* insight cards */
.insights{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin-top:20px}
.in{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.in p{font-size:13px;color:var(--muted);line-height:1.55}
.in p b{color:var(--text)}

/* legend */
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:12px;font-size:11.5px;color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:-1px}

/* tools */
.tool{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.tool:last-child{margin-bottom:0}
.tool .nm{width:92px;font-size:12.5px;text-align:right;color:var(--muted);font-weight:600;flex-shrink:0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tool .track{flex:1;height:8px;background:var(--chip);border-radius:6px;overflow:hidden}
.tool .fill{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--accent),var(--accent2));width:0;transition:width .7s cubic-bezier(.22,.9,.35,1)}
.tool .ct{width:88px;text-align:right;font-family:var(--mono);font-size:12px;color:var(--muted)}
.tool.dim{opacity:.3}
.tool.clickable{cursor:pointer}
.tool.clickable:hover .nm{color:var(--text)}

/* chips */
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:flex;align-items:center;gap:8px;background:var(--chip);border:1px solid var(--line);border-radius:10px;
  padding:8px 13px;font-size:12.5px;cursor:pointer;transition:border-color .12s,transform .12s}
.chip:hover{border-color:var(--subtle);transform:translateY(-1px)}
.chip.active{border-color:var(--accent);box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 25%,transparent)}
.chip b{font-family:var(--mono);font-weight:700}
.chip .n{color:var(--muted)}

/* interactive svg */
.inter{cursor:pointer}
.inter:hover{filter:brightness(1.15)}
.dim{opacity:.22}

/* table */
.tbl-tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.search{flex:1;min-width:200px;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 13px;
  font-family:var(--sans);font-size:13px;color:var(--text);outline:none}
.search:focus{border-color:var(--accent)}
.search::placeholder{color:var(--subtle)}
.filter{appearance:none;background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:8px 12px;
  font-family:var(--sans);font-size:12.5px;color:var(--muted);cursor:pointer;outline:none}
.fbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:9px 12px;margin-bottom:10px;
  background:color-mix(in srgb,var(--accent) 6%,transparent);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
  border-radius:10px;font-size:12.5px}
.fbar .fl{color:var(--muted);font-weight:600}
.fchip{display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);border-radius:999px;
  padding:3px 12px;font-family:var(--mono);font-size:11.5px;cursor:pointer}
.fchip:hover{border-color:var(--bad)}
.fchip .fx{color:var(--subtle);font-weight:800}
.fchip:hover .fx{color:var(--bad)}
.fclear{background:none;border:none;color:var(--bad);font-family:var(--sans);font-size:12px;font-weight:600;cursor:pointer;padding:3px 6px}
.fclear:hover{text-decoration:underline}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:12px}.tile{flex:1 1 160px;min-width:150px;max-width:230px;background:var(--chip);border:1px solid var(--line);border-radius:12px;padding:14px;cursor:pointer;transition:border-color .15s,transform .15s}.tile:hover{border-color:var(--accent);transform:translateY(-2px)}.tile.active{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}.tile .tn{font-weight:800;font-size:15px}.tile .tc{font-size:26px;font-weight:800;color:var(--accent);margin:4px 0}.tile .td{font-size:11.5px;color:var(--muted)}.tile-detail{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:12px}.tile-detail .wrow{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px dashed var(--line)}.tile-detail .wrow:last-child{border-bottom:none}tr.clickable{cursor:pointer}tr.clickable:hover td{background:rgba(127,127,127,.09)}.run-detail td{background:var(--panel);font-size:12px;color:var(--muted)}.kv{display:inline-block;margin:2px 14px 2px 0}.kv b{color:var(--text)}.dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin:2px 2px 4px}.dot.recent{background:#7aa2f7}.dot.quiet{background:#8a8f98}.dot.stale{background:#6b7280;opacity:.6}.dot.unknown{background:transparent;border:1.5px dashed #6b7280}.tbl-scroll{overflow:auto;max-height:min(62vh,640px)}
.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
.tbl thead th{position:sticky;top:0;z-index:5;background:var(--panel);text-align:left;font-size:10.5px;font-weight:700;
  text-transform:uppercase;letter-spacing:.08em;color:var(--subtle);padding:10px 12px;
  box-shadow:0 1px 0 var(--line);white-space:nowrap;cursor:pointer;user-select:none}
.tbl thead th:hover{color:var(--text)}
.tbl thead th.asc::after{content:" ↑"}
.tbl thead th.desc::after{content:" ↓"}
.tbl td{padding:11px 12px;border-bottom:1px solid var(--line2);vertical-align:top}
.tbl tr:last-child td{border-bottom:none}
.tbl tbody tr{cursor:pointer;transition:background .12s}
.tbl tbody tr:hover{background:color-mix(in srgb,var(--accent) 5%,transparent)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:700;background:var(--chip);color:var(--muted)}
.badge.agent{background:color-mix(in srgb,var(--accent2) 14%,transparent);color:var(--accent2)}
.badge.model{background:color-mix(in srgb,var(--accent3) 12%,transparent);color:var(--accent3)}
.badge.latest{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--accent)}
.tbl tbody tr.latest td{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.tbl tbody tr.latest td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.tbl .t{font-weight:700;line-height:1.35;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:break-word}
.tbl .badges{display:flex;gap:4px;flex-wrap:wrap;margin-top:5px}
.tbl .badges.mix{flex-direction:column;align-items:flex-start;gap:4px;margin-top:0}
.tbl .badges .badge{display:inline-block;margin:0;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill.accent{border-color:color-mix(in srgb,var(--accent) 45%,transparent)}

/* activity heatmap (GitHub/Codex-style: one cell per day, 52 weeks) */
.act-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:14px}
.act-head .sum{font-size:12.5px;color:var(--muted)}
.act-head .sum b{color:var(--text);font-family:var(--mono);font-weight:700}
.act-scroll{overflow-x:auto;padding-bottom:6px}
.act-grid{display:grid;grid-template-columns:auto 1fr;gap:8px 10px;min-width:640px}
.act-months{display:flex;grid-column:2;height:16px;font-size:9.5px;color:var(--subtle);white-space:nowrap}
.act-months span{padding-top:2px;overflow:hidden}
.act-body{display:flex;gap:8px}
.act-labels{display:flex;flex-direction:column;justify-content:space-around;width:26px;font-size:9px;color:var(--subtle);text-align:right}
.act-cols{display:flex;gap:3px}
.act-col{display:flex;flex-direction:column;gap:3px}
.act-cell{appearance:none;border:none;width:12px;height:12px;border-radius:3px;background:var(--chip);cursor:pointer;padding:0;transition:transform .08s,outline-color .08s}
.act-cell:hover{transform:scale(1.35)}
.act-cell:focus-visible{outline:2px solid var(--accent);outline-offset:1px;transform:scale(1.35)}
.act-cell.today{outline:1px solid var(--text);outline-offset:1px}
.act-cell.dim{opacity:.22}
.act-legend{display:flex;align-items:center;gap:5px;margin-top:12px;font-size:10.5px;color:var(--subtle)}
.act-legend .sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.act-legend .sw.e{background:var(--chip)}

/* period presets */
.presets{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:16px}
.presets .pl{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-right:4px}
.pbtn{appearance:none;background:var(--panel);border:1px solid var(--line);border-radius:999px;color:var(--muted);
  font-family:var(--mono);font-size:11px;font-weight:600;padding:5px 12px;cursor:pointer;transition:.15s}
.pbtn:hover{color:var(--text);border-color:var(--subtle)}
.pbtn.active{background:color-mix(in srgb,var(--accent) 14%,transparent);border-color:var(--accent);color:var(--accent)}
.pbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

/* KPI deltas */
.delta{font-family:var(--mono);font-size:11px;font-weight:700;margin-left:8px;padding:1px 7px;border-radius:999px}
.delta.up{color:var(--good);background:color-mix(in srgb,var(--good) 12%,transparent)}
.delta.down{color:var(--bad);background:color-mix(in srgb,var(--bad) 12%,transparent)}
.delta.flat{color:var(--subtle);background:var(--chip)}

/* export menu */
.export-wrap{position:relative}
.expmenu{position:absolute;right:0;top:calc(100% + 6px);z-index:50;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;box-shadow:var(--shadow);padding:5px;display:none;min-width:150px}
.expmenu.open{display:block}
.expmenu button{display:block;width:100%;text-align:left;background:none;border:none;color:var(--text);
  font-family:var(--sans);font-size:13px;padding:8px 11px;border-radius:7px;cursor:pointer}
.expmenu button:hover{background:var(--chip)}
.expmenu button small{display:block;color:var(--subtle);font-size:11px}

/* toasts */
.toasts{position:fixed;right:18px;bottom:18px;z-index:120;display:flex;flex-direction:column;gap:8px;max-width:340px}
.toast{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;
  box-shadow:var(--shadow);padding:11px 14px;font-size:12.5px;color:var(--text);animation:up .2s ease;line-height:1.45}
.toast.ok{border-left-color:var(--good)}
.toast.err{border-left-color:var(--bad)}

/* a11y */
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.tbl tbody tr:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
#search{outline:none}
#search:focus-visible{border-color:var(--accent)}
@media (prefers-reduced-motion: reduce){
  *{animation:none!important;transition:none!important}
  html{scroll-behavior:auto}
}
/* tooltip */
.tip{position:fixed;z-index:100;pointer-events:none;display:none;max-width:280px;
  background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 13px;
  font-family:var(--sans);font-size:12px;line-height:1.55;color:var(--muted);
  box-shadow:var(--shadow);backdrop-filter:blur(8px)}
.tip b{color:var(--text);font-family:var(--mono);font-weight:700}
.tip .thint{display:block;margin-top:6px;font-size:10.5px;color:var(--accent);font-weight:600}
.tip .trow{display:flex;justify-content:space-between;gap:16px}
.tip .trow b{color:var(--text)}

/* modal */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);backdrop-filter:blur(4px);z-index:60;display:none;
  align-items:flex-end;justify-content:center}
.overlay.open{display:flex}
.modal{background:var(--panel);border:1px solid var(--line);border-top-left-radius:18px;border-top-right-radius:18px;
  width:100%;max-width:820px;max-height:88vh;overflow:auto;padding:24px 28px 34px;box-shadow:var(--shadow);
  animation:up .22s ease}
@keyframes up{from{transform:translateY(26px);opacity:0}to{transform:none;opacity:1}}
.modal-x{float:right;background:var(--chip);border:1px solid var(--line);color:var(--muted);border-radius:8px;
  width:30px;height:30px;cursor:pointer;font-size:14px}
.modal-x:hover{color:var(--text)}
.tabs{display:flex;gap:8px;margin:18px 0 -34px;position:relative;z-index:2}
.wrap.has-tabs .hero{padding-top:18px}
.tab{appearance:none;background:var(--panel);border:1px solid var(--line);border-bottom:none;border-radius:10px 10px 0 0;
  padding:9px 18px;font-family:var(--sans);font-size:12.5px;font-weight:700;color:var(--muted);cursor:pointer;transition:.15s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--text);border-color:var(--accent);box-shadow:0 -2px 0 var(--accent) inset;background:var(--panel2)}
.banner{background:color-mix(in srgb,var(--bad) 10%,transparent);border:1px solid color-mix(in srgb,var(--bad) 35%,transparent);
  color:var(--bad);border-radius:10px;padding:10px 14px;margin-top:18px;font-size:12.5px;display:flex;gap:10px;align-items:center}
.banner b{font-weight:700}
.banner button{margin-left:auto;flex-shrink:0}
.settings-form{display:grid;gap:16px;margin-top:18px}
.srow{display:grid;gap:7px}
.srow label{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle)}
.srow input[type=text],.srow input[type=password],.srow input[type=number],.srow select{
  background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 12px;font-family:var(--sans);
  font-size:13px;color:var(--text);outline:none;width:100%}
.srow input:focus,.srow select:focus{border-color:var(--accent)}
.srow .hint{font-size:11.5px;color:var(--subtle);text-transform:none;letter-spacing:0;font-weight:500}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--bg);width:fit-content}
.segbtn{appearance:none;border:none;background:transparent;padding:8px 14px;font-family:var(--sans);font-size:12.5px;font-weight:600;color:var(--muted);cursor:pointer}
.segbtn.active{background:var(--accent);color:#fff}
.sbtns{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#set-status{font-family:var(--mono);font-size:11px;color:var(--muted)}
.prompt{background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:10px;
  padding:11px 14px;margin-bottom:10px;font-size:13px;color:var(--muted);line-height:1.55;white-space:pre-wrap;word-break:break-word}
.mmeta{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 4px}
.empty{color:var(--subtle);text-align:center;padding:26px;font-size:13px}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:var(--line);border-radius:8px;border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:var(--subtle)}
/* source tabs: OpenCode | Codex */
.source-tabs{display:flex;gap:8px;margin:22px 0 0;position:relative;z-index:2}
.stab{appearance:none;background:var(--panel);border:1px solid var(--line);border-radius:10px 10px 0 0;
  padding:9px 20px;font-family:var(--sans);font-size:13px;font-weight:700;color:var(--muted);cursor:pointer;transition:.15s}
.stab:hover{color:var(--text)}
.stab.active{color:var(--text);border-color:var(--accent);box-shadow:0 -2px 0 var(--accent) inset;background:var(--panel2)}
.stab .cnt{font-family:var(--mono);font-size:10.5px;font-weight:600;color:var(--subtle);margin-left:7px}
.stab.active .cnt{color:var(--accent)}
[role="tabpanel"][hidden]{display:none!important}
.rbanner{background:color-mix(in srgb,var(--cache) 10%,transparent);border:1px solid color-mix(in srgb,var(--cache) 35%,transparent);
  color:var(--text);border-radius:10px;padding:10px 14px;margin-top:18px;font-size:12.5px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.rbanner b{font-weight:700}
.status-ok{color:var(--good);font-weight:700}
.status-err{color:var(--bad);font-weight:700}
.free-tag{font-size:9.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--accent2);
  border:1px solid color-mix(in srgb,var(--accent2) 40%,transparent);border-radius:5px;padding:1px 5px;margin-left:7px;vertical-align:1px}
/* token-first additions */
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:18px}
.sum-card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}
.sum-card .sl{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--subtle)}
.sum-card .sv{font-family:var(--mono);font-size:20px;font-weight:700;letter-spacing:-.02em;margin-top:4px;font-variant-numeric:tabular-nums}
.sum-card .ss{font-size:11px;color:var(--muted);margin-top:2px;font-variant-numeric:tabular-nums}
.records{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.rec{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.rec .rl{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--subtle)}
.rec .rv{font-family:var(--mono);font-size:18px;font-weight:700;margin-top:6px}
.rec .rs{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.5}
.model-row{display:grid;grid-template-columns:minmax(140px,220px) 1fr auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line2)}
.model-row:last-child{border-bottom:none}
.model-row .mn{font-size:12.5px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.model-row .ms{font-size:11px;color:var(--subtle);font-family:var(--mono)}
.model-row .mbar{height:12px;background:var(--chip);border-radius:7px;overflow:hidden;display:flex}
.model-row .mbar i{height:100%;display:block}
.model-row .mv{font-family:var(--mono);font-size:12px;font-weight:700;text-align:right;white-space:nowrap}
.model-row.clickable{cursor:pointer;border-radius:8px}
.model-row.clickable:hover{background:color-mix(in srgb,var(--accent) 4%,transparent)}
.model-row.active{outline:2px solid var(--accent);outline-offset:-2px}
/* compact leaderboard rows (file/subsystem/commit panels) */
.crows .model-row{grid-template-columns:minmax(120px,180px) 1fr auto;gap:10px;padding:6px 0}
.crows .model-row .mn{font-size:12px}
.crows .model-row .ms{font-size:10.5px}
.crows .model-row .mbar{height:9px;border-radius:5px}
.crows .model-row .mv{font-size:11.5px}
.more-btn{display:block;width:100%;margin-top:8px;background:none;border:1px solid var(--line);border-radius:9px;
  padding:7px 10px;font-family:var(--sans);font-size:12px;font-weight:600;color:var(--muted);cursor:pointer}
.more-btn:hover{color:var(--text);border-color:var(--subtle)}
.hbar-row{display:flex;align-items:flex-end;gap:3px;height:90px;margin-top:6px}
.hbar{flex:1;background:linear-gradient(180deg,var(--accent),var(--accent2));border-radius:3px 3px 0 0;min-height:2px;cursor:pointer}
.hbar:hover{filter:brightness(1.15)}
.pbtn[aria-pressed="true"]{background:color-mix(in srgb,var(--accent) 14%,transparent);border-color:var(--accent);color:var(--accent)}
@media(max-width:700px){.model-row{grid-template-columns:1fr;gap:6px}.model-row .mv{text-align:left}}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-in">
    <div class="brand"><img class="brand-logo" src="/logo.png" alt="">OpenCode<small>· usage</small></div>
    <div class="spacer"></div>
    <span id="status"></span>
    <span id="src-status" style="font-size:11px;opacity:.75;margin-left:10px;max-width:46ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></span>
    <button class="tbtn" id="theme">◐&nbsp; Light</button>
    <span class="export-wrap"><button class="tbtn" id="export">⤓ Export</button>
      <div class="expmenu" id="expmenu">
        <button data-x="json">JSON <small>full stats payload</small></button>
        <button data-x="csv">CSV <small>per-day usage table</small></button>
        <button data-x="csvc">CSV <small>commit windows · not additive</small></button>
      </div></span>
    <button class="tbtn" id="refresh">⟳ Refresh</button>
  </div>
</div>
<div class="wrap" id="auth-lock" hidden style="margin-top:18px"><div class="card"><b>Authorization required — this tab has no working token, so the page stays empty.</b> Every launcher start mints a fresh token: old tabs and plain <span class="mono">127.0.0.1:8765</span> bookmarks never work. Open the address printed by the launcher (it carries <span class="mono">#token=…</span> in the fragment), or click the desktop shortcut again and use the tab it opens.</div></div>

<div class="wrap">
  <div class="source-tabs" role="tablist" aria-label="Data source">
    <button class="stab active" id="tab-opencode" role="tab" aria-selected="true" aria-controls="view-opencode">OpenCode<span class="cnt" id="tab-opencode-cnt"></span></button>
    <button class="stab" id="tab-router" role="tab" aria-selected="false" aria-controls="view-router">Codex<span class="cnt" id="tab-router-cnt"></span></button>
  </div>
  <div id="view-opencode" role="tabpanel" aria-labelledby="tab-opencode">
  <section class="hero">
    <div class="eyebrow" id="range"></div>
    <h1 id="headline"></h1>
    <div class="pills" id="pills"></div>
    <section class="summary" id="summary" aria-label="Token summary"></section>
    <div class="presets" id="presets" role="group" aria-label="Time range">
      <span class="pl">Period</span>
      <button class="pbtn active" data-r="all">All time</button>
      <button class="pbtn" data-r="7">7 days</button>
      <button class="pbtn" data-r="30">30 days</button>
      <button class="pbtn" data-r="90">90 days</button>
      <button class="pbtn" data-r="365">1 year</button>
      <span class="pl" style="margin-left:14px">View</span>
      <button class="pbtn" id="split-btn" aria-pressed="false" title="Split Output into Output + Reasoning">Split reasoning: off</button>
      <button class="pbtn" id="share-btn" aria-pressed="false" title="Show 100% share instead of absolute tokens">Share: off</button>
    </div>
  </section>

  <section class="kpis" id="kpis"></section>

  <h2 class="sec">Records <span class="hint">personal bests · token-only</span></h2>
  <section class="records" id="records"></section>

  <h2 class="sec">Notes on your usage <span class="hint" id="notes-hint">generated from your local data</span></h2>
  <section class="insights" id="insights"></section>

  <h2 class="sec" id="sec-tokens">Tokens per day <span class="hint">input · output incl. reasoning · cache · click a day to filter</span></h2>
  <div class="card chart-card">
    <div class="ctitle">Tokens per day <small id="tok-sub">stacked absolute</small></div>
    <div id="tok-chart"></div>
    <div class="legend" id="tok-legend"></div>
  </div>
  <div class="grid2" style="margin-top:14px">
    <div class="card chart-card">
      <div class="ctitle">Tokens by file / subsystem <small id="file-sub">equal-split · click to filter</small></div>
      <div class="presets" style="margin:0 0 10px" role="group" aria-label="Files or subsystems">
        <button class="pbtn active" data-fs="file">Files</button>
        <button class="pbtn" data-fs="sub">Subsystems</button>
      </div>
      <div id="file-chart" class="crows"></div>
    </div>
    <div class="card chart-card">
      <div class="ctitle">Usage in the 24h before each commit <small id="commit-sub">project-matched window · not additive · click to filter</small></div>
      <div class="presets" style="margin:0 0 10px" role="group" aria-label="Commit order">
        <button class="pbtn active" data-cs="toks">By tokens</button>
        <button class="pbtn" data-cs="recent">Recent</button>
      </div>
      <div id="commit-chart" class="crows"></div>
      <div class="hint" id="commit-note">Project-matched time window; windows overlap; rows must not be summed; not commit cost.</div>
    </div>
  </div>

  <h2 class="sec" id="sec-env">Tokens by model <span class="hint">universal comparison · click a model to filter</span></h2>
  <div class="card" id="model-card"><div id="models"></div><div class="legend" id="model-legend"></div></div>
  <div class="grid2" style="margin-top:14px">
    <div class="card"><div class="ctitle">Projects <small>token leaderboard</small></div><div class="chips" id="projects"></div></div>
    <div class="card"><div class="ctitle">Agents <small>sessions · tokens</small></div><div class="chips" id="agents"></div></div>
  </div>

  <h2 class="sec" id="sec-sessions">Sessions <span class="hint" id="sess-hint"></span></h2>
  <div class="card">
    <div class="tbl-tools">
      <input class="search" id="search" placeholder="Search sessions…" aria-label="Search sessions">
      <select class="filter" id="agent-f"><option value="">All agents</option></select>
      <select class="filter" id="model-f"><option value="">All models</option></select>
      <span class="badge" id="sess-count"></span>
    </div>
    <div id="fbar" class="fbar" style="display:none"></div>
    <div class="tbl-scroll">
      <table class="tbl" id="tbl">
        <thead><tr>
          <th data-k="title">Session</th><th>Model</th><th data-k="time_created">Started</th>
          <th class="num" data-k="tokens_input">Input</th><th class="num" data-k="tokens_output">Output</th>
          <th class="num" data-k="tokens_cache">Cache</th><th class="num" data-k="tokens">Total</th>
          <th class="num" data-k="msgs">Msgs</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <h2 class="sec" id="sec-agcfg">Agent configuration <span class="hint" id="cfg-hint">definitions · project + builtins</span></h2>
  <div class="card">
    <div class="chips" id="cfg-chips" style="margin-bottom:10px"></div>
    <div class="tbl-scroll">
      <div class="tiles" id="cfg-tiles"></div>
      <div id="tile-detail"></div>
    </div>
  </div>

  <h2 class="sec" id="sec-subagents">Subagents <span class="hint" id="sub-hint">child &amp; related sessions · Based on session updates; not execution status</span></h2>
  <div class="card">
    <div class="chips" id="subagents-chips" style="margin-bottom:10px"></div>
    <div class="chips" id="subagents-all" style="margin-bottom:10px"></div>
    <div class="tbl-scroll">
      <table class="tbl" id="subtbl">
        <thead><tr>
          <th>Activity</th><th>Child session</th><th>Agent</th><th>Parent session</th><th>Started</th>
          <th class="num">Total</th><th class="num">Msgs</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <h2 class="sec" id="sec-graph">Team graph <span class="hint" id="graph-hint">parent -&gt; child</span></h2>
  <div class="card"><div id="graph">loading...</div></div>

  <h2 class="sec" id="sec-browse">Session browser <span class="hint">click row to inspect</span></h2>
  <div class="card">
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <input id="ss-q" placeholder="search: title / directory / id" style="flex:2">
      <input id="ss-agent" placeholder="agent" style="flex:1">
      <input id="ss-model" placeholder="model" style="flex:1">
    </div>
    <div class="tbl-scroll"><table class="tbl" id="sstbl"><thead><tr><th>Session</th><th>Agent</th><th>Model</th><th>Directory</th><th>Start</th></tr></thead><tbody></tbody></table></div>
    <div id="insp" class="tile-detail" hidden></div>
  </div>

  <h2 class="sec" id="sec-proj">Projects <span class="hint">project table + session counts</span></h2>
  <div class="card"><div class="tbl-scroll"><table class="tbl" id="projtbl"><thead><tr><th>Project</th><th>Directory</th><th>VCS</th><th class="num">Sessions</th><th>Last activity</th></tr></thead><tbody></tbody></table></div></div>

  <h2 class="sec" id="sec-sig">Signals <span class="hint">read-only health checks</span></h2>
  <div class="card"><div class="chips" id="sig-chips">loading...</div></div>

  <h2 class="sec" id="sec-heat">Activity <span class="hint">last 52 weeks · token intensity · click to filter</span></h2>
  <div class="card">
    <div class="act-head"><span class="sum" id="act-summary"></span></div>
    <div class="act-scroll"><div id="act"></div></div>
    <div class="act-legend" id="act-legend"></div>
  </div>
  </div><!-- /view-opencode -->
  <div id="view-router" role="tabpanel" aria-labelledby="tab-router" hidden>
  <section class="hero" style="padding-top:26px">
    <div class="eyebrow" id="r-range"></div>
    <h1 id="r-headline"></h1>
    <div class="pills" id="r-pills"></div>
    <section class="summary" id="r-summary" aria-label="Codex token summary"></section>
    <div class="presets" id="r-presets" role="group" aria-label="Codex time range">
      <span class="pl">Period</span>
      <button class="pbtn active" data-rr="all">All time</button>
      <button class="pbtn" data-rr="7">7 days</button>
      <button class="pbtn" data-rr="30">30 days</button>
      <button class="pbtn" data-rr="90">90 days</button>
      <button class="pbtn" data-rr="365">1 year</button>
      <span class="pl" style="margin-left:14px">View</span>
      <button class="pbtn" id="r-split-btn" aria-pressed="false" title="Split Output into Output + Reasoning">Split reasoning: off</button>
      <button class="pbtn" id="r-share-btn" aria-pressed="false" title="Show 100% share instead of absolute tokens">Share: off</button>
    </div>
  </section>
  <div class="rbanner" id="r-rate-banner" style="display:none"></div>
  <section class="kpis" id="r-kpis"></section>

  <h2 class="sec">Records <span class="hint">personal bests · token-only</span></h2>
  <section class="records" id="r-records"></section>

  <h2 class="sec">Notes on your usage <span class="hint" id="r-notes-hint">generated from your codex data</span></h2>
  <section class="insights" id="r-insights"></section>

  <h2 class="sec">Tokens per day <span class="hint">input · output · cache · all models</span></h2>
  <div class="card chart-card">
    <div class="ctitle">Tokens per day <small id="r-tok-sub">stacked absolute</small></div>
    <div id="r-tok-chart"></div>
    <div class="legend" id="r-tok-legend"></div>
  </div>
  <div class="grid2" style="margin-top:14px">
    <div class="card chart-card">
      <div class="ctitle">Tokens by file / subsystem <small id="r-file-sub">from local edit + write touches</small></div>
      <div class="presets" style="margin:0 0 10px" role="group" aria-label="Files or subsystems">
        <button class="pbtn active" data-rfs="file">Files</button>
        <button class="pbtn" data-rfs="sub">Subsystems</button>
      </div>
      <div id="r-file-chart" class="crows"></div>
    </div>
    <div class="card chart-card">
      <div class="ctitle">Usage in the 24h before each commit <small id="r-commit-sub">global window · not additive · time-matched</small></div>
      <div class="presets" style="margin:0 0 10px" role="group" aria-label="Commit order">
        <button class="pbtn active" data-rcs="toks">By tokens</button>
        <button class="pbtn" data-rcs="recent">Recent</button>
      </div>
      <div id="r-commit-chart" class="crows"></div>
      <div class="hint" id="r-commit-note">Global time window; may include other projects. Windows overlap. Rows must not be summed.</div>
    </div>
  </div>

  <h2 class="sec">Tokens by model <span class="hint">universal comparison · click a model to filter</span></h2>
  <div class="card" id="r-model-card"><div id="r-models"></div><div class="legend" id="r-model-legend"></div></div>
  <div class="grid2" style="margin-top:14px">
    <div class="card"><div class="ctitle">Providers <small>request leaderboard</small></div><div class="chips" id="r-providers"></div></div>
    <div class="card"><div class="ctitle">Status <small>ok · errors · click to filter</small></div><div class="chips" id="r-status"></div></div>
  </div>

  <h2 class="sec">Requests <span class="hint" id="r-req-hint"></span></h2>
  <div class="card">
    <div class="tbl-tools">
      <input class="search" id="r-search" placeholder="Filter by model or provider…" aria-label="Filter codex requests">
      <select class="filter" id="r-provider-f"><option value="">All providers</option></select>
      <select class="filter" id="r-model-f"><option value="">All models</option></select>
      <select class="filter" id="r-status-f"><option value="">All statuses</option><option value="ok">OK only</option><option value="err">Errors only</option><option value="unknown">Unknown only</option><option value="429">429 only</option></select>
      <span class="badge" id="r-req-count"></span>
    </div>
    <div id="r-fbar" class="fbar" style="display:none"></div>
    <div class="tbl-scroll">
      <table class="tbl" id="r-tbl">
        <thead><tr>
          <th>Time</th><th>Model</th><th>Status</th>
          <th class="num">Input</th><th class="num">Output</th>
          <th class="num">Cache</th><th class="num">Total</th><th class="num">ms</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <h2 class="sec">Codex sessions <span class="hint" id="cx-hint">from local rollout files</span></h2>
  <div class="card">
    <div class="tbl-scroll">
      <table class="tbl" id="cx-tbl">
        <thead><tr>
          <th>Session</th><th>Model</th><th>Directory</th>
          <th class="num">Msgs</th><th class="num">Tokens</th><th>Last activity</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <h2 class="sec">Activity <span class="hint">last 52 weeks · token intensity · click to filter</span></h2>
  <div class="card">
    <div class="act-head"><span class="sum" id="r-act-summary"></span></div>
    <div class="act-scroll"><div id="r-act"></div></div>
    <div class="act-legend" id="r-act-legend"></div>
  </div>
  </div><!-- /view-router -->
</div>

<div class="overlay" id="overlay"><div class="modal" id="modal" role="dialog" aria-modal="true" aria-label="Session details"></div></div>
<div class="tip" id="tip"></div>
<div class="toasts" id="toasts"></div>

<script>const S0 = __DATA__; const R0 = __ROUTER__;</script>
<script>
const $=id=>document.getElementById(id);
const ESC=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const BOLD=s=>{const ents=[];const t=s.replace(/&(?:#\d+|[a-z]+);/g,m=>{ents.push(m);return'@@'+String.fromCharCode(65+ents.length-1)+'@@';});
  return t.replace(/(\d[\d,]*(?:\.\d+)?)/g,'<b>$1</b>').replace(/@@[A-Z]+@@/g,m=>ents[m.charCodeAt(2)-65]);};
const F=n=>{const s=Math.round(Number(n)||0).toString();return s.replace(/\B(?=(\d{3})+(?!\d))/g,',');};
const FN=n=>{n=Number(n)||0;return n>=1e6?(n/1e6).toFixed(2)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(Math.round(n));};
const DT=t=>{if(!t)return'-';const d=new Date(t>1e12?t:t*1000);return d.toLocaleDateString('en-CA')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};
const DS=t=>{if(!t)return'';const d=new Date(t>1e12?t:t*1000);return d.toLocaleDateString('en-CA');};
const DUR=m=>{if(m==null)return'-';if(m<1)return'<1m';if(m<60)return Math.round(m)+'m';const h=Math.floor(m/60);return h+'h '+Math.round(m%60)+'m';};
const BURN=b=>{b=Number(b)||0;return b>=1000?(b/1000).toFixed(1)+'K/m':Math.round(b)+'/m';};
let S=null; if(typeof S0!=='undefined'&&S0){S=S0;}
let R=null; try{if(typeof R0!=='undefined'&&R0){R=R0;}}catch(_){R=null;}
let TAB='opencode';
let sortK='time_created', sortAsc=false;
let RANGE='all', SPLIT=false, SHARE=false;
let RRANGE='all', RSPLIT=false, RSHARE=false;
let FS='file', CS='toks', RFS='file', RCS='toks';
let FS_EXP=false, CS_EXP=false, RFS_EXP=false, RCS_EXP=false;
let currentRows=[];
let currentRRows=[];
const RFSTATE={day:null,model:null,provider:null,status:''};
const WID=sessionStorage.getItem('ocd-wid')||('w'+Math.random().toString(36).slice(2));
sessionStorage.setItem('ocd-wid',WID);
/* F2 access token: fragment-only (#token=...), memory + sessionStorage. */
let AUTH_TOKEN=null;let AUTH_FAILED=false;
try{
  var _hm=String(location.hash||'');
  var _mt=_hm.match(/token=([^&]+)/);
  if(_mt&&_mt[1]){AUTH_TOKEN=decodeURIComponent(_mt[1]);
    try{sessionStorage.setItem('ocd-token',AUTH_TOKEN);}catch(_){}
    try{history.replaceState(null,'',location.pathname+location.search);}catch(_){}}
  if(!AUTH_TOKEN){try{AUTH_TOKEN=sessionStorage.getItem('ocd-token')||null;}catch(_){AUTH_TOKEN=null;}}
}catch(_){AUTH_TOKEN=null;}
function authHeaders(extra){var h={'X-Window-Id':WID};if(AUTH_TOKEN)h['Authorization']='Bearer '+AUTH_TOKEN;if(extra)for(var k in extra)h[k]=extra[k];return h;}
function authFetch(url,opts){opts=opts||{};if(String(url).indexOf('/api/')!==0)return fetch(url,opts);
  opts.headers=authHeaders(opts.headers||{});return fetch(url,opts);}
/* F6d: shared JSON fetch. Auth comes from authFetch (token only for
   local /api/ URLs, never arbitrary hosts). The AbortController deadline
   covers receiving headers AND reading the whole body AND parsing it:
   the timer is cleared only in finally, after the parse. HTTP 200 with
   {error:...} / {ok:false} or invalid JSON is an explicit section error. */
async function fetchJson(url,opts){
  opts=opts||{};
  var ms=(window.__ocdTimeouts&&window.__ocdTimeouts.fetchMs)||10000;
  var started=Date.now();
  var ac=('AbortController' in window)?new AbortController():null;
  var fopts={};for(var k in opts)fopts[k]=opts[k];
  if(ac)fopts.signal=ac.signal;
  if(opts.signal&&ac){try{opts.signal.addEventListener('abort',function(){try{ac.abort();}catch(_){}});}catch(_){}}
  var timer=null;
  var deadline=new Promise(function(_,rej){timer=setTimeout(function(){rej(new Error('timeout after '+ms+' ms'));try{if(ac)ac.abort();}catch(_){}},ms);});
  try{
    var resp=await Promise.race([authFetch(url,fopts),deadline]);
    var text=await Promise.race([resp.text(),deadline]);
    if(Date.now()-started>ms)throw new Error('timeout after '+ms+' ms');
    if(resp.status===401)throw Object.assign(new Error('unauthorized'),{status:401});
    if(!resp.ok)throw Object.assign(new Error('HTTP '+resp.status),{status:resp.status});
    var data=null;
    if(text){try{data=JSON.parse(text);}catch(_){throw new Error('invalid JSON (status '+resp.status+')');}}
    if(data&&data.error)throw new Error('source error: '+data.error);
    if(data&&data.ok===false)throw new Error('source reported ok:false');
    return data;
  }finally{clearTimeout(timer);}
}
function showAuthLock(on){var el=document.getElementById('auth-lock');if(el)el.hidden=!on;
  var st=document.getElementById('status');if(on&&st)st.textContent='Authorization required — reopen via the launcher link';}
function noteAuthFailure(){if(AUTH_FAILED)return;AUTH_FAILED=true;showAuthLock(true);}
if(!AUTH_TOKEN){showAuthLock(true);}
const FSTATE={day:null,model:null,agent:null,file:null,sub:null,commit:null};
const tip=$('tip');
function tipShow(html,e){tip.innerHTML=html;tip.style.display='block';tipPos(e);}
function tipHide(){tip.style.display='none';}
function tipPos(e){
  const r=tip.getBoundingClientRect();
  let x=e.clientX+14,y=e.clientY+14;
  if(x+r.width>innerWidth-8)x=e.clientX-r.width-12;
  if(y+r.height>innerHeight-8)y=e.clientY-r.height-12;
  tip.style.left=x+'px';tip.style.top=y+'px';
}
function tipAt(el){
  const r=el.getBoundingClientRect();
  tip.style.display='block';
  const tw=tip.offsetWidth;
  let x=r.left+r.width/2-tw/2,y=r.bottom+8;
  if(y+tip.offsetHeight>innerHeight)y=r.top-tip.offsetHeight-8;
  tip.style.left=Math.max(8,Math.min(x,innerWidth-tw-8))+'px';tip.style.top=Math.max(8,y)+'px';
}
function toast(msg,ok){
  const t=document.createElement('div');
  t.className='toast'+(ok?' ok':ok===false?' err':'');
  t.textContent=msg;
  $('toasts').appendChild(t);
  setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),320);},3600);
}
/* theme */
const THEMES={light:{accent:'#a6761d',accent2:'#c9a84c',accent3:'#7a5c14',reason:'#8a6d1f',cache:'#c07f1a'},
              dark:{accent:'#c9a84c',accent2:'#f4d78d',accent3:'#d8ad44',reason:'#e8cf8f',cache:'#d99a26'}};
let theme=localStorage.getItem('ocd-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
document.documentElement.dataset.theme=theme;
const T=()=>THEMES[theme];
function syncThemeBtn(){$('theme').textContent=theme==='dark'?'☀ Light':'◐ Dark';}
syncThemeBtn();
$('theme').onclick=()=>{theme=theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;
  localStorage.setItem('ocd-theme',theme);syncThemeBtn();renderAll();renderRouter();};
/* svg */
const svg=(tag,at,html)=>'<'+tag+Object.entries(at).map(([k,v])=>' '+k+'="'+ESC(v)+'"').join('')+'>'+(html||'')+'</'+tag+'>';
const dayTotal=d=>(d.ti||0)+(d.to||0)+(d.tr||0)+(d.cache||0);
const outMerged=d=>(d.to||0)+(d.tr||0);
function dayTip(d){
  const tot=dayTotal(d);
  return '<b>'+d.date+'</b>'+
    '<span class="trow"><span>Input</span><b>'+FN(d.ti)+'</b></span>'+
    '<span class="trow"><span>Output incl. reasoning</span><b>'+FN(outMerged(d))+'</b></span>'+
    '<span class="trow"><span>Cache</span><b>'+FN(d.cache)+'</b></span>'+
    '<span class="trow"><span>Total</span><b>'+FN(tot)+'</b></span>'+
    '<span class="trow"><span>Sessions</span><b>'+d.sessions+'</b></span>'+
    '<span class="trow"><span>Messages</span><b>'+F(d.msgs)+'</b></span>';
}
function dayDim(d){return FSTATE.day&&FSTATE.day!==d.date?' dim':'';}
function stackChart(days){
  const t=T();
  const keys=SPLIT?[{p:'ti',c:t.accent,l:'Input'},{p:'to',c:t.accent2,l:'Output'},{p:'tr',c:t.reason,l:'Reasoning'},{p:'cache',c:t.cache,l:'Cache'}]
                   :[{p:'ti',c:t.accent,l:'Input'},{p:'out',c:t.accent2,l:'Output incl. reasoning'},{p:'cache',c:t.cache,l:'Cache'}];
  const val=(d,k)=>k.p==='out'?outMerged(d):(d[k.p]||0);
  const w=980,h=250,p=34;
  let max=1;
  if(SHARE){max=100;}
  else{max=Math.max(...days.map(d=>keys.reduce((a,k)=>a+val(d,k),0)),1);}
  const Y=v=>p+(1-v/max)*(h-2*p),X=i=>days.length>1?p+i*(w-2*p)/(days.length-1):p+(w-2*p)/2;
  const slot=(w-2*p)/Math.max(days.length,1);
  const sw=Math.min(slot*0.32,p*0.85);
  const step=Math.ceil(days.length/12)||1;
  let cells='',xl='';
  days.forEach((day,i)=>{
    const tot=keys.reduce((a,k)=>a+val(day,k),0);
    let acc=0;
    const x1=X(i)-sw,x2=X(i)+sw;
    keys.forEach(k=>{
      let v=val(day,k), y0,y1;
      if(SHARE){
        const pct=tot?v/tot*100:0;
        y1=Y(acc+((k===keys[0])?0:0)); // placeholder
        const share0=acc/tot*100||0;
        acc+=v;
        const share1=acc/tot*100||0;
        y0=Y(share1); y1=Y(share0);
        if(!v)return;
        const segTip=('<b>'+k.l+' · '+day.date+'</b><span class="trow"><span>Tokens</span><b>'+FN(v)+'</b></span>'+
          '<span class="trow"><span>Share</span><b>'+pct.toFixed(1)+'%</b></span>');
        cells+=svg('path',{d:'M'+x1.toFixed(1)+','+y0.toFixed(1)+' L'+x2.toFixed(1)+','+y0.toFixed(1)+' L'+x2.toFixed(1)+','+y1.toFixed(1)+' L'+x1.toFixed(1)+','+y1.toFixed(1)+' Z',
          fill:k.c,class:'inter'+dayDim(day),'data-tip':segTip,'data-f':'day|'+day.date});
      } else {
        if(!v)return;
        const segTip=('<b>'+k.l+' · '+day.date+'</b><span class="trow"><span>Tokens</span><b>'+FN(v)+'</b></span>'+
          '<span class="trow"><span>Share</span><b>'+(tot?Math.round(v/tot*100):0)+'%</b></span>');
        cells+=svg('path',{d:'M'+x1.toFixed(1)+','+Y(acc+v).toFixed(1)+' L'+x2.toFixed(1)+','+Y(acc+v).toFixed(1)+' L'+x2.toFixed(1)+','+Y(acc).toFixed(1)+' L'+x1.toFixed(1)+','+Y(acc).toFixed(1)+' Z',
          fill:k.c,class:'inter'+dayDim(day),'data-tip':segTip,'data-f':'day|'+day.date});
        acc+=v;
      }
    });
    cells+=svg('rect',{x:(X(i)-slot/2).toFixed(1),y:p,width:(slot*0.96).toFixed(1),height:(h-2*p).toFixed(1),fill:'transparent',
      class:'inter'+dayDim(day),'data-tip':dayTip(day),'data-f':'day|'+day.date});
    if(i%step===0)xl+=svg('text',{x:X(i).toFixed(1),y:h-p+17,'text-anchor':'middle',fill:'#8f7f5c','font-size':10},day.date.slice(5));
  });
  const sub=$('tok-sub');
  if(sub)sub.textContent=(SHARE?'100% share':'stacked absolute')+(SPLIT?' · reasoning split':' · output merged');
  return '<svg viewBox="0 0 '+w+' '+(h+10)+'" width="100%">'+cells+xl+'</svg>';
}
function shortPath(p){const seg=(p||'').split('/').filter(Boolean);return seg.length>2?'…/'+seg.slice(-2).join('/'):p;}
function fileRows(){
  // All-time backend aggregates, or a period recompute from recent sessions.
  if(RANGE==='all'&&!FSTATE.day)return {rows:(FS==='file'?(S.files||[]):(S.subsystems||[])),approx:false};
  const cut=RANGE==='all'?0:Date.now()-RANGE*86400000;
  const agg={};
  (S.sessions||[]).forEach(s=>{
    if(s.time_created<cut)return;
    if(FSTATE.day&&DS(s.time_created)!==FSTATE.day)return;
    const files=((S.session_files||{})[s.id])||[];
    if(!files.length)return;
    const share=(s.tokens_total||0)/files.length;
    files.forEach(fp=>{
      const key=FS==='file'?fp:(((S.file_subsys||{})[fp])||fp.split('/').filter(Boolean).slice(-2,-1)[0]||fp);
      const a=agg[key]||(agg[key]={toks:0,sess:0});
      a.toks+=share;a.sess+=1;
    });
  });
  const rows=Object.entries(agg).map(([k,a])=>[k,Math.round(a.toks*10)/10,a.sess,Math.round(a.toks/(a.sess||1))]);
  rows.sort((a,b)=>b[1]-a[1]);
  const approx=(S.totals&&S.totals.sessions||0)>(S.sessions||[]).length;
  return {rows:rows.slice(0,15),approx};
}
function renderFileChart(){
  const el=$('file-chart');if(!el)return;
  const {rows,approx}=fileRows();
  const sub=$('file-sub');
  if(sub)sub.textContent=(FS==='file'?'top files · equal-split':'top subsystems · top-level dir')+' · click to filter'+(approx?' · period from recent sessions':'');
  if(!rows.length){el.innerHTML='<div class="empty">No file touches yet.</div>';return;}
  const t=T(),totalAll=rows.reduce((a,r)=>a+(r[1]||0),0)||1;
  const max=Math.max(...rows.map(r=>r[1]||0),1);
  const shown=FS_EXP?rows:rows.slice(0,6);
  el.innerHTML=shown.map(r=>{
    const name=r[0],toks=r[1]||0,n=r[2]||0,avg=r[3]||0;
    const short=FS==='file'?shortPath(name):name;
    const key=FS==='file'?'file':'sub';
    const active=key==='file'?(FSTATE.file===name):(FSTATE.sub===name);
    const tip='<b>'+ESC(name)+'</b><span class=\'trow\'><span>Tokens</span><b>'+FN(toks)+' ('+(toks/totalAll*100).toFixed(1)+'% of top)</b></span>'+
      '<span class=\'trow\'><span>Sessions</span><b>'+n+'</b></span><span class=\'trow\'><span>Avg / session</span><b>'+FN(avg)+'</b></span>';
    return '<div class="model-row clickable'+(active?' active':'')+'" data-f="'+key+'|'+ESC(name)+'" data-tip="'+tip+'">'+
      '<div><div class="mn">'+ESC(short)+'</div><div class="ms">'+n+' sess · avg '+FN(avg)+'</div></div>'+
      '<div class="mbar"><i style="width:'+(toks/max*100).toFixed(1)+'%;background:'+(FS==='file'?t.accent:t.accent2)+'"></i></div>'+
      '<div class="mv">'+FN(toks)+'</div></div>';
  }).join('')+(rows.length>6?'<button class="more-btn" data-exp="fs">'+(FS_EXP?'Show less':'Show all '+rows.length)+' '+(FS_EXP?'▴':'▾')+'</button>':'');
}
function commitRows(){
  const cut=RANGE==='all'?0:Date.now()-RANGE*86400000;
  let rows=(S.commits||[]).filter(c=>{
    if(FSTATE.day&&c[2]!==FSTATE.day)return false;
    if(cut){
      const ms=Date.parse(c[2]+'T00:00:00');
      if(ms&&ms<cut-86400000)return false;
    }
    return true;
  });
  rows=rows.slice();
  if(CS==='recent')rows.sort((a,b)=>a[2]<b[2]?1:-1);
  else rows.sort((a,b)=>(b[7]||0)-(a[7]||0));
  return rows.slice(0,20);
}
function renderCommitChart(){
  const el=$('commit-chart');if(!el)return;
  const rows=commitRows();
  const sub=$('commit-sub');
  if(sub)sub.textContent='project-matched window · not additive · click to filter';
  if(!rows.length){el.innerHTML='<div class="empty">No git commits found in tracked worktrees.</div>';return;}
  const t=T();
  const max=Math.max(...rows.map(r=>r[7]||0),1);
  const shown=CS_EXP?rows:rows.slice(0,6);
  el.innerHTML=shown.map(r=>{
    const sha=r[0],msg=r[1]||'(no message)',date=r[2],proj=r[3],nf=r[4]||0,add=r[5]||0,del=r[6]||0,toks=r[7]||0,ns=r[8]||0;
    const tip='<b>'+ESC(msg)+'</b><span class=\'trow\'><span>SHA</span><b>'+ESC(sha.slice(0,12))+'</b></span>'+
      '<span class=\'trow\'><span>Date · project</span><b>'+date+' · '+ESC(proj)+'</b></span>'+
      '<span class=\'trow\'><span>Changed</span><b>'+nf+' files · +'+F(add)+' / -'+F(del)+'</b></span>'+
      '<span class=\'trow\'><span>Usage in the 24h before commit</span><b>'+FN(toks)+' · '+ns+' sessions</b></span>';
    return '<div class="model-row clickable'+(FSTATE.commit===sha?' active':'')+'" data-f="commit|'+sha+'" data-tip="'+tip+'">'+
      '<div><div class="mn">'+ESC(msg.length>44?msg.slice(0,44)+'…':msg)+'</div>'+
      '<div class="ms">'+sha.slice(0,7)+' · '+date+' · '+ESC(proj)+' · +'+F(add)+'/-'+F(del)+'</div></div>'+
      '<div class="mbar"><i style="width:'+Math.max(toks?2:0,toks/max*100).toFixed(1)+'%;background:'+t.accent2+'"></i></div>'+
      '<div class="mv">'+(toks?FN(toks):'-')+'</div></div>';
  }).join('')+(rows.length>6?'<button class="more-btn" data-exp="cs">'+(CS_EXP?'Show less':'Show all '+rows.length)+' '+(CS_EXP?'▴':'▾')+'</button>':'');
}
/* period helpers */
function cutDate(){
  if(RANGE==='all')return'';
  const d=new Date(Date.now()-RANGE*86400000);
  return d.toLocaleDateString('en-CA');
}
function inPeriod(day){const c=cutDate();return !c||day.date>=c;}
function periodDays(){return S.days.filter(inPeriod);}
function periodPrev(){
  const c=cutDate();if(!c)return[];
  const end=new Date();end.setDate(end.getDate()-RANGE);
  const start=new Date(end);start.setDate(start.getDate()-RANGE);
  const s=start.toLocaleDateString('en-CA'),e=end.toLocaleDateString('en-CA');
  return S.days.filter(d=>d.date>=s&&d.date<e);
}
function sumDays(list){
  const t={sessions:0,msgs:0,ti:0,to:0,tr:0,cache:0,total:0};
  list.forEach(d=>{t.sessions+=d.sessions;t.msgs+=d.msgs;t.ti+=d.ti;t.to+=d.to;t.tr+=d.tr;t.cache+=d.cache;t.total+=dayTotal(d);});
  return t;
}
function deltaPct(cur,prev){if(prev==null)return null;if(prev===0)return cur>0?100:0;return Math.round((cur-prev)/prev*100);}
function deltaTag(cur,prev){const p=deltaPct(cur,prev);if(p==null)return'';
  return p===0?'<span class="delta flat">±0%</span>':p>0?'<span class="delta up">▲ '+p+'%</span>':'<span class="delta down">▼ '+Math.abs(p)+'%</span>';}
/* renderers */
function renderAll(){
  if(!S)return;
  renderHero();renderSummary();renderKpis();renderRecords();renderInsights();renderCharts();renderModels();renderProjAgents();renderAct();renderFilterBar();renderSessions();
}
function renderHero(){
  const periodLbl=RANGE==='all'?'ALL TIME':(RANGE/30>=1?RANGE/30+' MO':RANGE+' DAYS');
  $('range').textContent='LOCAL · RECORDED '+S.range.toUpperCase()+' · LAST '+periodLbl+' · REFRESHES AUTOMATICALLY';
  $('headline').innerHTML=BOLD(ESC(S.insights[0]||'No activity yet.'));
  const last=[...S.sessions].sort((a,b)=>b.time_created-a.time_created)[0];
  const ltitle=last?ESC(last.title):'-';
  const nModels=S.models.length;
  $('pills').innerHTML=[
    ['Total tokens',FN(S.totals.tokens_total||0)],
    ['Models',nModels>1?nModels+' models':ESC(((S.models[0]||['-'])[0]+'').split('/').pop())],
    ['Latest',ltitle.length>36?ltitle.slice(0,36)+'…':ltitle]]
    .map(([k,v])=>'<span class="pill">'+ESC(k)+' <b style="color:var(--text)">'+v+'</b></span>').join('');
}
function periodTotal(n){
  const now=new Date();now.setHours(0,0,0,0);
  const start=new Date(now);start.setDate(start.getDate()-(n-1));
  const s=start.toLocaleDateString('en-CA');
  const list=S.days.filter(d=>d.date>=s);
  return sumDays(list);
}
function renderSummary(){
  const el=$('summary');if(!el)return;
  const t7=periodTotal(7),t30=periodTotal(30),t1=periodTotal(1);
  const prev7=(()=>{const end=new Date();end.setDate(end.getDate()-7);const st=new Date(end);st.setDate(st.getDate()-7);
    return sumDays(S.days.filter(d=>d.date>=st.toLocaleDateString('en-CA')&&d.date<end.toLocaleDateString('en-CA')));})();
  const cards=[
    ['Today',t1.total,'','tokens'],
    ['Last 7 days',t7.total,deltaTag(t7.total,prev7.total),'tokens'],
    ['Last 30 days',t30.total,'','tokens'],
    ['All time',S.totals.tokens_total||0,'','tokens'],
  ];
  el.innerHTML=cards.map(([l,v,tag])=>'<div class="sum-card"><div class="sl">'+l+'</div><div class="sv">'+FN(v)+' '+tag+'</div><div class="ss">tokens</div></div>').join('');
}
function renderKpis(){
  const t=S.totals,c=T();
  const cur=RANGE==='all'?null:sumDays(periodDays());
  const prev=RANGE==='all'?null:sumDays(periodPrev());
  const pv=prev||{sessions:0,msgs:0,ti:0,to:0,tr:0,cache:0,total:0};
  const tok=cur?{ti:cur.ti,out:cur.to+cur.tr,tr:cur.tr,ca:cur.cache}:{ti:t.tokens_input,out:t.tokens_output+t.tokens_reasoning,tr:t.tokens_reasoning,ca:t.tokens_cache_read};
  const total=tok.ti+tok.out+tok.ca;
  const pct=v=>total?Math.round(v/total*100):0;
  const row=(lbl,v,color,sub)=>'<div class="krow"><span class="lbl"><i class="dot" style="background:'+color+'"></i>'+lbl+
    (sub?' <span class="dim">'+sub+'</span>':'')+'</span><b>'+v+'</b></div>';
  const sessions=cur?cur.sessions:t.sessions;
  const msgs=cur?cur.msgs:t.messages;
  const tokTag=cur?deltaTag(total,pv.total):'';
  const actTag=cur?deltaTag(sessions+msgs,pv.sessions+pv.msgs):'';
  const hitRate=(()=>{const d=(tok.ti||0)+(tok.ca||0);return d?Math.round(tok.ca/d*100):0;})();
  const tokCard='<div class="kpi"><h3>Tokens '+(SPLIT?'(reasoning split)':'(output merged)')+'</h3><div class="big">'+FN(total)+' <small>total</small>'+tokTag+'</div>'+
    '<div class="tokbar">'+[[tok.ti,c.accent],[tok.out-(SPLIT?tok.tr:0),c.accent2]].concat(SPLIT?[[tok.tr,c.reason]]:[]).concat([[tok.ca,c.cache]]).map(([v,k])=>'<i style="width:'+pct(v)+'%;background:'+k+'"></i>').join('')+'</div>'+
    row('Input',FN(tok.ti),c.accent,pct(tok.ti)+'%')+row('Output incl. reasoning',FN(tok.out),c.accent2,pct(tok.out)+'%')+
    (SPLIT?row('&nbsp;&nbsp;↳ reasoning',FN(tok.tr),c.reason,pct(tok.tr)+'%'):'')+
    row('Cache',FN(tok.ca),c.cache,pct(tok.ca)+'%')+'</div>';
  const avgSess=sessions?Math.round(total/sessions):0;
  const tpm=msgs?Math.round(total/msgs):0;
  const effCard='<div class="kpi"><h3>Cache &amp; efficiency</h3><div class="big">'+hitRate+'% <small>cache hit</small></div>'+
    row('Cached tokens',FN(tok.ca),c.cache)+row('Avg / session',FN(avgSess),c.accent)+row('Tokens / message',FN(tpm),c.accent2)+
    row('Avg tokens/session (all)',FN(t.avg_tokens_per_session||0),'var(--subtle)')+'</div>';
  const topSub=((S.subsystems||[])[0])||null;
  const burnCard='<div class="kpi"><h3>Burn &amp; activity</h3><div class="big">'+F(sessions)+' <small>sessions</small>'+actTag+'</div>'+
    row('Messages',F(msgs),c.accent)+row('Top subsystem',topSub?ESC(topSub[0]):'-',c.accent2,'all-time')+
    row('Avg session',DUR(t.avg_duration_min),'var(--subtle)','all-time')+'</div>';
  $('kpis').innerHTML=tokCard+effCard+burnCard;
}
function humanize(n){n=Number(n)||0;const words=Math.round(n/1.3);if(words>1e6)return (words/1e6).toFixed(1)+'M words';if(words>1e3)return (words/1e3).toFixed(0)+'K words';return words+' words';}
function renderRecords(){
  const el=$('records');if(!el||!S.records)return;
  const r=S.records;
  const cards=[];
  if(r.biggest_day)cards.push(['Biggest day',FN(r.biggest_day.total),r.biggest_day.date+' · '+r.biggest_day.sessions+' sessions · '+humanize(r.biggest_day.total)]);
  if(r.biggest_session)cards.push(['Biggest session',FN(r.biggest_session.total),(r.biggest_session.title||'Untitled').slice(0,48)+' · '+((r.biggest_session.provider||'')?r.biggest_session.provider+' · ':'')+((r.biggest_session.model||'')+'').split('/').pop()]);
  if(r.fastest_burn)cards.push(['Fastest burn',BURN(r.fastest_burn.burn),(r.fastest_burn.title||'').slice(0,48)]);
  if(r.streaks)cards.push(['Streaks',r.streaks.current+'d now', 'longest '+r.streaks.longest+'d · '+humanize(S.totals.tokens_total)+' total ≈ '+(Math.round((S.totals.tokens_total||0)/750000*10)/10)+' novels']);
  el.innerHTML=cards.map(([l,v,s])=>'<div class="rec"><div class="rl">'+l+'</div><div class="rv">'+ESC(v)+'</div><div class="rs">'+ESC(s)+'</div></div>').join('')||'<div class="empty">No records yet.</div>';
}
function renderInsights(){
  $('notes-hint').textContent=RANGE==='all'?'generated from your local data':'all-time notes · charts/tables follow the period filter';
  $('insights').innerHTML=S.insights.slice(1,5).map(ins=>'<div class="in"><p>'+BOLD(ESC(ins))+'</p></div>').join('');
}
function renderCharts(){
  const d=periodDays();
  if(!d.length){$('tok-chart').innerHTML='<div class="empty">Nothing in this period.</div>';$('file-chart').innerHTML='';$('commit-chart').innerHTML='';return;}
  $('tok-chart').innerHTML=stackChart(d);
  const t=T();
  const keys=SPLIT?[{l:'Input',c:t.accent},{l:'Output',c:t.accent2},{l:'Reasoning',c:t.reason},{l:'Cache',c:t.cache}]:[{l:'Input',c:t.accent},{l:'Output incl. reasoning',c:t.accent2},{l:'Cache',c:t.cache}];
  $('tok-legend').innerHTML=keys.map(k=>'<span><i style="background:'+k.c+'"></i>'+k.l+'</span>').join('')+(SHARE?'<span>100% share mode</span>':'');
  renderFileChart();
  renderCommitChart();
}
function modelShort(m){return (m||'-').split('/').pop();}
function renderModels(){
  const el=$('models');if(!el)return;
  const t=T();
  const max=Math.max(...S.models.map(m=>m[2]||0),1);
  const totalAll=S.models.reduce((a,m)=>a+(m[2]||0),0)||1;
  if(!S.models.length){el.innerHTML='<div class="empty">No models yet.</div>';return;}
  el.innerHTML=S.models.map(m=>{
    const name=m[0],n=m[1],tot=m[2]||0,ti=m[3]||0,to=m[4]||0,tr=m[5]||0,ca=m[6]||0,avg=m[8]||0,hr=m[9]||0;
    const prov=name.indexOf('/')>=0?name.split('/')[0]:'';
    const out=to+tr;
    const segs=SPLIT?[[ti,t.accent],[to,t.accent2],[tr,t.reason],[ca,t.cache]]:[[ti,t.accent],[out,t.accent2],[ca,t.cache]];
    const bar=segs.map(([v,c])=>'<i style="width:'+(tot?v/tot*100:0)+'%;background:'+c+'"></i>').join('');
    const tipHtml=('<b>'+ESC(modelShort(name))+'</b>'+(prov?'<span class=\'trow\'><span>Provider</span><b>'+ESC(prov)+'</b></span>':'')+'<span class=\'trow\'><span>Sessions</span><b>'+n+'</b></span>'+
      '<span class=\'trow\'><span>Total tokens</span><b>'+FN(tot)+' ('+(tot/totalAll*100).toFixed(1)+'%)</b></span>'+
      '<span class=\'trow\'><span>Input / Output / Cache</span><b>'+FN(ti)+' / '+FN(out)+' / '+FN(ca)+'</b></span>'+
      (SPLIT?'<span class=\'trow\'><span>Reasoning</span><b>'+FN(tr)+'</b></span>':'')+
      '<span class=\'trow\'><span>Avg / session</span><b>'+FN(avg)+'</b></span>'+
      '<span class=\'trow\'><span>Cache hit</span><b>'+hr+'%</b></span>');
    return '<div class="model-row clickable'+(FSTATE.model===name?' active':'')+'" data-f="model|'+ESC(name)+'" data-tip="'+tipHtml+'">'+
      '<div><div class="mn">'+ESC(modelShort(name))+'</div><div class="ms">'+(prov?ESC(prov)+' · ':'')+n+' sess · '+(tot/totalAll*100).toFixed(1)+'% · avg '+FN(avg)+'</div></div>'+
      '<div class="mbar">'+bar+'</div>'+
      '<div class="mv">'+FN(tot)+'</div></div>';
  }).join('');
  $('model-legend').innerHTML='<span><i style="background:'+t.accent+'"></i>Input</span><span><i style="background:'+t.accent2+'"></i>Output incl. reasoning</span>'+(SPLIT?'<span><i style="background:'+t.reason+'"></i>Reasoning</span>':'')+'<span><i style="background:'+t.cache+'"></i>Cache</span>';
}
function renderProjAgents(){
  $('projects').innerHTML='<span style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--subtle);font-weight:700;align-self:center">Projects</span>'+
    S.projects.slice(0,8).map(p=>'<span class="chip" data-tip="<b>'+ESC(p[0])+'</b><span class=\'trow\'><span>Sessions</span><b>'+p[1]+'</b></span><span class=\'trow\'><span>Tokens</span><b>'+FN(p[2]||0)+'</b></span>">'+
      '<b>'+FN(p[2]||p[1])+'</b><span class="n">'+ESC(p[0])+' · '+p[1]+'</span></span>').join('')||'<span class="empty">No projects</span>';
  $('agents').innerHTML=S.agents.map(a=>'<span class="chip'+(FSTATE.agent===a[0]?' active':'')+'" data-f="agent|'+ESC(a[0])+'" data-tip="'+
    ('<b>'+ESC(a[0])+' agent</b><span class=\'trow\'><span>Sessions</span><b>'+a[1]+'</b></span>'+
     '<span class=\'trow\'><span>Tokens</span><b>'+FN(a[2])+'</b></span>')+'">'+
    '<i style="width:9px;height:9px;border-radius:3px;background:'+[T().accent,T().accent2,T().accent3][S.agents.indexOf(a)%3]+';display:inline-block"></i>'+
    ESC(a[0])+' · '+a[1]+' · '+FN(a[2])+'</span>').join('');
}
let SA=null;
let CX=null;
function renderCodex(){
  const el=$('cx-tbl');
  if(!el)return;
  const tb=el.tBodies[0];
  const rows=((CX||{}).sessions||[]);
  const hint=$('cx-hint');
  if(hint)hint.textContent='sessions: '+(((CX||{}).total||0))+' \u00b7 tokens: '+(((CX||{}).tokens||0)).toLocaleString('en-US');
  const cnt=$('tab-router-cnt');
  if(cnt)cnt.textContent=((CX||{}).total||'');
  if(!rows.length){tb.innerHTML='<tr><td colspan="6"><div class="empty">'+ESC((CX&&CX.error)||'No Codex sessions found.')+'</div></td></tr>';return;}
  tb.innerHTML=rows.map(function(r){
    const prev=(r.previews||[]).map(function(p){return '<div class="prev">'+ESC(p)+'</div>';}).join('');
    const tools=(r.tools||[]).map(function(t){return '<span class="chip">'+ESC(t[0])+' \u00d7'+t[1]+'</span>';}).join(' ');
    return '<tr>'+'<td><b>'+ESC(r.name||'?')+'</b>'+prev+'<div class="sub">'+ESC(r.cwd||'')+'</div></td>'+
      '<td>'+ESC(r.model||r.provider||'?')+'</td>'+'<td class="sub">'+ESC((r.cwd||'').split('\\').pop()||'')+'</td>'+
      '<td class="num">'+(r.n||0)+'</td>'+'<td class="num">'+(r.tok||0).toLocaleString('en-US')+'</td>'+
      '<td class="sub">'+ESC((r.last||'').slice(0,19).replace('T',' '))+'</td></tr>'+
      (tools?'<tr class="toolsrow"><td colspan="6">'+tools+'</td></tr>':'');
  }).join('');
}

const SUB_ACT={recent:'Recent activity',quiet:'No recent activity',stale:'Older activity',unknown:'Unknown'};

function renderSubagents(){
  const tb=$('subtbl').querySelector('tbody');
  if(!SA||SA.error){tb.innerHTML='<tr><td colspan="7"><div class="empty">'+ESC((SA&&SA.error)||'No subagent data.')+'</div></td></tr>';return;}
  const ac=SA.activity_counts||{};
  $('sub-hint').textContent=(ac.recent||0)+' '+SUB_ACT.recent+' · '+(ac.quiet||0)+' '+SUB_ACT.quiet+' · '+(ac.stale||0)+' '+SUB_ACT.stale+' · '+(ac.unknown||0)+' '+SUB_ACT.unknown+' · '+(SA.listed_count||0)+' of '+(SA.total_child_sessions||0)+' child sessions'+(SA.truncated?' (truncated)':'')+' · Based on session updates; not execution status';
  $('subagents-chips').innerHTML=(SA.child_agents||[]).map(a=>'<span class="chip"><i style="width:9px;height:9px;border-radius:3px;background:var(--accent);display:inline-block"></i>'+
    ESC(a.agent)+' · '+a.n+' · '+FN(a.toks)+'</span>').join('')||'<span class="empty">No subagents yet.</span>';
  $('subagents-all').innerHTML='<span class="empty">all sessions by type:</span> '+(SA.all_agents||[]).map(a=>'<span class="chip">'+
    ESC(a.agent)+' · '+a.n+' · '+FN(a.toks)+'</span>').join('');
  const dayLbl=t=>{const d=new Date(t>1e12?t:t*1000);
    const s=new Date(d),n=new Date();s.setHours(0,0,0,0);n.setHours(0,0,0,0);
    const diff=Math.round((n-s)/86400000);
    const hh=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
    return diff===0?'Today '+hh:diff===1?'Yesterday '+hh:(d.getMonth()+1)+'/'+d.getDate()+' '+hh;};
  const ageLbl=a=>{if(a==null)return 'timestamp unavailable';a=+a||0;if(a<60)return a+' s ago';if(a<3600)return Math.floor(a/60)+' min ago';if(a<86400)return Math.floor(a/3600)+' h ago';return Math.floor(a/86400)+' d ago';};
  tb.innerHTML=(SA.child_runs||[]).map((s,i)=>'<tr class="clickable" data-run="'+i+'">'
    +'<td><span class="dot '+(s.activity_state||'unknown')+'"></span><div style="font-size:11px;color:var(--muted)">'+SUB_ACT[s.activity_state||'unknown']+'<br>'+ageLbl(s.age_s)+'</div></td>'
    +'<td><div class="t">'+ESC(s.title)+'</div>'+
    '<div class="badges"><span class="badge">'+ESC((s.directory||'').split(/[\\/]/).pop())+'</span></div></td>'
    +'<td><div class="badges mix"><span class="badge agent">'+ESC(s.agent)+'</span><span class="badge model">'+ESC(modelShort(s.model))+'</span>'+(s.provider?'<span class="badge">'+ESC(s.provider)+'</span>':'')+'</div></td>'
    +'<td><div class="t" style="font-weight:400">'+ESC(s.parent_title)+'</div></td>'
    +'<td class="mono" style="font-size:11.5px;color:var(--muted)">'+dayLbl(s.time_created)+'</td>'
    +'<td class="num" style="font-weight:700">'+FN(s.toks||0)+'</td><td class="num" style="color:var(--subtle)">'+F(s.msgs)+'</td></tr>').join('')
  document.querySelectorAll('#subtbl tr.clickable').forEach(tr=>{tr.onclick=()=>toggleRun(tr);});

}

function toggleRun(tr){
  const nx=tr.nextSibling;
  if(nx&&nx.classList&&nx.classList.contains('run-detail')){nx.remove();return;}
  document.querySelectorAll('#subtbl tr.run-detail').forEach(x=>x.remove());
  const s=SA.child_runs[+tr.getAttribute('data-run')];
  const d=document.createElement('tr');d.className='run-detail';
  d.innerHTML='<td colspan="7"><span class="kv">Activity: <b>'+ESC(SUB_ACT[s.activity_state||'unknown'])+'</b></span>'
    +'<span class="kv">Last activity: <b>'+(s.age_s==null?'unknown':F(s.age_s)+' s ago')+'</b></span>'
    +'<span class="kv">Session: <b class="mono">'+ESC(s.id)+'</b></span>'
    +'<span class="kv">Agent: <b>'+ESC(s.agent)+'</b></span>'
    +'<span class="kv">Model: <b>'+ESC(s.model||'-')+(s.provider?' · '+ESC(s.provider):'')+'</b></span>'
    +'<span class="kv">Directory: <b>'+ESC(s.directory||'-')+'</b></span>'
    +'<span class="kv">Parent: <b class="mono">'+ESC(s.parent_id||'-')+'</b></span>'
    +'<span class="kv">Messages: <b>'+F(s.msgs)+'</b></span></td>';
  tr.after(d);
}
function renderConfig(){
  const cfg=(SA&&SA.config)||[];
  if(!cfg.length){$('cfg-tiles').innerHTML='<span class="empty">No agent definitions found.</span>';$('tile-detail').innerHTML='';return;}
  const nProj=cfg.filter(c=>c.source==='project').length;
  const nBuilt=cfg.filter(c=>c.source==='builtin').length;
  $('cfg-hint').textContent=cfg.length+' definitions | '+nProj+' project | '+nBuilt+' builtin';
  $('cfg-chips').innerHTML='<span class="chip">project: '+nProj+'</span><span class="chip">builtin: '+nBuilt+'</span>';
  const leads=cfg.filter(c=>/^tl-\d$/.test(c.name));
  const built=cfg.filter(c=>c.source==='builtin');
  const workersOf=l=>cfg.filter(c=>c.name.indexOf(l+'-')===0);
  $('cfg-tiles').innerHTML=
    leads.map(l=>{const w=workersOf(l.name);
      return '<div class="tile'+(TILE_SEL===l.name?' active':'')+'" data-tile="'+ESC(l.name)+'">'
      +'<div class="tn">'+ESC(l.name)+'</div>'
      +'<div class="tc">'+w.length+'</div>'
      +'<div class="td">subagents | click to expand</div>'
      +'<div class="td" style="margin-top:6px">'+ESC((l.description||'').slice(0,90))+'</div></div>';}).join('')
    +built.map(b=>'<div class="tile'+(TILE_SEL===b.name?' active':'')+'" data-built="'+ESC(b.name)+'">'
      +'<div class="tn">'+ESC(b.name)+'</div>'
      +'<div class="tc" style="color:var(--muted)">core</div>'
      +'<div class="td">builtin | '+ESC(b.mode||'')+'</div></div>').join('');
  document.querySelectorAll('#cfg-tiles .tile[data-tile]').forEach(t=>{t.onclick=()=>toggleTile(t.getAttribute('data-tile'));});
  document.querySelectorAll('#cfg-tiles .tile[data-built]').forEach(t=>{t.onclick=()=>toggleTile(t.getAttribute('data-built'));});
  if(TILE_SEL)toggleTile(TILE_SEL,true);
}
let TILE_SEL=null;
function toggleTile(name,keep){
  const cfg=(SA&&SA.config)||[];
  if(!keep)TILE_SEL=(TILE_SEL===name?null:name);
  document.querySelectorAll('#cfg-tiles .tile').forEach(t=>{t.classList.toggle('active',t.getAttribute('data-tile')===TILE_SEL||t.getAttribute('data-built')===TILE_SEL);});
  const box=$('tile-detail');
  if(!TILE_SEL){box.innerHTML='';return;}
  const me=cfg.find(c=>c.name===TILE_SEL);
  const workers=cfg.filter(c=>c.name.indexOf(TILE_SEL+'-')===0);
  let h='<div class="tile-detail"><div class="t" style="font-weight:800;margin-bottom:4px">'+ESC(TILE_SEL)+'</div>'
    +'<div class="td" style="margin-bottom:8px">'
    +ESC((me&&me.description)||'')+' <span style="color:var(--subtle)">['+ESC((me&&me.mode)||'')+' | '+ESC((me&&me.model)||'default')+']</span></div>';
  if(workers.length){h+=workers.map(w=>'<div class="wrow"><span class="badge">'+ESC(w.name)+'</span><span>'+ESC(w.description||'')+'</span></div>').join('');}
  else{h+='<div class="td">No assigned subagents.</div>';}
  box.innerHTML=h+'</div>';
}
function renderAct(){
  const act=S.activity||[];
  const $w=$('act');
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  if(!act.length){$w.innerHTML='<div class="empty">Nothing yet.</div>';$('act-summary').textContent='';$('act-legend').innerHTML='';return;}
  const today=new Date().toLocaleDateString('en-CA');
  const cols=[];let cur=null;
  act.forEach(d=>{
    const dt=new Date(d.date+'T00:00:00');
    const dow=(dt.getDay()+6)%7;
    if(dow===0||!cur){cur={days:new Array(7).fill(null),month:dt.getMonth()};cols.push(cur);}
    cur.days[dow]=d;
  });
  const mx=Math.max(...act.map(d=>dayTotal(d)),1);
  const lvl=v=>{if(!v)return 0;const t=v/mx;return t>=0.75?4:t>=0.5?3:t>=0.3?2:1;};
  const pct=[0,18,34,52,70,86];
  const cell=(d)=>{
    const tot=d?dayTotal(d):0;
    const lv=lvl(tot);
    const bg=lv?'color-mix(in srgb,'+T().accent+' '+pct[lv]+'%, var(--bg))':'var(--chip)';
    const tipH=d?('<b>'+d.date+'</b><span class="trow"><span>Total tokens</span><b>'+FN(tot)+'</b></span>'+
      '<span class="trow"><span>Input</span><b>'+FN(d.ti)+'</b></span>'+
      '<span class="trow"><span>Output incl. reasoning</span><b>'+FN(outMerged(d))+'</b></span>'+
      '<span class="trow"><span>Cache</span><b>'+FN(d.cache)+'</b></span>'+
      '<span class="trow"><span>Sessions</span><b>'+d.sessions+'</b></span>'):'';
    const f=d?' data-date="'+d.date+'" data-tip=\''+tipH+'\' data-f="day|'+d.date+'"':'';
    return '<button class="act-cell'+(d&&d.date===today?' today':'')+(d&&FSTATE.day&&FSTATE.day!==d.date?' dim':'')+'" '+
      'style="background:'+bg+'" aria-label="'+(d?d.date+', '+FN(tot)+' tokens':'No activity')+'"'+f+'></button>';
  };
  let monthHtml='',i=0;
  while(i<cols.length){const m=cols[i].month;let j=i;while(j<cols.length&&cols[j].month===m)j++;
    monthHtml+='<span style="width:'+((j-i)*(12+3)-3)+'px">'+MONTHS[m]+'</span>';i=j;}
  const labels=['Mon','Wed','Fri'].map(d=>'<span>'+d+'</span>').join('');
  $w.innerHTML='<div class="act-grid"><div class="act-months">'+monthHtml+'</div>'+
    '<div class="act-body"><div class="act-labels">'+labels+'</div>'+
    '<div class="act-cols">'+cols.map(c=>'<div class="act-col">'+c.days.map(cell).join('')+'</div>').join('')+'</div></div></div>';
  const active=act.filter(d=>dayTotal(d)>0).length;
  const tot=act.reduce((a,d)=>({tokens:a.tokens+dayTotal(d),sessions:a.sessions+d.sessions,msgs:a.msgs+d.msgs}),{tokens:0,sessions:0,msgs:0});
  $('act-summary').innerHTML='<b>'+active+'</b> active days · <b>'+FN(tot.tokens)+'</b> tokens · '+
    '<b>'+F(tot.sessions)+'</b> sessions in the last 52 weeks';
  $('act-legend').innerHTML='Less <span class="sw e"></span>'+
    [1,2,3,4].map(l=>'<span class="sw" style="background:color-mix(in srgb,'+T().accent+' '+pct[l]+'%, var(--bg))"></span>').join('')+' More <span style="margin-left:8px">token intensity</span>';
}
/* sessions */
function sessionRows(){
  const q=($('search').value||'').toLowerCase();
  const cut=RANGE==='all'?0:Date.now()-RANGE*86400000;
  return S.sessions.filter(s=>{
    if(s.time_created<cut)return false;
    if(FSTATE.agent&&s.agent!==FSTATE.agent)return false;
    if(FSTATE.model){const mk=(s.provider?s.provider+'/':'')+(s.model||'');if(mk!==FSTATE.model)return false;}
    if(FSTATE.day&&DS(s.time_created)!==FSTATE.day)return false;
    if(FSTATE.file&&!(((S.session_files||{})[s.id])||[]).includes(FSTATE.file))return false;
    if(FSTATE.sub){
      const files=((S.session_files||{})[s.id])||[];
      const subs=files.map(fp=>((S.file_subsys||{})[fp])||'');
      if(!subs.includes(FSTATE.sub))return false;
    }
    if(FSTATE.commit&&!(((S.commit_sessions||{})[FSTATE.commit])||[]).includes(s.id))return false;
    if(q&&!(s.title||'').toLowerCase().includes(q)&&!(s.worktree||'').toLowerCase().includes(q))return false;
    return true;});
}
function renderSessions(){
  const rows=sessionRows();
  const mf=$('model-f');
  const curSel=mf.value;
  mf.innerHTML='<option value="">All models</option>'+S.models.map(m=>'<option value="'+ESC(m[0])+'">'+ESC(modelShort(m[0]))+(m[0].indexOf('/')>=0?' ('+ESC(m[0].split('/')[0])+')':'')+'</option>').join('');
  mf.value=FSTATE.model||curSel||'';
  const af=$('agent-f');
  const curA=af.value;
  af.innerHTML='<option value="">All agents</option>'+S.agents.map(a=>'<option value="'+ESC(a[0])+'">'+ESC(a[0])+'</option>').join('');
  af.value=FSTATE.agent||curA||'';
  rows.sort((a,b)=>{let x=a[sortK],y=b[sortK];
    if(sortK==='tokens'){x=a.tokens_total||0;y=b.tokens_total||0;}
    return(x<y?-1:x>y?1:0)*(sortAsc?1:-1);});
  currentRows=rows;
  const fcount=Object.values(FSTATE).filter(Boolean).length;
  $('sess-count').textContent=F(rows.length)+' of '+F(S.sessions.length)+' shown';
  $('sess-hint').textContent=(fcount?'filtered by '+fcount+' criteria · ':'')+'click a row for token details';
  const latestTime=[...S.sessions].sort((a,b)=>b.time_created-a.time_created)[0]?.time_created;
  const dayLbl=t=>{const d=new Date(t>1e12?t:t*1000);
    const s=new Date(d),n=new Date();s.setHours(0,0,0,0);n.setHours(0,0,0,0);
    const diff=Math.round((n-s)/86400000);
    const hh=String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
    return diff===0?'Today '+hh:diff===1?'Yesterday '+hh:(d.getMonth()+1)+'/'+d.getDate()+' '+hh;};
  const tb=$('tbl').querySelector('tbody');
  tb.innerHTML=rows.map((s,i)=>{
    const tot=s.tokens_total||((s.tokens_input||0)+(s.tokens_output||0)+(s.tokens_reasoning||0)+(s.tokens_cache||0));
    const out=(s.tokens_output||0)+(s.tokens_reasoning||0);
    const isLatest=s.time_created===latestTime;
    return'<tr data-i="'+i+'" tabindex="0" role="link" aria-label="Open session '+ESC(s.title)+'"'+(isLatest?' class="latest"':'')+'><td><div class="t">'+ESC(s.title)+'</div>'+
    '<div class="badges"><span class="badge">'+ESC(s.worktree)+'</span>'+(isLatest?'<span class="badge latest">Latest</span>':'')+'</div></td>'
    +'<td><div class="badges mix"><span class="badge agent">'+ESC(s.agent)+'</span><span class="badge model">'+ESC(modelShort(s.model))+'</span>'+(s.provider?'<span class="badge">'+ESC(s.provider)+'</span>':'')+'</div></td>'
    +'<td class="mono" style="font-size:11.5px;color:var(--muted)">'+dayLbl(s.time_created)+'</td>'
    +'<td class="num">'+FN(s.tokens_input||0)+'</td><td class="num">'+FN(out)+'</td><td class="num">'+FN(s.tokens_cache||0)+'</td>'
    +'<td class="num" style="font-weight:700">'+FN(tot)+'</td><td class="num" style="color:var(--subtle)">'+F(s.msgs)+'</td></tr>';}).join('')
    ||'<tr><td colspan="8"><div class="empty">No sessions match.</div></td></tr>';
}
function renderFilterBar(){
  const bar=$('fbar');
  const active=Object.entries(FSTATE).filter(([k,v])=>v);
  if(!active.length){bar.style.display='none';return;}
  bar.style.display='flex';
  const labels={day:'Day',model:'Model',agent:'Agent',file:'File',sub:'Subsystem',commit:'Commit'};
  const disp=(k,v)=>{
    if(k==='model')return String(v).replace('/',' · ');
    if(k==='file')return shortPath(v);
    if(k==='commit')return String(v).slice(0,7);
    return v;
  };
  bar.innerHTML='<span class="fl">Filtering:</span>'+active.map(([k,v])=>
    '<span class="fchip" data-k="'+k+'">'+labels[k]+': '+ESC(disp(k,v))+' <span class="fx">✕</span></span>').join('')
    +'<button class="fclear">clear all</button>';
  bar.querySelectorAll('.fchip').forEach(ch=>ch.onclick=()=>{FSTATE[ch.dataset.k]=null;renderAll();});
  bar.querySelector('.fclear').onclick=()=>{for(const k in FSTATE)FSTATE[k]=null;$('agent-f').value='';$('model-f').value='';renderAll();};
}
/* ---- Codex tab (all models, from usage-events.jsonl) ---- */
function rModelShort(m){return (m||'-').split('/').pop();}
function rDayTokenTotal(d){return (d.ti||0)+(d.to||0);}
function rCutDate(){
  if(RRANGE==='all')return'';
  const d=new Date(Date.now()-RRANGE*86400000);
  return d.toLocaleDateString('en-CA');
}
function rInPeriod(day){const c=rCutDate();return !c||day.date>=c;}
function rPeriodDays(){return (R&&R.days?R.days:[]).filter(rInPeriod);}
function rSumDays(list){
  const t={reqs:0,ok:0,err:0,unknown:0,err429:0,err500:0,ti:0,to:0,tr:0,cache:0,total:0,what_if:0};
  list.forEach(d=>{t.reqs+=d.reqs||0;t.ok+=d.ok||0;t.err+=d.err||0;t.unknown+=d.unknown||0;t.err429+=d.err429||0;t.err500+=d.err500||0;t.ti+=d.ti||0;t.to+=d.to||0;t.tr+=d.tr||0;t.cache+=d.cache||0;t.total+=rDayTokenTotal(d);t.what_if+=d.what_if||0;});
  return t;
}
function rDayTip(d){
  return '<b>'+d.date+'</b>'+
    '<span class="trow"><span>Requests</span><b>'+F(d.reqs||0)+' ('+F(d.ok||0)+' ok)</b></span>'+
    '<span class="trow"><span>Input total</span><b>'+FN(d.ti)+'</b></span>'+
    '<span class="trow"><span>↳ cached input</span><b>'+FN(d.cache||0)+'</b></span>'+
    '<span class="trow"><span>↳ uncached input</span><b>'+FN(Math.max(0,(d.ti||0)-(d.cache||0)))+'</b></span>'+
    '<span class="trow"><span>Output incl. reasoning</span><b>'+FN(d.to||0)+'</b></span>'+
    (RSPLIT?'<span class="trow"><span>Reasoning</span><b>'+FN(d.tr||0)+'</b></span>':'')+
    '<span class="trow"><span>Total</span><b>'+FN(rDayTokenTotal(d))+'</b></span>';
}
function rStackChart(days){
  const t=T();
  const keys=RSPLIT?[{p:'uncached',c:t.accent,l:'Uncached input'},{p:'cache',c:t.cache,l:'Cached input'},{p:'to',c:t.accent2,l:'Output'},{p:'tr',c:t.reason,l:'Reasoning'}]
                    :[{p:'uncached',c:t.accent,l:'Uncached input'},{p:'cache',c:t.cache,l:'Cached input'},{p:'out',c:t.accent2,l:'Output incl. reasoning'}];
  const val=(d,k)=>k.p==='uncached'?Math.max(0,(d.ti||0)-(d.cache||0)):k.p==='out'?(d.to||0):k.p==='to'?Math.max(0,(d.to||0)-(RSPLIT?(d.tr||0):0)):(d[k.p]||0);
  const w=980,h=250,p=34;
  let max=1;
  if(RSHARE){max=100;}
  else{max=Math.max(...days.map(d=>keys.reduce((a,k)=>a+val(d,k),0)),1);}
  const Y=v=>p+(1-v/max)*(h-2*p),X=i=>days.length>1?p+i*(w-2*p)/(days.length-1):p+(w-2*p)/2;
  const slot=(w-2*p)/Math.max(days.length,1);
  const sw=Math.min(slot*0.32,p*0.85);
  const step=Math.ceil(days.length/12)||1;
  let cells='',xl='';
  days.forEach((day,i)=>{
    const tot=keys.reduce((a,k)=>a+val(day,k),0);
    let acc=0;
    const x1=X(i)-sw,x2=X(i)+sw;
    keys.forEach(k=>{
      const v=val(day,k);
      if(RSHARE){
        const share0=acc/(tot||1)*100||0;
        acc+=v;
        const share1=acc/(tot||1)*100||0;
        const y0=Y(share1),y1=Y(share0);
        if(!v)return;
        cells+=svg('path',{d:'M'+x1.toFixed(1)+','+y0.toFixed(1)+' L'+x2.toFixed(1)+','+y0.toFixed(1)+' L'+x2.toFixed(1)+','+y1.toFixed(1)+' L'+x1.toFixed(1)+','+y1.toFixed(1)+' Z',
          fill:k.c,class:'inter','data-tip':'<b>'+k.l+' · '+day.date+'</b><span class="trow"><span>Tokens</span><b>'+FN(v)+'</b></span>'});
      }else{
        if(!v)return;
        cells+=svg('path',{d:'M'+x1.toFixed(1)+','+Y(acc+v).toFixed(1)+' L'+x2.toFixed(1)+','+Y(acc+v).toFixed(1)+' L'+x2.toFixed(1)+','+Y(acc).toFixed(1)+' L'+x1.toFixed(1)+','+Y(acc).toFixed(1)+' Z',
          fill:k.c,class:'inter','data-tip':'<b>'+k.l+' · '+day.date+'</b><span class="trow"><span>Tokens</span><b>'+FN(v)+'</b></span>'});
        acc+=v;
      }
    });
    cells+=svg('rect',{x:(X(i)-slot/2).toFixed(1),y:p,width:(slot*0.96).toFixed(1),height:(h-2*p).toFixed(1),fill:'transparent',
      class:'inter','data-tip':rDayTip(day)});
    if(i%step===0)xl+=svg('text',{x:X(i).toFixed(1),y:h-p+17,'text-anchor':'middle',fill:'#8f7f5c','font-size':10},day.date.slice(5));
  });
  const sub=$('r-tok-sub');
  if(sub)sub.textContent=(RSHARE?'100% share':'stacked absolute')+(RSPLIT?' · reasoning split':' · output merged')+' · all Codex models';
  return '<svg viewBox="0 0 '+w+' '+(h+10)+'" width="100%">'+cells+xl+'</svg>';
}
function rFileRows(){
  // Mirrored from the local edit/write aggregates: the router event stream
  // carries no file info, so this panel is display-only context.
  return (RFS==='file'?((S&&S.files)||[]):((S&&S.subsystems)||[]))||[];
}
function renderRouterFileChart(){
  const el=$('r-file-chart');if(!el)return;
  const rows=rFileRows();
  const sub=$('r-file-sub');
  if(sub)sub.textContent=(RFS==='file'?'top files · equal-split':'top subsystems')+' · from local edit + write touches';
  if(!S||!rows.length){el.innerHTML='<div class="empty">No local file data to mirror.</div>';return;}
  const t=T();
  const totalAll=rows.reduce((a,r)=>a+(r[1]||0),0)||1;
  const max=Math.max(...rows.map(r=>r[1]||0),1);
  const shown=RFS_EXP?rows:rows.slice(0,6);
  el.innerHTML=shown.map(r=>{
    const name=r[0],toks=r[1]||0,n=r[2]||0,avg=r[3]||0;
    const short=RFS==='file'?shortPath(name):name;
    const tip='<b>'+ESC(name)+'</b><span class=\'trow\'><span>Tokens</span><b>'+FN(toks)+'</b></span>'+
      '<span class=\'trow\'><span>Sessions</span><b>'+n+'</b></span><span class=\'trow\'><span>Avg / session</span><b>'+FN(avg)+'</b></span>'+
      '<span class=\'trow\'><span>Source</span><b>local touches</b></span>';
    return '<div class="model-row" data-tip="'+tip+'">'+
      '<div><div class="mn">'+ESC(short)+'</div><div class="ms">'+n+' sess · avg '+FN(avg)+'</div></div>'+
      '<div class="mbar"><i style="width:'+(toks/max*100).toFixed(1)+'%;background:'+(RFS==='file'?t.accent:t.accent2)+'"></i></div>'+
      '<div class="mv">'+FN(toks)+'</div></div>';
  }).join('')+(rows.length>6?'<button class="more-btn" data-exp="rfs">'+(RFS_EXP?'Show less':'Show all '+rows.length)+' '+(RFS_EXP?'▴':'▾')+'</button>':'');
}
function rCommitRows(){
  const cut=RRANGE==='all'?0:Date.now()-RRANGE*86400000;
  let rows=((R&&R.commits)||[]).filter(c=>{
    if(RFSTATE.day&&c[2]!==RFSTATE.day)return false;
    if(cut){
      const ms=Date.parse(c[2]+'T00:00:00');
      if(ms&&ms<cut-86400000)return false;
    }
    return true;
  });
  rows=rows.slice();
  if(RCS==='recent')rows.sort((a,b)=>a[2]<b[2]?1:-1);
  else rows.sort((a,b)=>(b[7]||0)-(a[7]||0));
  return rows.slice(0,20);
}
function renderRouterCommitChart(){
  const el=$('r-commit-chart');if(!el)return;
  const rows=rCommitRows();
  const sub=$('r-commit-sub');
  if(sub)sub.textContent='global window · not additive · time-matched';
  if(!rows.length){el.innerHTML='<div class="empty">No git commits found in tracked worktrees.</div>';return;}
  const t=T();
  const max=Math.max(...rows.map(r=>r[7]||0),1);
  const shown=RCS_EXP?rows:rows.slice(0,6);
  el.innerHTML=shown.map(r=>{
    const sha=r[0],msg=r[1]||'(no message)',date=r[2],proj=r[3],nf=r[4]||0,add=r[5]||0,del=r[6]||0,toks=r[7]||0,nq=r[8]||0;
    const tip='<b>'+ESC(msg)+'</b><span class=\'trow\'><span>SHA</span><b>'+ESC(sha.slice(0,12))+'</b></span>'+
      '<span class=\'trow\'><span>Date · project</span><b>'+date+' · '+ESC(proj)+'</b></span>'+
      '<span class=\'trow\'><span>Changed</span><b>'+nf+' files · +'+F(add)+' / -'+F(del)+'</b></span>'+
      '<span class=\'trow\'><span>Usage in the 24h before commit</span><b>'+FN(toks)+' · '+nq+' requests</b></span>'+
      '<span class=\'trow\'><span>Scope</span><b>global window · windows overlap · not additive</b></span>';
    return '<div class="model-row" data-tip="'+tip+'">'+
      '<div><div class="mn">'+ESC(msg.length>44?msg.slice(0,44)+'…':msg)+'</div>'+
      '<div class="ms">'+sha.slice(0,7)+' · '+date+' · '+ESC(proj)+' · +'+F(add)+'/-'+F(del)+'</div></div>'+
      '<div class="mbar"><i style="width:'+Math.max(toks?2:0,toks/max*100).toFixed(1)+'%;background:'+t.accent2+'"></i></div>'+
      '<div class="mv">'+(toks?FN(toks):'-')+'</div></div>';
  }).join('')+(rows.length>6?'<button class="more-btn" data-exp="rcs">'+(RCS_EXP?'Show less':'Show all '+rows.length)+' '+(RCS_EXP?'▴':'▾')+'</button>':'');
}
function rHumanize(n){n=Number(n)||0;const words=Math.round(n/1.3);if(words>1e6)return (words/1e6).toFixed(1)+'M words';if(words>1e3)return (words/1e3).toFixed(0)+'K words';return words+' words';}
function renderRouterRecords(R,totTokens){
  const el=$('r-records');if(!el||!R.records)return;
  const r=R.records;
  const cards=[];
  if(r.biggest_day)cards.push(['Biggest day',FN(r.biggest_day.total),r.biggest_day.date+' · '+r.biggest_day.sessions+' requests · '+rHumanize(r.biggest_day.total)]);
  if(r.biggest_model)cards.push(['Biggest model',FN(r.biggest_model.total),rModelShort(r.biggest_model.name)+' · '+F(r.biggest_model.requests)+' requests']);
  if(r.biggest_request)cards.push(['Biggest request',FN(r.biggest_request.total),rModelShort(r.biggest_request.model)+' · '+String(r.biggest_request.at||'').replace('T',' ').slice(0,19)]);
  if(r.streaks)cards.push(['Streaks',r.streaks.current+'d now','longest '+r.streaks.longest+'d · '+rHumanize(totTokens)+' total']);
  el.innerHTML=cards.map(([l,v,s])=>'<div class="rec"><div class="rl">'+l+'</div><div class="rv">'+ESC(v)+'</div><div class="rs">'+ESC(s)+'</div></div>').join('')||'<div class="empty">No records yet.</div>';
}
function renderRouterStatus(pc){
  const t=(R&&R.totals)||{};
  const v=k=>pc?pc[k]:(t[k==='ok'?'ok':k==='err'?'errors':k]||0);
  const items=[
    ['OK',v('ok'),T().accent2,'ok'],
    ['Errors',v('err'),(v('err')?'var(--bad)':'var(--subtle)'),'err'],
    ['429 rate-limited',v('err429'),(v('err429')?'var(--bad)':'var(--subtle)'),'429'],
    ['5xx upstream',v('err500'),'var(--bad)','err'],
    ['Unknown outcome',v('unknown'),'var(--subtle)','unknown'],
  ];
  $('r-status').innerHTML=items.map(([l,v,cc,k])=>'<span class="chip'+(RFSTATE.status===k?' active':'')+'" data-rs="'+k+'" data-tip="<b>'+l+'</b><span class=\'trow\'><span>Requests</span><b>'+F(v)+'</b></span>">'+
    '<i style="width:9px;height:9px;border-radius:3px;background:'+cc+';display:inline-block"></i>'+l+' · '+F(v)+'</span>').join('');
}
function rActDayTotal(dd){return rDayTokenTotal(dd);}
function renderRouterAct(){
  const act=(R&&R.activity)||[];
  const $w=$('r-act');
  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  if(!act.length){$w.innerHTML='<div class="empty">Nothing yet.</div>';$('r-act-summary').textContent='';$('r-act-legend').innerHTML='';return;}
  const today=new Date().toLocaleDateString('en-CA');
  const cols=[];let cur=null;
  act.forEach(dd=>{
    const dt=new Date(dd.date+'T00:00:00');
    const dow=(dt.getDay()+6)%7;
    if(dow===0||!cur){cur={days:new Array(7).fill(null),month:dt.getMonth()};cols.push(cur);}
    cur.days[dow]=dd;
  });
  const mx=Math.max(...act.map(dd=>rActDayTotal(dd)),1);
  const lvl=v=>{if(!v)return 0;const tt=v/mx;return tt>=0.75?4:tt>=0.5?3:tt>=0.3?2:1;};
  const pc=[0,18,34,52,70,86];
  const cell=(dd)=>{
    const tot=dd?rActDayTotal(dd):0;
    const lv=lvl(tot);
    const bg=lv?'color-mix(in srgb,'+T().accent+' '+pc[lv]+'%, var(--bg))':'var(--chip)';
    const tipH=dd?('<b>'+dd.date+'</b><span class="trow"><span>Total tokens</span><b>'+FN(tot)+'</b></span>'+
      '<span class="trow"><span>Requests</span><b>'+F(dd.reqs||dd.msgs||0)+'</b></span>'):'';
    const f=dd?' data-tip=\''+tipH+'\' data-rday="'+dd.date+'"':'';
    return '<button class="act-cell'+(dd&&dd.date===today?' today':'')+(dd&&RFSTATE.day&&RFSTATE.day!==dd.date?' dim':'')+'" '+
      'style="background:'+bg+'" aria-label="'+(dd?dd.date+', '+FN(tot)+' tokens':'No activity')+'"'+f+'></button>';
  };
  let monthHtml='',i=0;
  while(i<cols.length){const m=cols[i].month;let j=i;while(j<cols.length&&cols[j].month===m)j++;
    monthHtml+='<span style="width:'+((j-i)*(12+3)-3)+'px">'+MONTHS[m]+'</span>';i=j;}
  const labels=['Mon','Wed','Fri'].map(dd=>'<span>'+dd+'</span>').join('');
  $w.innerHTML='<div class="act-grid"><div class="act-months">'+monthHtml+'</div>'+
    '<div class="act-body"><div class="act-labels">'+labels+'</div>'+
    '<div class="act-cols">'+cols.map(cc=>'<div class="act-col">'+cc.days.map(cell).join('')+'</div>').join('')+'</div></div></div>';
  const active=act.filter(dd=>rActDayTotal(dd)>0).length;
  const tot=act.reduce((a,dd)=>({tokens:a.tokens+rActDayTotal(dd),reqs:a.reqs+(dd.reqs||dd.msgs||0)}),{tokens:0,reqs:0});
  $('r-act-summary').innerHTML='<b>'+active+'</b> active days · <b>'+FN(tot.tokens)+'</b> tokens · '+
    '<b>'+F(tot.reqs)+'</b> requests in the last 52 weeks';
  $('r-act-legend').innerHTML='Less <span class="sw e"></span>'+
    [1,2,3,4].map(l=>'<span class="sw" style="background:color-mix(in srgb,'+T().accent+' '+pc[l]+'%, var(--bg))"></span>').join('')+' More <span style="margin-left:8px">token intensity</span>';
}
function renderRouterFilterBar(){
  const bar=$('r-fbar');
  const active=Object.entries(RFSTATE).filter(([k,v])=>v);
  if(!active.length){bar.style.display='none';return;}
  bar.style.display='flex';
  const labels={day:'Day',model:'Model',provider:'Provider',status:'Status'};
  const disp=(k,v)=>k==='model'?rModelShort(v):v;
  bar.innerHTML='<span class="fl">Filtering:</span>'+active.map(([k,v])=>
    '<span class="fchip" data-rk="'+k+'">'+labels[k]+': '+ESC(disp(k,v))+' <span class="fx">✕</span></span>').join('')
    +'<button class="fclear">clear all</button>';
  bar.querySelectorAll('.fchip').forEach(ch=>ch.onclick=()=>{
    const k=ch.dataset.rk;RFSTATE[k]=null;
    if(k==='status')$('r-status-f').value='';
    if(k==='provider')$('r-provider-f').value='';
    if(k==='model')$('r-model-f').value='';
    renderRouter();
  });
  bar.querySelector('.fclear').onclick=()=>{for(const k in RFSTATE)RFSTATE[k]=null;$('r-status-f').value='';$('r-provider-f').value='';$('r-model-f').value='';renderRouter();};
}
function renderRouter(){
  if(!R)return;
  const t=R.totals||{};
  const periodLbl=RRANGE==='all'?'ALL TIME':(RRANGE/30>=1?RRANGE/30+' MO':RRANGE+' DAYS');
  $('r-range').textContent='CODEX · '+(R.range||'').toUpperCase()+' · LAST '+periodLbl+' · REFRESHES AUTOMATICALLY';
  const cur=RRANGE==='all'?null:rSumDays(rPeriodDays());
  const totTokens=cur?cur.total:(t.tokens_total||0);
  const totReqs=cur?cur.reqs:(t.requests||0);
  $('r-headline').innerHTML=BOLD(ESC(totReqs+' '+(R.req_word||'requests')+' · '+Math.round(totTokens).toLocaleString('en-US')+' tokens with Codex.'));
  const nModels=(R.models||[]).length;
  $('r-pills').innerHTML=[
    [(R.is_synth?'Model calls':'Requests'),F(totReqs)],
    ['Tokens',FN(totTokens)],
    ['Models',F(nModels)],
    ['What-if paid','$'+Number(cur?cur.what_if:(t.what_if_cost||0)).toFixed(2)]]
    .map(([k,v])=>'<span class="pill">'+ESC(k)+' <b style="color:var(--text)">'+v+'</b></span>').join('');
  const t7=(()=>{const now=new Date();now.setHours(0,0,0,0);const s=new Date(now);s.setDate(s.getDate()-6);
    return rSumDays((R.days||[]).filter(d=>d.date>=s.toLocaleDateString('en-CA')));})();
  const t30=(()=>{const now=new Date();now.setHours(0,0,0,0);const s=new Date(now);s.setDate(s.getDate()-29);
    return rSumDays((R.days||[]).filter(d=>d.date>=s.toLocaleDateString('en-CA')));})();
  const t1=(()=>{const s=new Date().toLocaleDateString('en-CA');
    return rSumDays((R.days||[]).filter(d=>d.date>=s));})();
  $('r-summary').innerHTML=[['Today',t1.total],['Last 7 days',t7.total],['Last 30 days',t30.total],['All time',t.tokens_total||0]]
    .map(([l,v])=>'<div class="sum-card"><div class="sl">'+l+'</div><div class="sv">'+FN(v)+'</div><div class="ss">tokens</div></div>').join('');
  const rb=$('r-rate-banner');
  const lims=R.rate_limits||{};
  const limKeys=Object.keys(lims);
  const be=cur?cur.err429:(t.err429||0);
  const bw=cur?cur.what_if:(t.what_if_cost||0);
  if((t.err429||0)>0||limKeys.length){
    rb.style.display='flex';
    rb.innerHTML='<span>⚠ <b>'+F(be)+'</b> Codex requests hit <b>429</b> (usually free-model capacity)'+(cur?' in this period':'')+
      (limKeys.length?' · rate-limits: <span class="mono" style="font-size:11px">'+ESC(limKeys.slice(0,4).join(', '))+(limKeys.length>4?'…':'')+'</span>':'')+
      ' · actual free cost <b>$0</b> · what-if <b>$'+Number(bw||0).toFixed(2)+'</b>'+(cur?' (period)':'')+'</span>';
  }else{rb.style.display='none';rb.innerHTML='';}
  const c=T();
  const tok=cur?{input:cur.ti,out:cur.to,tr:cur.tr,ca:cur.cache}:{input:t.tokens_input,out:t.tokens_output,tr:t.tokens_reasoning,ca:t.tokens_cache_read};
  const uncached=Math.max(0,tok.input-tok.ca);
  const total=tok.input+tok.out;
  const pct=v=>total?Math.round(v/total*100):0;
  const row=(lbl,v,color,sub)=>'<div class="krow"><span class="lbl"><i class="dot" style="background:'+color+'"></i>'+lbl+
    (sub?' <span class="dim">'+sub+'</span>':'')+'</span><b>'+v+'</b></div>';
  const okRate=totReqs?Math.round(((cur?cur.ok:t.ok)||0)/totReqs*100):0;
  const hitRate=tok.input?Math.round(tok.ca/tok.input*100):0;
  const avgReq=totReqs?Math.round(total/totReqs):0;
  const topCommit=((R.commits||[])[0])||null;
  $('r-kpis').innerHTML=
    '<div class="kpi"><h3>Tokens '+(RSPLIT?'(reasoning split)':'(output merged)')+'</h3><div class="big">'+FN(total)+' <small>total</small></div>'+
    '<div class="tokbar">'+[[uncached,c.accent],[tok.ca,c.cache],[tok.out-(RSPLIT?tok.tr:0),c.accent2]].concat(RSPLIT?[[tok.tr,c.reason]]:[]).map(([v,k])=>'<i style="width:'+pct(v)+'%;background:'+k+'"></i>').join('')+'</div>'+
    row('Input total',FN(tok.input),c.accent,pct(tok.input)+'%')+row('&nbsp;&nbsp;↳ cached input',FN(tok.ca),c.cache,hitRate+'% of input')+
    row('&nbsp;&nbsp;↳ uncached input',FN(uncached),c.accent,(100-hitRate)+'% of input')+row('Output incl. reasoning',FN(tok.out),c.accent2,pct(tok.out)+'%')+
    (RSPLIT?row('&nbsp;&nbsp;↳ reasoning',FN(tok.tr),c.reason,pct(tok.tr)+'%'):'')+'</div>'+
    '<div class="kpi"><h3>Cache &amp; efficiency</h3><div class="big">'+hitRate+'% <small>cache hit</small></div>'+
    row('Cached tokens',FN(tok.ca),c.cache)+row('Avg / request',FN(avgReq),c.accent)+
    row('What-if paid','$'+Number(cur?cur.what_if:(t.what_if_cost||0)).toFixed(2)+(cur?' (period)':''),c.accent2)+
    row('Avg tokens/request (all)',FN(t.avg_tokens_per_session||0),'var(--subtle)')+'</div>'+
    '<div class="kpi"><h3>Burn &amp; activity</h3><div class="big">'+F(totReqs)+' <small>requests · '+okRate+'% ok</small></div>'+
    row('Errors',F(cur?cur.err:(t.errors||0)),(cur?cur.err:t.errors)?'var(--bad)':'var(--subtle)')+
    row('429s',F(cur?cur.err429:(t.err429||0)),(cur?cur.err429:(t.err429||0))?'var(--bad)':'var(--subtle)')+
    row('Top commit',topCommit?ESC((topCommit[1]||'').slice(0,30)):'-',c.accent2,'all-time')+
    row('Avg latency',(t.latency_na?'N/A':(F(t.avg_ms||0)+' ms')),'var(--subtle)',(t.latency_na?'no timing in Codex data':'ok-only · all-time'))+'</div>';
  renderRouterRecords(R,totTokens);
  const d=rPeriodDays();
  $('r-tok-chart').innerHTML=d.length?rStackChart(d):'<div class="empty">Nothing in this period.</div>';
  $('r-tok-legend').innerHTML='<span><i style="background:'+c.accent+'"></i>Uncached input</span><span><i style="background:'+c.cache+'"></i>Cached input</span><span><i style="background:'+c.accent2+'"></i>'+(RSPLIT?'Output':'Output incl. reasoning')+'</span>'+(RSPLIT?'<span><i style="background:'+c.reason+'"></i>Reasoning</span>':'')+(RSHARE?'<span>100% share mode</span>':'');
  renderRouterFileChart();
  renderRouterCommitChart();
  $('r-providers').innerHTML=(R.providers||[]).map(p=>'<span class="chip'+(RFSTATE.provider===p[0]?' active':'')+'" data-rp="'+ESC(p[0])+'" data-tip="<b>'+ESC(p[0])+'</b><span class=\'trow\'><span>Requests</span><b>'+p[1]+'</b></span><span class=\'trow\'><span>Tokens</span><b>'+FN(p[2]||0)+'</b></span>">'+
    '<b>'+FN(p[2]||p[1])+'</b><span class="n">'+ESC(p[0])+' · '+p[1]+'</span></span>').join('')||'<span class="empty">No providers</span>';
  renderRouterStatus(cur);
  const totalAll=(R.models||[]).reduce((a,m)=>a+(m[2]||0),0)||1;
  $('r-models').innerHTML=(R.models||[]).length?(R.models||[]).map(m=>{
    const name=m[0],reqs=m[1],tot=m[2]||0,ti=m[3]||0,to=m[4]||0,ca=m[5]||0,ok=m[6]||0,err=m[7]||0;
    const prov=m[10]||'',free=m[11],wi=m[12]||0;
    const tr=m[13]||0,out=to,uncached=Math.max(0,ti-ca);
    const segs=RSPLIT?[[uncached,c.accent],[ca,c.cache],[Math.max(0,to-tr),c.accent2],[tr,c.reason]]:[[uncached,c.accent],[ca,c.cache],[out,c.accent2]];
    const bar=segs.map(([v,k])=>'<i style="width:'+(tot?v/tot*100:0)+'%;background:'+k+'"></i>').join('');
    const tipH=('<b>'+ESC(name)+'</b><span class=\'trow\'><span>Requests</span><b>'+reqs+' ('+ok+' ok'+(err?', '+err+' err':'')+')</b></span>'+
      '<span class=\'trow\'><span>Total tokens</span><b>'+FN(tot)+' ('+(tot/totalAll*100).toFixed(1)+'%)</b></span>'+
      '<span class=\'trow\'><span>Input / Cached / Output</span><b>'+FN(ti)+' / '+FN(ca)+' / '+FN(out)+'</b></span>'+
      (RSPLIT?'<span class=\'trow\'><span>Reasoning</span><b>'+FN(tr)+'</b></span>':'')+
      '<span class=\'trow\'><span>Provider</span><b>'+ESC(prov)+'</b></span>'+
      ((!tot&&ok)?'<span class=\'trow\'><span>Note</span><b>unmetered, 0 tokens reported</b></span>':'')+
      ((free&&!wi)?'<span class=\'trow\'><span>Pricing</span><b>no rate on file, excluded from what-if</b></span>':'')+
      (free?'<span class=\'trow\'><span>What-if paid</span><b>$'+Number(wi).toFixed(4)+'</b></span>':''));
    return '<div class="model-row clickable'+(RFSTATE.model===name?' active':'')+'" data-rm="'+ESC(name)+'" data-tip="'+tipH+'">'+
      '<div><div class="mn">'+ESC(rModelShort(name))+(free?'<span class="free-tag">free</span>':'')+'</div><div class="ms">'+reqs+' req · '+(tot/totalAll*100).toFixed(1)+'% · '+ESC(prov)+'</div></div>'+
      '<div class="mbar">'+bar+'</div>'+
      '<div class="mv">'+FN(tot)+'</div></div>';
  }).join(''):'<div class="empty">No router models yet.</div>';
  $('r-model-legend').innerHTML='<span><i style="background:'+c.accent+'"></i>Uncached input</span><span><i style="background:'+c.cache+'"></i>Cached input</span><span><i style="background:'+c.accent2+'"></i>Output incl. reasoning</span>'+(RSPLIT?'<span><i style="background:'+c.reason+'"></i>Reasoning</span>':'')+'<span>click a model to filter requests</span>';
  $('r-insights').innerHTML=(R.insights||[]).map(ins=>'<div class="in"><p>'+BOLD(ESC(ins))+'</p></div>').join('');
  $('r-notes-hint').textContent=RRANGE==='all'?'generated from your codex data':'all-time notes · charts/tables follow the period filter';
  const pf=$('r-provider-f'),mf=$('r-model-f');
  pf.innerHTML='<option value="">All providers</option>'+(R.providers||[]).map(p=>'<option value="'+ESC(p[0])+'">'+ESC(p[0])+'</option>').join('');
  mf.innerHTML='<option value="">All models</option>'+(R.models||[]).map(m=>'<option value="'+ESC(m[0])+'">'+ESC(rModelShort(m[0]))+'</option>').join('');
  pf.value=RFSTATE.provider||'';mf.value=RFSTATE.model||'';
  renderRouterFilterBar();
  renderRouterRequests();
  renderRouterAct();
  const oc=(S&&S.totals?S.totals.tokens_total:0)||0;
  $('tab-opencode-cnt').textContent=FN(oc);
  $('tab-router-cnt').textContent=F(t.requests||0)+' '+(R.req_word_short||'req');
}
function routerRequestRows(){
  const q=(($('r-search')||{}).value||'').toLowerCase();
  const st=($('r-status-f')||{}).value||'';
  const cut=RRANGE==='all'?0:Date.now()-RRANGE*86400000;
  return (R.requests||[]).filter(r=>{
    if(RFSTATE.model&&r.model!==RFSTATE.model)return false;
    if(RFSTATE.provider&&r.provider!==RFSTATE.provider)return false;
    if(RFSTATE.day){
      const ms0=Date.parse(r.at||'');
      if(!ms0||new Date(ms0).toLocaleDateString('en-CA')!==RFSTATE.day)return false;
    }
    if(st==='ok'&&r.outcome!=='success')return false;
    if(st==='err'&&r.outcome!=='error')return false;
    if(st==='unknown'&&r.outcome!=='unknown')return false;
    if(st==='429'&&r.status!==429)return false;
    if(cut){
      const ms=Date.parse(r.at||'');
      if(ms&&ms<cut)return false;
    }
    if(q&&!(r.model||'').toLowerCase().includes(q)&&!(r.provider||'').toLowerCase().includes(q))return false;
    return true;});
}
function renderRouterRequests(){
  const rows=routerRequestRows();
  currentRRows=rows;
  $('r-req-count').textContent=F(rows.length)+' of '+F((R.requests||[]).length)+' shown';
  const rf=Object.values(RFSTATE).filter(Boolean).length;
  const rt=(R&&R.totals)||{};
  $('r-req-hint').textContent=(rf?'filtered by '+rf+' criteria · ':'')+'newest '+F(rows.length)+' of '+F(rt.requests||0)+' counted'+
    (R&&R.truncated?' (file capped at recent '+F(R.scanned||0)+')':'')+
    ' · '+F(rt.unmetered||0)+' unmetered excluded · errors show 0 tokens (actuals only) · '+F(rt.unknown||0)+' outcome unavailable';
  const tb=$('r-tbl').querySelector('tbody');
  tb.innerHTML=rows.map(r=>{
    const ok=r.ok;
    return '<tr><td class="mono" style="font-size:11.5px;color:var(--muted)">'+ESC((r.at||'').replace('T',' ').slice(0,19))+'</td>'+
    '<td><div class="t" style="font-size:12.5px">'+ESC(rModelShort(r.model))+'</div><div class="badges"><span class="badge">'+ESC(r.provider)+'</span></div></td>'+
    +(r.outcome==='unknown'?'<td><span class="mono" style="font-size:12px;color:var(--subtle)">Outcome unavailable</span></td>':'<td><span class="'+(ok?'status-ok':'status-err')+' mono" style="font-size:12px">'+r.status+'</span></td>')+
    '<td class="num">'+FN(r.ti||0)+'</td><td class="num">'+FN(r.to||0)+'</td><td class="num">'+FN(r.cache||0)+'</td>'+
    '<td class="num" style="font-weight:700">'+FN(r.total||0)+'</td><td class="num" style="color:var(--subtle)">'+F(r.ms||0)+'</td></tr>';}).join('')
    ||'<tr><td colspan="8"><div class="empty">No requests match.</div></td></tr>';
}
function setTab(name){
  TAB=name;
  const oc=name==='opencode';
  $('tab-opencode').classList.toggle('active',oc);
  $('tab-router').classList.toggle('active',!oc);
  $('tab-opencode').setAttribute('aria-selected',oc);
  $('tab-router').setAttribute('aria-selected',!oc);
  $('view-opencode').hidden=!oc;
  $('view-router').hidden=oc;
  try{sessionStorage.setItem('ocd-tab',name);}catch(_){}
  if(!oc)renderRouter();else renderAll();
}
function sortH(e){const th=e.target.closest('th');if(!th)return;const k=th.dataset.k;if(!k)return;
  if(k===sortK)sortAsc=!sortAsc;else{sortK=k;sortAsc=false;}
  document.querySelectorAll('#tbl th').forEach(t=>{
    t.classList.remove('asc','desc');
    t.setAttribute('aria-sort',t===th?(sortAsc?'ascending':'descending'):'none');});
  th.classList.add(sortAsc?'asc':'desc');renderSessions();}
function openSession(s){
  const tot=s.tokens_total||((s.tokens_input||0)+(s.tokens_output||0)+(s.tokens_reasoning||0)+(s.tokens_cache||0));
  const out=(s.tokens_output||0)+(s.tokens_reasoning||0);
  const t=T();
  const rows=[['Input',s.tokens_input||0,t.accent],['Output',s.tokens_output||0,t.accent2],['Reasoning',s.tokens_reasoning||0,t.reason],['Cache',s.tokens_cache||0,t.cache]];
  const bar=rows.filter(([l,v])=>v>0).map(([l,v,c])=>'<i style="width:'+(tot?v/tot*100:0)+'%;background:'+c+'" title="'+l+'"></i>').join('');
  const brow=rows.map(([l,v,c])=>'<div class="krow"><span class="lbl"><i class="dot" style="background:'+c+'"></i>'+l+'</span><b>'+FN(v)+' · '+(tot?Math.round(v/tot*100):0)+'%</b></div>').join('');
  lastFocus=document.activeElement;
  $('modal').innerHTML='<button class="modal-x" aria-label="Close">✕</button>'
  +'<h2 style="font-size:18px;font-weight:700;letter-spacing:-.02em">'+ESC(s.title)+'</h2>'
  +'<div class="mmeta">'+['<span class="badge agent">'+ESC(s.agent)+'</span>','<span class="badge model">'+ESC(s.model)+'</span>'+(s.provider?'<span class="badge">'+ESC(s.provider)+'</span>':''),
    '<span class="badge">'+DT(s.time_created)+'</span>','<span class="badge">'+DUR(s.duration_min)+'</span>',
    '<span class="badge">'+F(s.msgs)+' msgs</span>','<span class="badge">'+FN(tot)+' tokens</span>','<span class="badge">'+BURN(s.burn)+' burn</span>'].join('')+'</div>'
  +'<div class="card" style="margin-top:16px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--subtle);font-weight:700;margin-bottom:12px">Token breakdown</div>'
  +'<div class="tokbar" style="height:12px">'+bar+'</div>'+brow+'</div>'
  +'<div class="card" style="margin-top:12px"><div style="font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--subtle);font-weight:700;margin-bottom:12px">Prompts</div>'
  +'<div id="modal-prompts"><div class="empty">Loading prompts…</div></div></div>';
  $('overlay').classList.add('open');
  const x=$('modal').querySelector('.modal-x');if(x)x.focus();
  fetchJson('/api/session/'+encodeURIComponent(s.id)).then(pj=>{
    const el=$('modal-prompts');if(!el)return;
    const list=(pj&&pj.prompts)||[];
    el.innerHTML=list.length?list.slice().reverse().map(p=>'<div class="prompt"><span class="mono" style="font-size:10.5px;color:var(--subtle)">'+DT(p.t)+'</span><br>'+ESC(p.p)+'</div>').join(''):'<div class="empty">No prompts recorded.</div>';
  }).catch(e=>{if(e&&e.status===401)noteAuthFailure();const el=$('modal-prompts');if(el)el.innerHTML='<div class="empty">'+(e&&e.status===401?'Authorization required — reopen via the launcher link':ESC('Could not load prompts: '+((e&&e.message)||'error')+' (reopen the session to retry).'))+'</div>';});
}
/* interactions */
let lastFocus=null;
document.addEventListener('mousemove',e=>{
  const el=e.target.closest?e.target.closest('[data-tip]'):null;
  if(el&&el.dataset.tip){tipShow(el.dataset.tip,e);}
  else if(tip.style.display==='block'){tipHide();}
});
document.addEventListener('focusin',e=>{
  const el=e.target.closest?e.target.closest('[data-tip]'):null;
  if(el&&el.dataset.tip){tip.innerHTML=el.dataset.tip;tipAt(el);}
});
console.log('%c made by zX ','background:#0a0a0a;color:#22ff88;font-weight:bold');
document.addEventListener('focusout',()=>tipHide());
document.addEventListener('click',e=>{
  if(!e.target.closest('.export-wrap'))$('expmenu').classList.remove('open');
  const el=e.target.closest?e.target.closest('[data-f]'):null;
  if(!el)return;
  const idx=el.dataset.f.indexOf('|');
  const k=el.dataset.f.slice(0,idx),v=el.dataset.f.slice(idx+1);
  if(k==='day'&&!v)return;
  FSTATE[k]=FSTATE[k]===v?null:v;
  if(k==='agent'&&FSTATE.agent!==null)$('agent-f').value=FSTATE.agent;
  if(k==='model'&&FSTATE.model!==null)$('model-f').value=FSTATE.model;
  renderAll();
});
$('agent-f').addEventListener('change',e=>{FSTATE.agent=e.target.value||null;renderAll();});
$('model-f').addEventListener('change',e=>{FSTATE.model=e.target.value||null;renderAll();});
document.querySelectorAll('.pbtn[data-fs]').forEach(b=>b.onclick=()=>{
  FS=b.dataset.fs;FS_EXP=false;
  document.querySelectorAll('.pbtn[data-fs]').forEach(x=>x.classList.toggle('active',x===b));
  renderFileChart();
});
document.querySelectorAll('.pbtn[data-cs]').forEach(b=>b.onclick=()=>{
  CS=b.dataset.cs;CS_EXP=false;
  document.querySelectorAll('.pbtn[data-cs]').forEach(x=>x.classList.toggle('active',x===b));
  renderCommitChart();
});
document.querySelectorAll('.pbtn[data-rfs]').forEach(b=>b.onclick=()=>{
  RFS=b.dataset.rfs;RFS_EXP=false;
  document.querySelectorAll('.pbtn[data-rfs]').forEach(x=>x.classList.toggle('active',x===b));
  renderRouterFileChart();
});
document.querySelectorAll('.pbtn[data-rcs]').forEach(b=>b.onclick=()=>{
  RCS=b.dataset.rcs;RCS_EXP=false;
  document.querySelectorAll('.pbtn[data-rcs]').forEach(x=>x.classList.toggle('active',x===b));
  renderRouterCommitChart();
});
document.addEventListener('click',e=>{
  const b=e.target.closest?e.target.closest('[data-exp]'):null;
  if(!b)return;
  const k=b.dataset.exp;
  if(k==='fs'){FS_EXP=!FS_EXP;renderFileChart();}
  else if(k==='cs'){CS_EXP=!CS_EXP;renderCommitChart();}
  else if(k==='rfs'){RFS_EXP=!RFS_EXP;renderRouterFileChart();}
  else if(k==='rcs'){RCS_EXP=!RCS_EXP;renderRouterCommitChart();}
});
document.querySelectorAll('.pbtn[data-r]').forEach(b=>b.onclick=()=>{
  RANGE=b.dataset.r;
  document.querySelectorAll('.pbtn[data-r]').forEach(x=>x.classList.toggle('active',x===b));
  renderAll();
});
$('split-btn').onclick=()=>{SPLIT=!SPLIT;$('split-btn').textContent='Split reasoning: '+(SPLIT?'on':'off');$('split-btn').setAttribute('aria-pressed',SPLIT);renderAll();};
$('share-btn').onclick=()=>{SHARE=!SHARE;$('share-btn').textContent='Share: '+(SHARE?'on':'off');$('share-btn').setAttribute('aria-pressed',SHARE);renderAll();};
document.querySelectorAll('.pbtn[data-rr]').forEach(b=>b.onclick=()=>{
  RRANGE=b.dataset.rr;
  document.querySelectorAll('.pbtn[data-rr]').forEach(x=>x.classList.toggle('active',x===b));
  renderRouter();
});
$('r-split-btn').onclick=()=>{RSPLIT=!RSPLIT;$('r-split-btn').textContent='Split reasoning: '+(RSPLIT?'on':'off');$('r-split-btn').setAttribute('aria-pressed',RSPLIT);renderRouter();};
$('r-share-btn').onclick=()=>{RSHARE=!RSHARE;$('r-share-btn').textContent='Share: '+(RSHARE?'on':'off');$('r-share-btn').setAttribute('aria-pressed',RSHARE);renderRouter();};
$('tab-opencode').onclick=()=>setTab('opencode');
$('tab-router').onclick=()=>setTab('router');
$('r-search').addEventListener('input',renderRouterRequests);
$('r-status-f').addEventListener('change',e=>{RFSTATE.status=e.target.value||'';renderRouter();});
$('r-provider-f').addEventListener('change',e=>{RFSTATE.provider=e.target.value||null;renderRouter();});
$('r-model-f').addEventListener('change',e=>{RFSTATE.model=e.target.value||null;renderRouter();});
document.addEventListener('click',e=>{
  const rm=e.target.closest?e.target.closest('[data-rm]'):null;
  if(rm){RFSTATE.model=RFSTATE.model===rm.dataset.rm?null:rm.dataset.rm;$('r-model-f').value=RFSTATE.model||'';renderRouter();return;}
  const rp=e.target.closest?e.target.closest('[data-rp]'):null;
  if(rp){RFSTATE.provider=RFSTATE.provider===rp.dataset.rp?null:rp.dataset.rp;$('r-provider-f').value=RFSTATE.provider||'';renderRouter();return;}
  const rs=e.target.closest?e.target.closest('[data-rs]'):null;
  if(rs){const k=rs.dataset.rs;RFSTATE.status=RFSTATE.status===k?null:k;$('r-status-f').value=RFSTATE.status||'';renderRouter();return;}
  const rd=e.target.closest?e.target.closest('[data-rday]'):null;
  if(rd){RFSTATE.day=RFSTATE.day===rd.dataset.rday?null:rd.dataset.rday;renderRouter();return;}
});
/* session rows: event delegation + keyboard */
$('tbl').addEventListener('click',e=>{const tr=e.target.closest('tbody tr[data-i]');if(!tr)return;
  const s=currentRows[+tr.dataset.i];if(s)openSession(s);});
$('tbl').addEventListener('keydown',e=>{if(e.key!=='Enter'&&e.key!==' '&&e.key!=='Spacebar')return;
  const tr=e.target.closest('tbody tr[data-i]');if(!tr)return;e.preventDefault();
  const s=currentRows[+tr.dataset.i];if(s)openSession(s);});
document.querySelectorAll('#tbl th').forEach(th=>{th.onclick=sortH;th.tabIndex=0;th.setAttribute('role','button');
  th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sortH(e);}});});
/* export */
function exportData(kind){
  const isR=TAB==='router'&&R;
  if(kind==='json'){
    const b=new Blob([JSON.stringify(isR?R:S,null,2)],{type:'application/json'});
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=isR?'codex-tokens.json':'opencode-tokens.json';a.click();
  }else if(kind==='csvc'){
    const rows=(isR?(R.commits||[]):(S.commits||[]));
    const meta=((isR?R:S).commit_windows)||{};
    const head=['sha','subject','date','project','files','add','del','window_tokens','window_events','method','scope','overlap_possible','additive','window_hours','note'];
    const q=v=>'"'+String(v==null?'':v).replace(/"/g,'""')+'"';
    const lines=[head.join(',')].concat(rows.map(r=>r.slice(0,9).join(',')+','+[meta.method||'',meta.scope||'',meta.overlap_possible===undefined?'':meta.overlap_possible,meta.additive===undefined?'':meta.additive,meta.window_hours==null?'':meta.window_hours,meta.note||''].map(q).join(',')));
    const b=new Blob([lines.join('\n')],{type:'text/csv'});
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=isR?'codex-tokens-commits.csv':'opencode-tokens-commits.csv';a.click();
  }else if(isR){
    const rows=rPeriodDays();
    const head=['date','requests','ok','errors','unknown','tokens_in','tokens_out','tokens_cache','tokens_total','what_if_cost'];
    const lines=[head.join(',')].concat(rows.map(d=>[d.date,d.reqs,d.ok,d.err,(d.unknown||0),Math.round(d.ti),Math.round(d.to),Math.round(d.cache),Math.round(rDayTokenTotal(d)),(d.what_if||0)].join(',')));
    const b=new Blob([lines.join('\n')],{type:'text/csv'});
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='codex-tokens-days.csv';a.click();
  }else{
    const rows=periodDays();
    const head=['date','messages','sessions','tokens_in','tokens_out','tokens_reasoning','tokens_cache','tokens_total'];
    const lines=[head.join(',')].concat(rows.map(d=>[d.date,d.msgs,d.sessions,Math.round(d.ti),Math.round(d.to),Math.round(d.tr),Math.round(d.cache),Math.round(dayTotal(d))].join(',')));
    const b=new Blob([lines.join('\n')],{type:'text/csv'});
    const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='opencode-tokens-days.csv';a.click();
  }
}
$('export').onclick=e=>{e.stopPropagation();$('expmenu').classList.toggle('open');};
document.querySelectorAll('#expmenu button').forEach(b=>b.onclick=()=>{exportData(b.dataset.x);$('expmenu').classList.remove('open');});
/* stop the hidden server when this window closes or quits (Cmd+Q) */
/* F2: authenticated fetch with keepalive; sendBeacon cannot carry the
   Authorization header, so it must not be used. The existing idle
   timeout/monitor remains the fallback if unload is not delivered. */
function stopServer(){if(!AUTH_TOKEN)return;try{authFetch('/api/close?wid='+encodeURIComponent(WID),{method:'POST',keepalive:true});}catch(_){}}
window.addEventListener('beforeunload',stopServer);
window.addEventListener('pagehide',stopServer);
/* modals */
function closeOverlay(id){const ov=$(id);if(ov&&ov.classList.contains('open')){ov.classList.remove('open');if(lastFocus&&lastFocus.focus)lastFocus.focus();}}
$('overlay').onclick=e=>{if(e.target.id==='overlay')closeOverlay('overlay');};
$('modal').addEventListener('click',e=>{if(e.target.classList.contains('modal-x'))closeOverlay('overlay');});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){closeOverlay('overlay');return;}
  if(e.key==='Tab'){
    const open=$('overlay').classList.contains('open')?$('overlay'):null;
    if(!open)return;
    const els=[...open.querySelectorAll('button,input,select,a,[tabindex]')].filter(el=>!el.disabled&&el.tabIndex>=0);
    if(!els.length)return;
    if(e.shiftKey&&document.activeElement===els[0]){e.preventDefault();els[els.length-1].focus();}
    else if(!e.shiftKey&&document.activeElement===els[els.length-1]){e.preventDefault();els[0].focus();}
  }
});
/* boot (F6d): independent per-section refresh, deadline and honest status */
let loading=false,pendingRefresh=false;
const SEC=window.__ocdState={sections:{},cycle:{}};
const SECTIONS=[
 {key:'stats',url:'/api/stats',validate:d=>d&&typeof d==='object'&&!d.error&&('totals' in d||'days' in d||'day_total' in d),render:d=>{S=d;renderAll();}},
 {key:'router',url:'/api/router',validate:d=>d&&typeof d==='object'&&!d.error&&('totals' in d||'days' in d||'day_total' in d),render:d=>{R=d;renderRouter();}},
 {key:'agents',url:'/api/agents',validate:d=>d&&typeof d==='object'&&!d.error&&Array.isArray(d.child_runs),render:d=>{SA=d;renderSubagents();renderConfig();}},
 {key:'graph',url:'/api/graph',validate:d=>d&&typeof d==='object'&&!d.error&&d.nodes&&Array.isArray(d.roots),render:d=>{G=d;renderGraph();}},
 {key:'sessions',url:'/api/sessions',validate:d=>d&&typeof d==='object'&&!d.error&&Array.isArray(d.rows),render:d=>{SB=d;renderSSTable();}},
 {key:'projects',url:'/api/projects',validate:d=>d&&typeof d==='object'&&!d.error&&Array.isArray(d.rows),render:d=>{PJ=d;renderProjects();}},
 {key:'signals',url:'/api/signals',validate:d=>d&&typeof d==='object'&&!d.error&&Array.isArray(d.signals),render:d=>{SG=d;renderSignals();}},
 {key:'codex',url:'/api/codex',validate:d=>d&&d.ok===true&&Array.isArray(d.sessions),render:d=>{CX=d;renderCodex();}}
];
function secUnavailable(key,msg){
  var tb=(key==='sessions')?document.querySelector('#sstbl tbody'):((key==='projects')?document.querySelector('#projtbl tbody'):null);
  if(tb)tb.innerHTML='<tr><td colspan="5">unavailable: '+ESC(msg)+'</td></tr>';
  if(key==='graph'){var g=document.getElementById('graph');if(g)g.textContent='unavailable: '+msg;}
  if(key==='signals'){var s=document.getElementById('sig-chips');if(s)s.textContent='unavailable: '+msg;}
  if(key==='codex'){var c=document.querySelector('#cx-tbl tbody');if(c)c.innerHTML='<tr><td colspan="6">unavailable: '+ESC(msg)+'</td></tr>';}
}
function secStrip(){
  var sts=SEC.sections,parts=[];
  SECTIONS.forEach(function(s){var st=sts[s.key];if(!st)return;var t=s.key+': '+st.state;
    if(st.error)t+=' ('+st.error+')';
    if((st.state==='stale'||st.state==='error')&&st.lastSuccess)t+=' last ok '+new Date(st.lastSuccess).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    parts.push(t);});
  var el=document.getElementById('src-status');if(el){el.textContent=parts.join(' · ');el.title=parts.join('; ');}
}
async function load(){
  if(loading){pendingRefresh=true;return;}
  if(AUTH_FAILED)return;
  if(!AUTH_TOKEN){showAuthLock(true);return;}
  loading=true;pendingRefresh=false;$('refresh').disabled=true;
  SEC.cycle={started:Date.now(),ok:0,failed:0,total:SECTIONS.length};
  var results=await Promise.allSettled(SECTIONS.map(runSection));
  var ok=0;for(var i=0;i<results.length;i++){if(results[i].status==='fulfilled'&&results[i].value)ok++;}
  SEC.cycle.finished=Date.now();SEC.cycle.ok=ok;SEC.cycle.failed=SECTIONS.length-ok;
  if(ok===SECTIONS.length&&!AUTH_FAILED){$('status').textContent='Updated '+new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});}
  else{var bad=[];SECTIONS.forEach(function(s){var st=SEC.sections[s.key];if(st&&st.state!=='success')bad.push(s.key);});
    $('status').textContent='Partial update: '+ok+'/'+SECTIONS.length+' sources'+(bad.length?(' (failed: '+bad.join(', ')+')'):'');}
  secStrip();
  loading=false;$('refresh').disabled=false;
  if(pendingRefresh&&!AUTH_FAILED){pendingRefresh=false;load();}
}
async function runSection(s){
  var st=SEC.sections[s.key];
  if(!st){st=SEC.sections[s.key]={state:'idle',gen:0,inFlight:false,lastSuccess:0,error:null,data:null,ac:null};}
  if(st.inFlight)return true;
  st.inFlight=true;st.gen++;var gen=st.gen;
  st.state=st.lastSuccess?'stale':'pending';
  var ac=('AbortController' in window)?new AbortController():null;st.ac=ac;
  try{
    var d=await fetchJson(s.url+((s.url.indexOf('?')<0)?'?':'&')+'_='+Date.now(),ac?{signal:ac.signal}:{});
    if(!s.validate(d))throw new Error('unexpected payload shape');
    if(gen!==st.gen)return true;
    /* A01/A05: a retained snapshot with a failed refresh is stale (never a
       plain success, and it must not advance lastSuccess); an explicitly
       skipped raw record is partial coverage of the source. Both stay
       visible in the data and in the source strip. */
    var staleMsg=null,partialMsg=null;
    if(d&&d.synth_stale){staleMsg='stale snapshot: '+(d.synth_error||'source refresh failed');}
    if(d&&d.synth_oversize_records>0){partialMsg='partial source coverage: '+d.synth_oversize_records+' oversize record(s) skipped';}
    if(staleMsg){
      st.data=d;st.state='stale';st.error=staleMsg;
      s.render(d);
      console.warn('ocd source '+s.key+' stale: '+staleMsg);
      return false;
    }
    st.data=d;st.state='success';st.lastSuccess=Date.now();
    st.error=null;if(partialMsg)st.error=partialMsg;
    s.render(d);
    if(partialMsg){console.warn('ocd source '+s.key+' partial: '+partialMsg);return false;}
    return true;
  }catch(e){
    if(e&&e.status===401){noteAuthFailure();return false;}
    if(gen!==st.gen)return true;
    st.error=(e&&e.message)?e.message:String(e);
    if(st.lastSuccess){st.state='stale';}else{st.state='error';}
    if(!st.lastSuccess)secUnavailable(s.key,st.error);
    console.warn('ocd source '+s.key+' failed: '+st.error);
    return false;
  }finally{st.inFlight=false;st.ac=null;}
}
var G=null,SB=null,PJ=null,SG=null;
function agoMs(v){if(v==null)return '?';var ms=(v>1e12)?v:(v*1000);var s=Math.max(0,Math.floor((Date.now()-ms)/1000));if(s<60)return s+' s ago';var m=Math.floor(s/60);if(m<60)return m+' min ago';var h=Math.floor(m/60);if(h<48)return h+' h ago';return Math.floor(h/24)+' d ago';}
function fmtT(v){if(v==null)return '?';var ms=(v>1e12)?v:(v*1000);try{return new Date(ms).toLocaleString([],{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}catch(_){return String(v);}}
function renderGraph(){var el=document.getElementById('graph');if(!el)return;if(!G||G.error){el.textContent=(G&&G.error)?('ERR '+G.error):'no data';return;}var H='',count=0;function walk(id,depth){if(count>300||depth>5)return;var n=G.nodes[id];if(!n)return;count++;H+='<div data-sid="'+n.id+'" style="padding-left:'+(depth*18)+'px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><b>'+ESC(n.agent)+'</b> <span style="opacity:.65">'+ESC(n.title)+' · '+agoMs(n.updated)+'</span></div>';var kids=G.kids[id]||[];for(var i=0;i<kids.length;i++)walk(kids[i],depth+1);}for(var i=0;i<G.roots.length&&count<=300;i++)walk(G.roots[i],0);el.innerHTML='<div style="opacity:.65;margin-bottom:6px">'+G.roots.length+' roots, nodes: '+Object.keys(G.nodes).length+'</div>'+H;}
function renderSSTable(){var tb=document.querySelector('#sstbl tbody');if(!tb)return;if(!SB||SB.error){tb.innerHTML='<tr><td colspan="5">ERR</td></tr>';return;}var H='';for(var i=0;i<SB.rows.length;i++){var r=SB.rows[i];H+='<tr data-sid="'+r.id+'" style="cursor:pointer"><td>'+ESC(r.title)+'</td><td>'+ESC(r.agent||'')+'</td><td>'+ESC(r.model||'')+(r.provider?' · '+ESC(r.provider):'')+'</td><td>'+ESC(r.directory||'')+'</td><td>'+fmtT(r.time_created)+'</td></tr>';}if(!H)H='<tr><td colspan="5">no results</td></tr>';tb.innerHTML=H;}
var _ssGen=0;
async function fetchSessions(){var p='q='+encodeURIComponent($('ss-q').value)+'&agent='+encodeURIComponent($('ss-agent').value)+'&model='+encodeURIComponent($('ss-model').value);var g=++_ssGen;try{var d=await fetchJson('/api/sessions?'+p);if(g!==_ssGen)return;SB=d;renderSSTable();}catch(e){if(e&&e.status===401){noteAuthFailure();return;}if(g!==_ssGen)return;console.warn('ocd sessions search failed: '+((e&&e.message)||e));}}
function renderProjects(){var tb=document.querySelector('#projtbl tbody');if(!tb)return;if(!PJ||PJ.error){tb.innerHTML='<tr><td colspan="5">ERR</td></tr>';return;}var H='';for(var i=0;i<PJ.rows.length;i++){var r=PJ.rows[i];var nm=r.name||r.directory||r.worktree||r.id;H+='<tr><td>'+ESC(nm)+'</td><td>'+ESC(r.directory||r.worktree||'')+'</td><td>'+ESC(r.vcs||'')+'</td><td class="num">'+(r.sessions||0)+'</td><td>'+fmtT(r.last)+'</td></tr>';}if(!H)H='<tr><td colspan="5">no projects</td></tr>';tb.innerHTML=H;}
function renderSignals(){var el=document.getElementById('sig-chips');if(!el)return;if(!SG||SG.error){el.textContent='ERR';return;}var H='';for(var i=0;i<SG.signals.length;i++){var s=SG.signals[i];var c=(s.level==='warn')?'#a6761d':((s.level==='err')?'#c00':'#16a34a');H+='<span style="display:inline-block;padding:2px 10px;border:1px solid '+c+';border-radius:12px;margin:2px;color:'+c+'">'+ESC(s.text)+'</span>';}if(!H)H='<span>all clear</span>';el.innerHTML=H;}
var _inspGen=0;
const _inspState={sid:null,cursor:null,hasMore:false,pending:null};
async function inspect(sid){var box=document.getElementById('insp');if(!box)return;box.hidden=false;var g=++_inspGen;if(_inspState.sid!==sid){_inspState.sid=sid;_inspState.cursor=null;_inspState.hasMore=false;_inspState.pending=null;}box.textContent='loading '+sid+' ...';try{var u='/api/inspect?id='+encodeURIComponent(sid)+'&_='+Date.now();if(_inspState.pending)u+='&cursor='+encodeURIComponent(_inspState.pending);var d=await fetchJson(u);if(g!==_inspGen)return;if(!d||!d.found){box.textContent='not found '+sid+' (click the row to retry)';return;}var H='<b>'+ESC(d.title||sid)+'</b> <span style="opacity:.65">'+ESC(d.agent||'')+' / '+ESC(d.model||'')+(d.provider?' ('+ESC(d.provider)+')':'')+' / '+ESC(d.directory||'')+'</span>';for(var i=0;i<d.messages.length;i++){var m=d.messages[i];H+='<div style="margin-top:8px"><b>'+ESC(m.role)+'</b> <span style="opacity:.65">'+ESC(m.agent||'')+' '+ESC(m.model||'')+' · '+m.parts.length+' parts'+(m.parts_omitted?(' (+'+m.parts_omitted+' omitted)'):'')+'</span>';if(m.summary)H+='<div>'+ESC(m.summary)+'</div>';for(var j=0;j<m.parts.length;j++){var p=m.parts[j];H+='<div style="margin-left:12px;opacity:.85">['+ESC(p.type)+'] '+p.size+' B'+(p.preview?(', '+ESC(String(p.preview).slice(0,300))+(p.truncated?' …[truncated]':'')+(p.raw_fragment?' [raw fragment]':'')):'')+'</div>';}H+='</div>';}H+='<div style="margin-top:10px;opacity:.75;font-size:11px">'+d.count+' messages shown'+((d.parts_truncated)?' · some parts truncated':'')+((d.response_truncated)?' · response shortened':'')+'</div>';if(d.has_more){H+='<div style="margin-top:6px"><button id="insp-more" class="tbtn">Load next</button></div>';}box.innerHTML=H;if(d.has_more){_inspState.cursor=d.cursor;_inspState.hasMore=true;var b=document.getElementById('insp-more');if(b)b.onclick=function(){_inspState.pending=_inspState.cursor;inspect(_inspState.sid);};}else{_inspState.hasMore=false;}box.scrollIntoView();}catch(e){if(e&&e.status===401){noteAuthFailure();box.textContent='Authorization required — reopen via the launcher link';return;}if(g!==_inspGen)return;box.textContent='ERR '+e.message+' (click the row to retry)';}}
if(!window.__p1wire){window.__p1wire=1;document.addEventListener('click',function(e){var t=(e.target&&e.target.closest)?e.target.closest('[data-sid]'):null;if(t)inspect(t.getAttribute('data-sid'));});['ss-q','ss-agent','ss-model'].forEach(function(id){var el=document.getElementById(id);if(el)el.addEventListener('input',function(){if(window.__p1t)clearTimeout(window.__p1t);window.__p1t=setTimeout(fetchSessions,350);});});}
$('refresh').onclick=()=>{if(AUTH_FAILED)return;load();};
$('search').addEventListener('input',renderSessions);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!AUTH_FAILED)load();});
setInterval(()=>{if(!document.hidden&&!AUTH_FAILED)load();},((window.__ocdTimeouts&&window.__ocdTimeouts.refreshMs)||30000));
try{const saved=sessionStorage.getItem('ocd-tab');if(saved==='router'){setTab('router');}}catch(_){}
if(S){renderAll();}
if(R){renderRouter();}
if(AUTH_TOKEN){load();}else{showAuthLock(true);}
</script>
</body>
</html>"""


def _cancel_close_timer():
    with LOCK:
        if CLOSE_TIMER:
            CLOSE_TIMER.cancel()


class Handler(BaseHTTPRequestHandler):
    db_path = None
    router_events = None
    router_limits = None
    server = None
    quiet = False

    def _cancel_close(self):
        _cancel_close_timer()

    @staticmethod
    def _close_now():
        if Handler.server:
            Handler.server.shutdown()

    def _arm_close(self):
        global CLOSE_TIMER
        with LOCK:
            if CLOSE_TIMER:
                CLOSE_TIMER.cancel()
            CLOSE_TIMER = threading.Timer(3.0, Handler._close_now)
            CLOSE_TIMER.daemon = True
            CLOSE_TIMER.start()

    def _check_origin(self):
        """Strict Origin check against the real server address (F2).

        Missing Origin is allowed (local script client) but never counts
        as authentication. Present Origin must equal the exact allowed
        origin including scheme and port. "null", foreign origins and
        multiple values are rejected. Kept for backward-compatible call
        sites; new code prefers _check_origin_strict().
        """
        return self._check_origin_strict()

    def _check_origin_strict(self):
        vals = _header_all(self, "Origin")
        if not vals:
            return True
        if len(vals) != 1:
            return False
        raw = vals[0]
        if raw is None:
            return True
        raw = raw.strip()
        if not raw or "," in raw:
            return False
        return raw == _expected_origin(self.server)

    def _check_host(self):
        """Validate Host against the real bound address. Returns None when
        OK, 400 for missing/multiple, 403 for a disallowed value."""
        vals = _header_all(self, "Host")
        expanded = []
        for v in vals:
            if v is None:
                continue
            if "," in v:
                return 400
            expanded.append(v)
        if len(expanded) != 1:
            return 400
        raw = expanded[0]
        if raw is None or not raw.strip():
            return 400
        if raw.strip() != _expected_host(self.server):
            return 403
        return None

    def _check_auth(self):
        """All private /api/* routes need Authorization: Bearer <token>."""
        vals = _header_all(self, "Authorization")
        if len(vals) != 1:
            return False
        raw = vals[0]
        if raw is None:
            return False
        raw = raw.strip()
        if not raw.startswith("Bearer "):
            return False
        cand = raw[7:].strip()
        if not cand or "," in cand or " " in cand or "\t" in cand:
            return False
        if not _TOKEN_RE.fullmatch(cand):
            return False
        try:
            expected = self.server.auth_token
        except AttributeError:
            expected = getattr(Handler.server, "auth_token", None)
        if not expected or not isinstance(expected, str):
            return False
        try:
            return secrets.compare_digest(cand, expected)
        except Exception:
            return False

    def _deny(self, code):
        if code == 400:
            self._send(400, "text/plain", b"bad request")
        elif code == 401:
            self._send(401, "application/json", b'{"error":"unauthorized"}')
        elif code == 403:
            self._send(403, "application/json", b'{"error":"forbidden"}')
        elif code == 405:
            self._send(405, "text/plain", b"method not allowed")
        else:
            self._send(code, "text/plain", b"error")

    def _guard(self, path):
        """Shared F2 guard: Host (400/403), Origin (403), auth for /api/*.

        Returns None when the request may proceed, else the denial code
        that was already sent. Never touches state, disk, or timers.
        """
        bad = self._check_host()
        if bad is not None:
            self._deny(bad)
            return bad
        if not self._check_origin_strict():
            self._deny(403)
            return 403
        if _is_private_path(path):
            if not self._check_auth():
                self._deny(401)
                return 401
        return None

    def _unsupported_method(self):
        try:
            path = urlparse(self.path).path
        except Exception:
            path = "/"
        if self._guard(path) is not None:
            return
        self._deny(405)

    def __getattr__(self, name):
        if name.startswith("do_"):
            return object.__getattribute__(self, "_unsupported_method")
        raise AttributeError(name)

    def _touch(self, wid=None):
        with LOCK:
            now = time.time()
            globals()["LAST_REQUEST"] = now
            if wid:
                WINDOWS[wid] = now
                globals()["HAD_WINDOW"] = True

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        # F2: Host/Origin/auth run before any read, synth, window
        # registration, _touch, or close handling.
        if self._guard(path) is not None:
            return
        wid = (query.get("wid") or [""])[0] or self.headers.get("X-Window-Id", "")
        if path.startswith(("/api/", "/")):
            self._cancel_close()
            self._touch(wid or None)
        if path in ("/logo.png", "/favicon.ico"):
            try:
                _lp = Path(__file__).parent / "logo.png"
                self._send(200, "image/png", _lp.read_bytes())
            except OSError:
                self._send(404, "text/plain", b"no logo")
            return
        elif path in ("/", "/index.html"):
            # F2: public shell carries no private data and no token.
            # Initial payloads are null; the browser fetches real data
            # through the authenticated API. No DB/synth read happens here.
            body = PAGE.replace("__DATA__", "null").replace("__ROUTER__", "null").encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
        elif path.startswith("/api/session/"):
            session_id = path[len("/api/session/"):]
            if not session_id:
                self._send(404, "application/json", b'{"error":"missing session id"}')
                return
            try:
                con = connect(self.db_path)
                try:
                    prompts = session_prompts(con, session_id)
                finally:
                    con.close()
                self._send(200, "application/json", json.dumps({"prompts": prompts}).encode("utf-8"))
            except Exception as e:
                self._send(404, "application/json", json.dumps({"error": str(e)}).encode("utf-8"))
        elif path.startswith("/api/stats"):
            try:
                stats = cached_local_stats(self.db_path)
            except Exception as e:
                stats = blank_stats("local", "Local OpenCode data", error=str(e))
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/router"):
            try:
                _rep, _rsyn = router_events_path(self.router_events)
                try:
                    _rlim = self.router_limits if Path(self.router_limits).is_file() else None
                except (TypeError, OSError):
                    _rlim = None
                stats = cached_router_stats(_rep, _rlim,
                                              db_worktrees(self.db_path), _rsyn)
                if _rsyn and isinstance(stats, dict):
                    stats["source"] = "codex-synth"
                    stats["source_label"] = "Codex sessions (synthesized)"
                    if _codex_last_error:
                        # Audit A01: the refresh failed; the payload is the
                        # last good snapshot and says so explicitly.
                        stats["synth_error"] = _codex_last_error
                        stats["synth_stale"] = True
                    stats["synth_oversize_records"] = _codex_last_oversize
            except Exception as e:
                stats = blank_router_stats(error=str(e))
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/codex"):
            try:
                force = ("refresh=1" in (self.path or ""))
                body = json.dumps(query_codex(force=force)).encode("utf-8")
            except Exception as e:
                body = json.dumps({"ok": False, "error": str(e)[:200]}).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/agents"):
            try:
                con = connect(self.db_path)
                try:
                    stats = query_agents(con)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "child_runs": [], "child_agents": [], "child_total": 0,
                         "listed_count": 0, "total_child_sessions": 0, "limit": 100, "truncated": False,
                         "activity_counts": {"recent": 0, "quiet": 0, "stale": 0, "unknown": 0},
                         "all_agents": [], "config": []}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/graph"):
            try:
                con = connect(self.db_path)
                try:
                    stats = query_graph(con)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "nodes": {}, "kids": {}, "roots": []}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/inspect"):
            try:
                pq = parse_qs(urlparse(self.path).query)
                sid = (pq.get("id") or [""])[0]
                cursor = (pq.get("cursor") or [None])[0]
                limit = (pq.get("limit") or [None])[0]
                con = connect(self.db_path)
                try:
                    stats = query_inspect(con, sid, cursor=cursor, limit=limit)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "found": False}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/sessions"):
            try:
                pq = parse_qs(urlparse(self.path).query)
                q = (pq.get("q") or [""])[0]
                ag = (pq.get("agent") or [""])[0]
                mo = (pq.get("model") or [""])[0]
                con = connect(self.db_path)
                try:
                    stats = query_sessions(con, q, ag, mo)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "rows": []}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/projects"):
            try:
                con = connect(self.db_path)
                try:
                    stats = query_projects(con)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "rows": []}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        elif path.startswith("/api/signals"):
            try:
                con = connect(self.db_path)
                try:
                    stats = query_signals(con)
                finally:
                    con.close()
            except Exception as e:
                stats = {"error": str(e), "signals": []}
            body = json.dumps(stats).encode("utf-8")
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        # F2: same guard as GET — Host, Origin, then auth for /api/* —
        # before any window or close handling.
        if self._guard(path) is not None:
            return
        if path == "/api/close":
            wid = (query.get("wid") or [""])[0]
            if not wid:
                wid = self.headers.get("X-Window-Id", "")
            # F2-T09: pop before responding so any client that receives the
            # 200 is guaranteed the window is already gone (no send/pop race).
            with LOCK:
                if wid:
                    WINDOWS.pop(wid, None)
                now = time.time()
                others = any(now - last < 90 for w, last in WINDOWS.items())
            self._send(200, "text/plain", b"bye")
            if not others:
                self._arm_close()
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Made-By", "zX")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(body)
        except ConnectionError:
            # Client went away mid-response (tab reload/close on Windows
            # surfaces as ConnectionAbortedError, a sibling - not a child -
            # of ConnectionResetError). Not a server fault: stay quiet.
            pass

    def log_message(self, fmt, *args):
        if not Handler.quiet:
            print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def _idle_monitor(server, idle_timeout):
    """Exit when the app is abandoned: all dashboard windows closed, or the
    --idle-timeout grace period elapsed with no requests at all."""
    while True:
        time.sleep(5)
        with LOCK:
            now = time.time()
            last = LAST_REQUEST
            windowless = HAD_WINDOW and not WINDOWS
            if idle_timeout > 0 and now - last > idle_timeout:
                stop = True
            elif windowless and now - last > 120:
                stop = True
            else:
                stop = False
        if stop:
            try:
                server.shutdown()
            except Exception:
                pass
            return


def main():
    parser = argparse.ArgumentParser(description="OpenCode usage dashboard")
    parser.add_argument("db", nargs="?", default=str(DB_PATH), help="path to opencode.db")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port to serve on")
    parser.add_argument(
        "--idle-timeout", type=int, default=0, metavar="SECONDS",
        help="shut the server down after this many seconds with no requests (0 = off)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress per-request logging")
    parser.add_argument("--agents-dir", action="append", default=[], metavar="DIR",
                        help="extra dir with agent .md files (repeatable)")
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser on start")
    parser.add_argument("--router-events", default=str(ROUTER_EVENTS_DEFAULT),
                        help="path to Codex Router usage-events.jsonl")
    parser.add_argument("--router-limits", default=str(ROUTER_LIMITS_DEFAULT),
                        help="path to Codex Router rate-limits.json")
    args = parser.parse_args()

    Handler.db_path = args.db
    Handler.router_events = args.router_events
    Handler.router_limits = args.router_limits
    Handler.quiet = args.quiet
    if args.agents_dir:
        AGENT_DIRS.extend(args.agents_dir)

    try:
        connect(args.db)
    except RuntimeError as e:
        print(f"warning: {e}", file=sys.stderr)
        print("The dashboard will still start and show a setup page once OpenCode has written data.", file=sys.stderr)

    server = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{args.port} ({e}).\n"
              "Another dashboard instance is probably still running: close its\n"
              "console window and start again. The launcher (start-dashboard.bat)\n"
              "retires a stale instance automatically.", file=sys.stderr)
        raise SystemExit(2)
    Handler.server = server
    try:
        server.auth_token = _new_token()
    except Exception:
        pass
    try:
        Handler.auth_token = server.auth_token
    except Exception:
        pass
    _host, _real_port = server.server_address[:2]
    url = f"http://127.0.0.1:{_real_port}"
    url_with_token = f"{url}/#token={server.auth_token}"
    print(f"Dashboard live at {url}  (Ctrl+C to stop)")
    # F2: the operator opens the fragment address once; the fragment is
    # never sent over HTTP and never uses a query-string token.
    print(f"Open this address to authorize this browser session: {url_with_token}")
    if args.open:
        try:
            webbrowser.open(url_with_token)
        except Exception as e:
            print(f"warning: could not open browser: {e}", file=sys.stderr)

    monitor = threading.Thread(
        target=_idle_monitor, args=(server, args.idle_timeout), daemon=True
    )
    monitor.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
