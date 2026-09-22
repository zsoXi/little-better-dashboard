# Little Better Dashboard

**The local dashboard that shows what your AI coding tools actually did.**

Your tools keep a remarkably detailed record of everything you do with them. OpenCode has a full SQLite database, Codex writes rollout files and a router ledger, Jev and Antigravity leave their own traces on disk. Reading any single one of those raw sources is a chore. Seeing all of them together, on one page, is the entire point of this project.

It is a single Python file with zero dependencies. Download it, double-click the launcher, and your own stats appear in the browser.

![Dashboard screenshot](screenshot.png)

![logo](logo.png)

## What it shows

The OpenCode and Codex tabs share one set of time controls: **All time, Last 24h, Today, Yesterday, 7 days, 30 days, 90 days, 1 year and a custom from-to range**. Choose a period once and the charts, tables, key metrics, model lists and exports all follow it, and the choice is remembered across refreshes. The summary strip and the all-time records keep their fixed view on purpose, so there is always a stable baseline to compare against.

**All tab** is the combined view and the one that opens first: one headline with total tokens, sessions, requests, judgments and agent steps, a per-day table that lines up OpenCode, Codex, Jev and Antigravity tokens next to Codex request counts, Jev judgments and Antigravity steps, and a source block that shows what loaded. The token total adds up OpenCode, Codex, Jev and Antigravity.

**OpenCode tab** covers sessions, turns, tokens and cost, with tokens per day, tokens by model with provider labels, an activity heatmap and streaks, a session browser that includes search and a click-to-open message inspector, plus projects, warning signals, child and related sessions with **session activity** (recent, no recent activity, older or unknown, based on session updates, not execution status; auto-refreshed), agent configuration tiles, and a team graph of parent and child sessions.

**Codex tab** gives the same treatment to your local Codex sessions, with request, token and model stats synthesized from your rollout files alongside a browsable session table.

**Jev tab** reads the local Jev audit logs (JevDesk and JevDeskEasy installs): proposal counts by verdict, input and output tokens, average latency, sessions, and a table of the newest judgments with the audit source for each. Sessions created in mock mode are excluded from the totals and reported separately.

**Antigravity tab** reads the local Antigravity client data (agent conversations): token usage across conversations and per day (cached and uncached input, output including thinking), request counts, agent steps by type, and each conversation's workspace, status and last activity.

There is more under the hood: a split reasoning view, a share view, and one-click JSON or CSV export of exactly what you are looking at.

Everything is read from files already on your machine. The server binds to `127.0.0.1` only and additionally requires a per-instance bearer token plus strict Host/Origin checks; the bind alone is not claimed to prevent all exfiltration.

Privacy: The dashboard may read and display message content from your local OpenCode and Codex sessions for session inspection, and reads local Jev audit logs (session ids, verdicts, token counts) for the Jev tab, and local Antigravity conversation data (titles, workspace paths, step times) for the Antigravity tab. This content stays on your machine and is only served to a browser session presenting the instance token over the local 127.0.0.1 dashboard. Nothing is uploaded or sent to external services. Private content is visible to anyone holding the instance link, and tunnels or public exposure are not supported.

## Data handling and limits

- Unknown outcomes stay unknown: a request whose result could not be determined is never silently counted as success or error, and its tokens are never dropped or folded into another bucket.
- Recency labels (recent, no recent activity, older, unknown) come from session update recency (a session-history signal, not execution status).
- The Codex request list is a bounded **tail** of the newest events with an explicit scanned-vs-total indicator; the synthesized history that feeds the Codex charts keeps the full history (no silent cut-off).
- The Jev tab is a usage view over local hash-chained audit logs, not an audit tool: it does not verify the hash chain, excludes `mock: true` sessions from totals, and reads a bounded tail of each log.
- The Antigravity tab reads the local client databases read-only (conversation summaries plus per-conversation step and generation metadata; newest steps are sampled) and decodes token usage from that local metadata; credit or quota data lives in the cloud and is not shown.
- "Usage in the 24h before each commit" windows are global and **non-additive**: windows may overlap and may include other projects, so commit rows must not be summed.
- The Codex synthesis **cache** index lives in `.cache/codex_index.json` (a derived **checkpoint** tied to the published generation): it is **safe to delete** at any time and is never a source of truth; a failed checkpoint write never fails a publish.
- The server binds to 127.0.0.1 only, requires the per-instance token from the `#token=` fragment, and does not support **tunnels** or public exposure.

## Quick start (one click)

Requirements: **Python 3** (3.10 or newer; tested locally on **Windows with Python 3.14.3**, other platforms are not verified yet and CI is pending) and OpenCode run at least once, so that its database exists.

- **Windows:** double-click `start-dashboard.bat`
- **macOS / Linux:** run `./start-dashboard.sh` (or `bash start-dashboard.sh`)

The dashboard opens in your browser at `http://127.0.0.1:8765/#token=…` (per-instance token in the URL fragment; the fragment is never sent over HTTP and no `?token=` query string is used).

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
| `--router-events / --router-limits` | unset | Use a real Codex Router ledger instead of the synthesized one |
| `--jev-logs FILE` | local JevDesk installs | Jev `audit.jsonl` to read (repeatable) |
| `--antigravity-dir DIR` | `~/.gemini/antigravity` | Antigravity data dir to read |
| `--idle-timeout SECONDS` | off | Shut down after N seconds with no requests |

If the database is missing, you get a plain message asking you to run OpenCode at least once so it can be created.

## Where the data comes from

| Source | Used for |
|---|---|
| OpenCode `opencode.db` (SQLite, read-only) | Everything on the OpenCode tab |
| `~/.codex/sessions/**/*.jsonl` (local rollout files) | Codex tab. On the first run a small `codex_router_events.jsonl` index is built next to the script. It regenerates automatically and is safe to delete |
| `%LOCALAPPDATA%\JevDeskEasy\runtime\audit.jsonl` and `%LOCALAPPDATA%\JevDesk\audit.jsonl` (read-only) | Jev tab. One JSON line per session, proposal or stop event; missing files are reported as not found |
| `%USERPROFILE%\.gemini\antigravity` (read-only) | Antigravity tab. `conversation_summaries.db` plus `conversations/*.db` step and generation metadata; missing files are reported as not found |
| `.opencode/agent/*.md` and `~/.config/opencode/agent/*.md` in the current project | Agent configuration tiles |

It makes no network calls, collects no telemetry and needs no accounts.

## Notes

- The first start can take up to a minute on large databases while SQLite warms up.
- Agent tiles group workers under their leads by filename convention (`tl-1`, `tl-1-w1`, and so on).
- Child-session activity comes from session update recency (recent up to 2 minutes, no recent activity up to 15 minutes, older beyond; unknown when the timestamp is missing or uninterpretable). It is not agent execution status, and a parent_id link is a session relation, not proof of delegation.
- "Usage in the 24h before each commit" covers a 24-hour window that is inclusive on both ends. On the Codex tab that window is global and may include other projects; windows overlap, so commit rows must not be summed.

## Project layout

```
little-better-dashboard/
├── opencode_dashboard.py   # the whole app: backend, API and UI
├── logo.png                # mascot
├── start-dashboard.bat     # one-click start (Windows)
├── start-dashboard.sh      # one-click start (macOS/Linux)
├── LICENSE                 # MIT
└── README.md
```

## License

MIT, see [LICENSE](LICENSE).
