# Little Better Dashboard

A single-file, zero-dependency usage dashboard for [OpenCode](https://github.com/sst/opencode) — plus a Codex view. One click and you're looking at your own stats.

![logo](logo.png)

## What it shows

**OpenCode tab** — sessions, turns, tokens and cost; tokens per day; tokens by model; activity heatmap and streaks; session browser with search and a click-to-open message inspector; projects; warning signals; subagent runs with **live status** (running / idle / finished, auto-refreshed); agent configuration tiles; team graph (parent → child sessions).

**Codex tab** — the same treatment for your local Codex sessions: request/token/model stats synthesized from your rollout files, plus a browsable session table.

Everything is read from files already on your machine. Nothing is uploaded anywhere — the server binds to `127.0.0.1` only.

## Quick start (one click)

Requirements: **Python 3** (3.10+ works; tested on 3.14) and OpenCode run at least once (so its database exists).

- **Windows:** double-click `start-dashboard.bat`
- **macOS / Linux:** `./start-dashboard.sh` (or `bash start-dashboard.sh`)

The dashboard opens in your browser at `http://127.0.0.1:8765`.

Prefer the terminal?

```bash
python opencode_dashboard.py --open
```

## Options

```
python opencode_dashboard.py [db] [--port PORT] [--agents-dir DIR] [--open] [--quiet]
```

| Argument | Default | What it does |
|---|---|---|
| `db` | `~/.local/share/opencode/opencode.db` | Path to the OpenCode database |
| `--port` | `8765` | HTTP port to serve on |
| `--agents-dir DIR` | auto-detected | Extra dir with agent `.md` files (repeatable) |
| `--open` | off | Open the dashboard in a browser on start |
| `--quiet` | off | Suppress per-request logging |
| `--router-events / --router-limits` | — | Use a real Codex Router ledger instead of the synthesized one |
| `--idle-timeout SECONDS` | off | Shut down after N seconds with no requests |

If the database is missing you get a plain message: run OpenCode at least once to create it.

## Where the data comes from

| Source | Used for |
|---|---|
| OpenCode `opencode.db` (SQLite, read-only) | Everything on the OpenCode tab |
| `~/.codex/sessions/**/*.jsonl` (local rollout files) | Codex tab. On first run a small `codex_router_events.jsonl` index is built next to the script (regenerated automatically, safe to delete) |
| `.opencode/agent/*.md` + `~/.config/opencode/agent/*.md` in the current project | Agent configuration tiles |

No network calls, no telemetry, no accounts.

## Notes

- First start can take up to a minute on large databases — that's just SQLite warming up.
- Agent tiles group workers under their leads by filename convention (`tl-1`, `tl-1-w1`, …).
- Subagent "running / idle" state is derived from session recency (active < 2 min, idle < 15 min).

## Project layout

```
little-better-dashboard/
├── opencode_dashboard.py   # the whole app: backend + API + UI
├── logo.png                # mascot
├── start-dashboard.bat     # one-click start (Windows)
├── start-dashboard.sh      # one-click start (macOS/Linux)
├── LICENSE                 # MIT
└── README.md
```

## License

MIT — see [LICENSE](LICENSE).
