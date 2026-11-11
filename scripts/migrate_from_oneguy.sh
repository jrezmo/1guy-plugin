#!/usr/bin/env bash
# Move an existing install from the old plugin id `oneguy` (v1.0.1 and earlier) to `1guy`.
#
#   scripts/migrate_from_oneguy.sh                 # DRY RUN: prints what it would do, changes nothing
#   scripts/migrate_from_oneguy.sh --apply         # do it
#   scripts/migrate_from_oneguy.sh --desktop-only [--apply]
#                                                  # on the machine that runs Hermes Desktop when the
#                                                  # backend lives elsewhere: only the desktop half
# Options:
#   --source <id>   what to install (default: this checkout, pinned to its HEAD commit).
#                   Anything `hermes plugins install` accepts: 1guy, jrezmo/1guy-plugin, a Git URL.
#   HERMES_HOME     the Hermes home to migrate (default ~/.hermes)
#
# What --apply does, in this order (each step is skipped when there is nothing to do):
#   1. moves  <HERMES_HOME>/plugin-data/oneguy  ->  plugin-data/1guy      (threads, placements, choices)
#   2. installs and enables the new plugin      ->  plugins/1guy          (hermes plugins install)
#   3. disables the old plugin id                                         (hermes plugins disable oneguy)
#   4. archives plugins/oneguy and desktop-plugins/oneguy into
#        <HERMES_HOME>/archive/oneguy-to-1guy-<timestamp>/               (moved, never deleted)
# It does not restart anything and never touches sessions, cron or the tasklist.
# Safe to run twice: a second run finds nothing left to do.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY=0 DESKTOP_ONLY=0 SOURCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --desktop-only) DESKTOP_ONLY=1 ;;
    --source) SOURCE="${2:?--source needs a value}"; shift ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
HH="${HERMES_HOME:-$HOME/.hermes}"
[ -d "$HH" ] || { echo "no Hermes home at $HH" >&2; exit 1; }
HERMES="${HERMES_BIN:-$(command -v hermes || true)}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$HH/archive/oneguy-to-1guy-$STAMP"
OLD=oneguy NEW=1guy
did=0

say() { printf '%s\n' "$*"; }
step() {            # step "<what>" cmd args...   -> prints it; runs it only with --apply
  local what="$1"; shift
  did=1
  if [ "$APPLY" = 1 ]; then say "  doing: $what"; "$@"; else say "  would: $what"; fi
}
archive() {         # archive <path>  -> moves it under $ARCHIVE keeping its parent folder name
  local path="$1" dest="$ARCHIVE/$(basename "$(dirname "$1")")"
  mkdir -p "$dest" && mv "$path" "$dest/"
}
enabled_has() {     # is <id> listed under plugins.enabled in config.yaml?
  [ -f "$HH/config.yaml" ] && awk '/^plugins:/{p=1;next} /^[^ ]/{p=0} p&&/^  enabled:/{e=1;next} p&&/^  [a-z_]+:/{e=0} e&&/^ *- /{print $2}' "$HH/config.yaml" | tr -d "\"'" | grep -qx "$1"
}

say "Hermes home: $HH"
say "mode: $([ "$APPLY" = 1 ] && echo APPLY || echo 'dry run (add --apply to change anything)')$([ "$DESKTOP_ONLY" = 1 ] && echo ', desktop half only')"
say ""

if [ "$DESKTOP_ONLY" = 1 ]; then
  say "desktop half"
  if [ ! -f "$HH/desktop-plugins/$NEW/plugin.js" ] || ! cmp -s "$ROOT/desktop/plugin.js" "$HH/desktop-plugins/$NEW/plugin.js"; then
    install_desktop() {           # write beside the target, then rename: the app never sees half a file
      mkdir -p "$HH/desktop-plugins/$NEW"
      cp "$ROOT/desktop/plugin.js" "$HH/desktop-plugins/$NEW/.plugin.js.new"
      mv "$HH/desktop-plugins/$NEW/.plugin.js.new" "$HH/desktop-plugins/$NEW/plugin.js"
    }
    step "install desktop-plugins/$NEW/plugin.js from this checkout" install_desktop
  fi
  [ -d "$HH/desktop-plugins/$OLD" ] && step "archive desktop-plugins/$OLD -> ${ARCHIVE#$HH/}/desktop-plugins/" archive "$HH/desktop-plugins/$OLD"
  [ "$did" = 0 ] && say "  nothing to do: desktop-plugins/$NEW is current and there is no desktop-plugins/$OLD"
  say ""
  say "then, in Hermes Desktop: ⌘K → Reload desktop plugins, and switch '1guy' on under Capabilities → Plugins."
  say "The sidebar row is still '1guy'. Panes saved in the layout under the old id (oneguy:dock, oneguy:tasks-pane)"
  say "are gone; open them again with ⌘K 'Show 1guy dock' / 'Show 1guy to-dos'."
  exit 0
fi

[ -n "$HERMES" ] || { echo "hermes is not on PATH (set HERMES_BIN)" >&2; exit 1; }
if [ -z "$SOURCE" ]; then
  SOURCE="file://$ROOT"
  REF="$(git -C "$ROOT" rev-parse HEAD)"
else
  REF=""
fi

say "1. data folder"
if [ -d "$HH/plugin-data/$OLD" ] && [ ! -e "$HH/plugin-data/$NEW" ]; then
  step "move plugin-data/$OLD -> plugin-data/$NEW" mv "$HH/plugin-data/$OLD" "$HH/plugin-data/$NEW"
elif [ -d "$HH/plugin-data/$OLD" ]; then
  say "  BOTH plugin-data/$OLD and plugin-data/$NEW exist. Nothing is merged or overwritten."
  say "  The new id is already keeping its own state. To prefer the old state instead, stop Hermes and run:"
  say "    mv '$HH/plugin-data/$NEW' '$HH/plugin-data/$NEW.set-aside-$STAMP' && mv '$HH/plugin-data/$OLD' '$HH/plugin-data/$NEW'"
else
  say "  nothing to do (no plugin-data/$OLD)"
fi

say "2. the new plugin"
if [ -f "$HH/plugins/$NEW/plugin.yaml" ]; then
  say "  plugins/$NEW is already installed ($(sed -n 's/^version: *//p' "$HH/plugins/$NEW/plugin.yaml" | tr -d '"'))"
  enabled_has "$NEW" || step "enable $NEW" "$HERMES" plugins enable "$NEW"
else
  if [ -n "$REF" ]; then
    step "hermes plugins install $SOURCE --ref ${REF:0:10}… --yes-deps --enable" "$HERMES" plugins install "$SOURCE" --ref "$REF" --yes-deps --enable
  else
    step "hermes plugins install $SOURCE --yes-deps --enable" "$HERMES" plugins install "$SOURCE" --yes-deps --enable
  fi
fi

say "3. the old plugin id"
if enabled_has "$OLD"; then
  step "hermes plugins disable $OLD" "$HERMES" plugins disable "$OLD"
else
  say "  nothing to do ($OLD is not in plugins.enabled)"
fi

say "4. old folders"
found=0
for d in "$HH/plugins/$OLD" "$HH/desktop-plugins/$OLD"; do
  [ -e "$d" ] || continue
  found=1
  step "archive ${d#$HH/} -> ${ARCHIVE#$HH/}/$(basename "$(dirname "$d")")/" archive "$d"
done
[ "$found" = 0 ] && say "  nothing to do (no plugins/$OLD, no desktop-plugins/$OLD)"

say ""
if [ "$did" = 0 ]; then
  say "Already migrated: nothing left to do."
elif [ "$APPLY" = 0 ]; then
  say "Dry run only. Re-run with --apply to make these changes."
  exit 0
else
  say "Done. Nothing was deleted; the old folders are in ${ARCHIVE#$HH/}/"
fi
cat <<NOTE

Verify (the script does not restart anything):
  1. restart the gateway / dashboard so the backend is re-imported
  2. the web server log shows the new mount and not the old one:
       grep 'Mounted plugin API routes: /api/plugins/' "$HH/logs/agent.log" | tail -5
       -> expect   /api/plugins/1guy/      and no new line for /api/plugins/oneguy/
  3. the plugin answers and sees its old state (needs the dashboard session, or run it from the app):
       GET /api/plugins/1guy/diag      -> plugin_version, canvas, backend.blocks
       GET /api/plugins/1guy/threads   -> the item -> session map from before the move
  4. on the machine that runs Hermes Desktop, if it is a different machine:
       scripts/migrate_from_oneguy.sh --desktop-only --apply
NOTE
