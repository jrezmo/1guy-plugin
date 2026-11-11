#!/usr/bin/env bash
# Proof for scripts/migrate_from_oneguy.sh on a scratch Hermes home that starts as a real
# v1.0.1 install under the old id `oneguy`:
#   install v1.0.1 (old id) -> run it, leave state behind -> dry run changes nothing ->
#   --apply -> the new id serves the same state, the old route is gone, old folders are archived ->
#   a second --apply has nothing to do -> --desktop-only on a separate "app machine" home.
# Nothing outside the scratch homes is touched. Servers are killed by PID.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLD_COMMIT="${OLD_COMMIT:-68b17f9}"            # "version 1.0.1", plugin id oneguy
PORT=9193
SCRATCH="$(mktemp -d -t 1guy-migrate-XXXXXX)"
export HOME="$SCRATCH" HERMES_HOME="$SCRATCH/.hermes"
HERMES="${HERMES_BIN:-$(command -v hermes)}"
MIG="$ROOT/scripts/migrate_from_oneguy.sh"
DASH_PID=""
APP_PARENT=""
# both scratch homes hold dependency installs (over a gigabyte); removed on exit unless KEEP_HOME=1
trap '[ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null || true; [ "${KEEP_HOME:-0}" = 1 ] || rm -rf "$SCRATCH" "$APP_PARENT" 2>/dev/null || { sleep 2; rm -rf "$SCRATCH" "$APP_PARENT" 2>/dev/null || true; }' EXIT
say() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok: %s\n' "$*"; }
start_dash() {
  : >"$SCRATCH/dash.log"
  "$HERMES" dashboard --port "$PORT" --host 127.0.0.1 --skip-build --no-open >"$SCRATCH/dash.log" 2>&1 &
  DASH_PID=$!
  for _ in $(seq 1 80); do grep -q HERMES_DASHBOARD_READY "$SCRATCH/dash.log" && break; sleep 0.5; done
  grep -q HERMES_DASHBOARD_READY "$SCRATCH/dash.log" || { tail -20 "$SCRATCH/dash.log"; exit 1; }
  TOKEN=$(curl -s "http://127.0.0.1:$PORT/" | grep -o 'SESSION_TOKEN__="[^"]*"' | cut -d'"' -f2)
}
stop_dash() { kill "$DASH_PID" 2>/dev/null || true; wait "$DASH_PID" 2>/dev/null || true; DASH_PID=""; sleep 1; }
api() { curl -s -o "$SCRATCH/out.json" -w '%{http_code}' -H "X-Hermes-Session-Token: $TOKEN" -H 'Content-Type: application/json' "$@"; }

say "1. a real v1.0.1 install under the old id (commit $OLD_COMMIT)"
python3 "$ROOT/tests/make_home.py" "$HERMES_HOME" >/dev/null
"$HERMES" plugins install "file://$ROOT" --ref "$(git -C "$ROOT" rev-parse "$OLD_COMMIT")" --yes-deps --enable >"$SCRATCH/install-old.log" 2>&1
test -f "$HERMES_HOME/plugins/oneguy/plugin.yaml" && ok "plugins/oneguy installed ($(sed -n 's/^version: *//p' "$HERMES_HOME/plugins/oneguy/plugin.yaml"))"
mkdir -p "$HERMES_HOME/desktop-plugins/oneguy" && cp "$HERMES_HOME/plugins/oneguy/desktop/plugin.js" "$HERMES_HOME/desktop-plugins/oneguy/"

say "2. use it, so there is state to carry over"
start_dash
[ "$(api "http://127.0.0.1:$PORT/api/plugins/oneguy/board")" = 200 ] && ok "old route /api/plugins/oneguy/board answers"
[ "$(api -X POST -d '{"kind":"park","block":"task:migration-marker"}' "http://127.0.0.1:$PORT/api/plugins/oneguy/judgment")" = 200 ]
[ "$(api -X POST -d '{"data":{"og.sounds.on":"1"}}' "http://127.0.0.1:$PORT/api/plugins/oneguy/local")" = 200 ]
stop_dash
grep -q migration-marker "$HERMES_HOME/plugin-data/oneguy/judgments.jsonl" && ok "state written under plugin-data/oneguy"

say "3. dry run changes nothing"
before="$(cd "$HERMES_HOME" && find plugins plugin-data desktop-plugins -maxdepth 1 | sort; cat config.yaml)"
"$MIG" | sed 's/^/   | /'
after="$(cd "$HERMES_HOME" && find plugins plugin-data desktop-plugins -maxdepth 1 | sort; cat config.yaml)"
[ "$before" = "$after" ] && ok "nothing changed on disk"

say "4. --apply"
"$MIG" --apply 2>&1 | grep -vE '^\s*(→|✓ Res|✓ Inst|╭|│|╰)' | sed 's/^/   | /'
test -f "$HERMES_HOME/plugins/1guy/plugin.yaml" && ok "plugins/1guy installed ($(sed -n 's/^version: *//p' "$HERMES_HOME/plugins/1guy/plugin.yaml"))"
test ! -e "$HERMES_HOME/plugins/oneguy" && test ! -e "$HERMES_HOME/desktop-plugins/oneguy" && ok "old folders are out of the way"
ARCH="$(ls -d "$HERMES_HOME"/archive/oneguy-to-1guy-*)"
test -f "$ARCH/plugins/oneguy/plugin.yaml" && test -f "$ARCH/desktop-plugins/oneguy/plugin.js" && ok "…and archived, not deleted (${ARCH#$HERMES_HOME/})"
test ! -e "$HERMES_HOME/plugin-data/oneguy" && grep -q migration-marker "$HERMES_HOME/plugin-data/1guy/judgments.jsonl" && ok "state moved to plugin-data/1guy"
sed -n '/^plugins:/,/^[^ ]/p' "$HERMES_HOME/config.yaml" | sed 's/^/   | /'

say "5. the new id serves the old state; the old route is gone"
start_dash
[ "$(api "http://127.0.0.1:$PORT/api/plugins/1guy/diag")" = 200 ] && ok "GET /api/plugins/1guy/diag -> $(python3 -c "import json;d=json.load(open('$SCRATCH/out.json'));print('version',d['plugin_version'],'| blocks',d['backend']['blocks'])")"
[ "$(api "http://127.0.0.1:$PORT/api/plugins/1guy/judgments")" = 200 ] && grep -q migration-marker "$SCRATCH/out.json" && ok "the judgment made under the old id is there"
[ "$(api "http://127.0.0.1:$PORT/api/plugins/1guy/app/page")" = 200 ] && grep -q '"og.sounds.on": *"1"' "$SCRATCH/out.json" && ok "the remembered 'sounds on' choice is there"
code="$(api "http://127.0.0.1:$PORT/api/plugins/oneguy/board")"; [ "$code" != 200 ] && ok "old route /api/plugins/oneguy/board -> $code"
stop_dash
grep 'Mounted plugin API routes: /api/plugins/' "$HERMES_HOME/logs/agent.log" | grep -E 'oneguy|1guy' | tail -2 | sed 's/^.*INFO/   | INFO/'
grep 'Mounted plugin API routes' "$HERMES_HOME/logs/agent.log" | tail -1 | grep -q '/api/plugins/' && \
  [ "$(grep -c 'Mounted plugin API routes: /api/plugins/1guy/' "$HERMES_HOME/logs/agent.log")" -ge 1 ] && ok "agent.log has the 1guy mount line"

say "6. running it again finds nothing to do"
"$MIG" --apply | tail -12 | grep -q "Already migrated" && ok "second --apply: already migrated"

say "7. --desktop-only on a separate app-machine home"
APP_PARENT="$(mktemp -d -t 1guy-migrate-app-XXXXXX)"
APP="$APP_PARENT/.hermes"
mkdir -p "$APP/desktop-plugins/oneguy" && echo "// old" >"$APP/desktop-plugins/oneguy/plugin.js"
HERMES_HOME="$APP" "$MIG" --desktop-only >/dev/null
test -f "$APP/desktop-plugins/oneguy/plugin.js" && test ! -e "$APP/desktop-plugins/1guy" && ok "dry run changed nothing"
HERMES_HOME="$APP" "$MIG" --desktop-only --apply | sed -n '1,6p' | sed 's/^/   | /'
cmp -s "$ROOT/desktop/plugin.js" "$APP/desktop-plugins/1guy/plugin.js" && test ! -e "$APP/desktop-plugins/oneguy" && ls "$APP"/archive/*/desktop-plugins/oneguy/plugin.js >/dev/null && ok "desktop-plugins/1guy is this checkout's plugin.js; the old one is archived"

say "PASS"
