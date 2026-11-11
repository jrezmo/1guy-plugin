#!/usr/bin/env bash
# The stranger test: a brand-new Hermes home that has never heard of 1guy.
#
#   tests/stranger_install.sh [out-dir]
#
# 1. builds a synthetic Hermes home (4 sessions, 4 cron jobs, a tasklist; all invented)
# 2. installs this plugin into it with the real CLI, pinned to this checkout's HEAD commit
# 3. starts the real `hermes dashboard` on 127.0.0.1:9193 against that home
# 4. checks the auth gate, the board and the canvas page over HTTP
# 5. drives the real desktop/plugin.js in a browser (stub SDK) against that dashboard and
#    saves screenshots
# Nothing outside the scratch home is read or written. Servers are killed by PID on exit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Which home: `lived` (default; a week of use: 4 chats, 4 jobs, a tasklist) or `fresh` (a
# brand-new Hermes: nothing in it at all). STRANGER_HOME=fresh tests/stranger_install.sh
# STRANGER_BUILDER=/path/to/builder.py uses another synthetic-home builder instead of ours; it is
# called as `python3 builder.py <home> [--fresh]` (the 1guy demo world's lived_in_home.py fits).
KIND="${STRANGER_HOME:-lived}"
BUILDER="${STRANGER_BUILDER:-}"
OUT="${1:-$(mktemp -d -t 1guy-stranger-XXXXXX)}"
SCRATCH="$(mktemp -d -t 1guy-stranger-home-XXXXXX)"
REAL_HOME="$HOME"     # only the browser driver (playwright) runs with it; Hermes never does
export HOME="$SCRATCH" HERMES_HOME="$SCRATCH/.hermes"
HERMES="${HERMES_BIN:-$(command -v hermes)}"
PORT=9193 HARNESS_PORT=9194
mkdir -p "$OUT"
DASH_PID=""
# The scratch home holds a full dependency install (over a gigabyte). It is removed on exit;
# KEEP_HOME=1 keeps it for a post-mortem.
cleanup() {
  if [ -n "$DASH_PID" ]; then kill "$DASH_PID" 2>/dev/null || true; wait "$DASH_PID" 2>/dev/null || true; fi
  # the dashboard's helpers may still be writing for a moment after it dies: try twice, never fail on it
  [ "${KEEP_HOME:-0}" = 1 ] || rm -rf "$SCRATCH" 2>/dev/null || { sleep 2; rm -rf "$SCRATCH" 2>/dev/null || true; }
}
trap cleanup EXIT

say() { printf '\n== %s\n' "$*"; }

say "1. $KIND Hermes home at $HERMES_HOME"
if [ -n "$BUILDER" ]; then
  if [ "$KIND" = fresh ]; then python3 "$BUILDER" "$HERMES_HOME" --fresh >/dev/null; else python3 "$BUILDER" "$HERMES_HOME" >/dev/null; fi
  echo "   built by $(basename "$BUILDER")"
elif [ "$KIND" = fresh ]; then mkdir -p "$HERMES_HOME"; else python3 "$ROOT/tests/make_home.py" "$HERMES_HOME" >/dev/null; fi

# STRANGER_REF=public installs that ref instead of HEAD (the release commit the catalog names)
REF="$(git -C "$ROOT" rev-parse "${STRANGER_REF:-HEAD}")"
say "2. hermes plugins install (pinned to ${REF:0:7})"
if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then echo "   note: uncommitted changes are NOT part of this install"; fi
"$HERMES" plugins install "file://$ROOT" --ref "$REF" --yes-deps --enable 2>&1 | grep -E "Installed|enabled|✗|rror" || true
test -f "$HERMES_HOME/plugins/1guy/plugin.yaml"

say "3. hermes dashboard on 127.0.0.1:$PORT"
"$HERMES" dashboard --port "$PORT" --host 127.0.0.1 --skip-build --no-open >"$OUT/dashboard.log" 2>&1 &
DASH_PID=$!
for _ in $(seq 1 60); do grep -q HERMES_DASHBOARD_READY "$OUT/dashboard.log" && break; sleep 0.5; done
grep -q HERMES_DASHBOARD_READY "$OUT/dashboard.log" || { echo "dashboard never came up"; tail -20 "$OUT/dashboard.log"; exit 1; }
API="http://127.0.0.1:$PORT/api/plugins/1guy"

say "4. over HTTP"
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/board")
echo "   without a credential: $code"; [ "$code" = 401 ]
TOKEN=$(curl -s "http://127.0.0.1:$PORT/" | grep -o 'SESSION_TOKEN__="[^"]*"' | cut -d'"' -f2)
curl -s -H "X-Hermes-Session-Token: $TOKEN" "$API/board?fresh=1" >"$OUT/board.json"
KIND="$KIND" BUILDER="$BUILDER" python3 - "$OUT/board.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1]))
bad = {k: v.get("error") for k, v in b["sources"].items() if not v["ok"]}
kinds = {}
for x in b["blocks"]:
    kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
print("   sources:", ", ".join(f"{k} {v.get('count', 0)}" for k, v in b["sources"].items()))
print("   blocks: ", kinds)
print("   he says:", b["briefing"]["line"])
print("   top:    ", [x["title"] for x in b["blocks"][:3]])
assert not bad, bad
import os
if os.environ["BUILDER"]:
    # someone else's world: we only know its rough shape
    if os.environ["KIND"] == "fresh":
        assert not kinds.get("job") and not kinds.get("task"), kinds
    else:
        assert kinds.get("session", 0) >= 3 and kinds.get("job", 0) >= 2, kinds
    assert b["briefing"]["line"], b["briefing"]
elif os.environ["KIND"] == "fresh":
    # nothing is invented on an empty Hermes: no chats, no jobs, no to-dos; he still has a way in
    assert not kinds.get("session") and not kinds.get("job") and not kinds.get("task"), kinds
    assert b.get("first_run") == "b" and b["briefing"]["line"] == "what are you in the middle of?", b["briefing"]
else:
    assert kinds.get("session") == 4 and kinds.get("job") == 4 and kinds.get("task", 0) >= 7, kinds
PY
size=$(curl -s -H "X-Hermes-Session-Token: $TOKEN" "$API/app/page" | wc -c)
echo "   canvas page: $size bytes"; [ "$size" -gt 200000 ]

say "5. the real desktop half in a browser, against that dashboard"
if [ "$KIND" = fresh ] && [ -z "$BUILDER" ]; then
  # an empty Hermes opens on door B: he asks, typed lines become to-dos in THIS home's
  # tasklist.md, and the sample desk loads and leaves without changing anything
  ONEGUY_UPSTREAM="http://127.0.0.1:$PORT" ONEGUY_UPSTREAM_TOKEN="$TOKEN" ONEGUY_E2E_HOME="$HERMES_HOME" \
    ONEGUY_TEST_PYTHON="${ONEGUY_TEST_PYTHON:-$ROOT/.venv/bin/python}" \
    HOME="$REAL_HOME" XDG_CACHE_HOME="$REAL_HOME/.cache" \
    /usr/bin/python3 "$ROOT/tests/e2e_doors.py" "$HARNESS_PORT" "$OUT" >"$OUT/e2e-doors.json" || true
  python3 - "$OUT/e2e-doors.json" <<'PY'
import json, sys
r = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
c = r["checks"]
print("   door B:", c.get("he_asks"), "| to-dos written:", len(c.get("todos_written") or []), "| sample desk:", (c.get("sample") or {}).get("chip"))
if r["failed"]:
    print("   failed:", r["failed"])
sys.exit(0 if r["ok"] else 1)
PY
elif [ -n "$BUILDER" ]; then
  # another builder's world is not the one the scripted walk knows: check the first sight
  ONEGUY_UPSTREAM="http://127.0.0.1:$PORT" ONEGUY_UPSTREAM_TOKEN="$TOKEN" FIRST_RUN_CARDS=1 \
    ONEGUY_TEST_PYTHON="${ONEGUY_TEST_PYTHON:-$ROOT/.venv/bin/python}" \
    HOME="$REAL_HOME" XDG_CACHE_HOME="$REAL_HOME/.cache" \
    /usr/bin/python3 "$ROOT/tests/first_run_shots.py" "$HARNESS_PORT" "$OUT" | sed 's/^/   he says: /'
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "   first sight had problems"; exit 1; }
else
ONEGUY_UPSTREAM="http://127.0.0.1:$PORT" ONEGUY_UPSTREAM_TOKEN="$TOKEN" \
  ONEGUY_TEST_PYTHON="${ONEGUY_TEST_PYTHON:-$ROOT/.venv/bin/python}" \
  HOME="$REAL_HOME" XDG_CACHE_HOME="$REAL_HOME/.cache" \
  /usr/bin/python3 "$ROOT/tests/e2e_canvas.py" "$HARNESS_PORT" "$OUT" >"$OUT/e2e.json" || true
python3 - "$OUT/e2e.json" <<'PY'
import json, sys
r = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
print("   ok:", r["ok"], "| cards:", r["checks"].get("cards"), "| gateway calls:", r["checks"].get("gateway_calls"))
if r["failed"]:
    print("   failed:", r["failed"])
sys.exit(0 if r["ok"] else 1)
PY
fi

say "written under the scratch home (and nowhere else)"
( cd "$HERMES_HOME" && find plugin-data/1guy tasklist.md -type f | sort | sed 's/^/   /' )
say "PASS. evidence in $OUT"
ls "$OUT" | sed 's/^/   /'
