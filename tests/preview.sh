#!/usr/bin/env bash
# A preview someone can open in a browser: the plugin installed into a scratch Hermes home
# (synthetic data), served by the real `hermes dashboard`, with the real desktop/plugin.js
# running in the harness page on 127.0.0.1:9195.
#
#   tests/preview.sh <state-dir>          start (reinstalls HEAD into <state-dir>)
#   tests/preview.sh <state-dir> stop     stop both servers by PID
#
# The harness stands in for Hermes Desktop: the board, cards, talk bar, gestures and the
# tasklist are real; agent replies come from a canned fake gateway (no model is called).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$(mkdir -p "$1" && cd "$1" && pwd)"
# ports: the page on PREVIEW_PORT (9195), its dashboard on PREVIEW_DASH_PORT (9196).
# 9193-9194 belong to stranger_install.sh. A second preview: PREVIEW_PORT=9197 PREVIEW_DASH_PORT=9198.
DASH_PORT="${PREVIEW_DASH_PORT:-9196}" PORT="${PREVIEW_PORT:-9195}"
stop() { for f in "$STATE"/dash.pid "$STATE"/harness.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; done; }
stop
[ "${2:-}" = stop ] && { echo stopped; exit 0; }

REAL_HERMES="$(command -v hermes)"
rm -rf "$STATE/home" && mkdir -p "$STATE/home"
export HOME="$STATE/home" HERMES_HOME="$STATE/home/.hermes"
# PREVIEW_BUILDER=/path/to/builder.py uses another synthetic-home builder (python3 builder.py <home>)
# PREVIEW_HOME=fresh starts from an empty Hermes (door B)
if [ "${PREVIEW_HOME:-}" = fresh ]; then mkdir -p "$HERMES_HOME"
elif [ -n "${PREVIEW_BUILDER:-}" ]; then python3 "$PREVIEW_BUILDER" "$HERMES_HOME" >/dev/null; else python3 "$ROOT/tests/make_home.py" "$HERMES_HOME" >/dev/null; fi
# a snapshot of the world as built, so /harness/reset (or ?reset on the page) can put it back
rm -rf "$STATE/snapshot" && mkdir -p "$STATE/snapshot"
for f in state.db tasklist.md cron; do [ -e "$HERMES_HOME/$f" ] && cp -r "$HERMES_HOME/$f" "$STATE/snapshot/"; done
"$REAL_HERMES" plugins install "file://$ROOT" --ref "$(git -C "$ROOT" rev-parse HEAD)" --yes-deps --enable >"$STATE/install.log" 2>&1
nohup "$REAL_HERMES" dashboard --port "$DASH_PORT" --host 127.0.0.1 --skip-build --no-open >"$STATE/dashboard.log" 2>&1 &
echo $! >"$STATE/dash.pid"
for _ in $(seq 1 60); do grep -q HERMES_DASHBOARD_READY "$STATE/dashboard.log" && break; sleep 0.5; done
TOKEN=$(curl -s "http://127.0.0.1:$DASH_PORT/" | grep -o 'SESSION_TOKEN__="[^"]*"' | cut -d'"' -f2)
[ -n "$TOKEN" ] || { echo "dashboard did not come up"; tail "$STATE/dashboard.log"; exit 1; }
ONEGUY_UPSTREAM="http://127.0.0.1:$DASH_PORT" ONEGUY_UPSTREAM_TOKEN="$TOKEN" \
  ONEGUY_RESET_HOME="$HERMES_HOME" ONEGUY_RESET_SNAPSHOT="$STATE/snapshot" \
  nohup "$ROOT/.venv/bin/python" "$ROOT/tests/harness/serve.py" "$PORT" >"$STATE/harness.log" 2>&1 &
echo $! >"$STATE/harness.pid"
sleep 2
curl -s -o /dev/null -w "preview http://127.0.0.1:$PORT/ -> %{http_code}\n" "http://127.0.0.1:$PORT/harness/index.html"
curl -s -o "$STATE/board.json" "http://127.0.0.1:$PORT/api/plugins/1guy/board"
python3 -c 'import json,sys; b=json.load(open(sys.argv[1])); print("board:", len(b["blocks"]), "nodes |", b["briefing"]["line"])' "$STATE/board.json"
echo "installed commit: $(git -C "$ROOT" rev-parse --short HEAD)   pids: dash $(cat "$STATE/dash.pid") harness $(cat "$STATE/harness.pid")"
