#!/bin/sh
# Little Better Dashboard - one-click launcher (macOS / Linux)
# No dependencies: uses the Python standard library only.
cd "$(dirname "$0")"
exec python3 opencode_dashboard.py --open "$@"
