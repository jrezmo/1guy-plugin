"""Where the plugin's state lives, and nothing else.

Reads (never writes):  <HERMES_HOME>/state.db, <HERMES_HOME>/cron/jobs.json + cron/output/
Reads and writes:      <HERMES_HOME>/tasklist.md (the shared tasklist, created if absent)
Owns:                  <HERMES_HOME>/plugin-data/1guy/   threads.json, home-state.json,
                       judgments.jsonl, headlines.json, local.json, client-errors.jsonl, proposals/, brain/, logs/
"""
from __future__ import annotations

import os
from pathlib import Path

PLUGIN_ID = "1guy"
LEGACY_ID = "oneguy"      # the id this plugin shipped under before 1.0.2 (data-folder migration only)


def hermes_home() -> Path:
    """The active Hermes home (profile-aware when the host can tell us)."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def data_dir() -> Path:
    """<HERMES_HOME>/plugin-data/1guy/: survives plugin update and remove."""
    base = hermes_home() / "plugin-data"
    old, new = base / LEGACY_ID, base / PLUGIN_ID
    if old.is_dir() and not new.exists():
        # one-time move: versions up to 1.0.1 were installed under the id "oneguy"
        try:
            os.replace(old, new)
        except OSError:
            pass
    try:
        from plugins.plugin_storage import plugin_data_dir
        d = Path(plugin_data_dir(PLUGIN_ID))
    except Exception:
        d = new
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_db() -> Path:
    return hermes_home() / "state.db"


def cron_dir() -> Path:
    return hermes_home() / "cron"


def tasklist_path() -> Path:
    return Path(os.environ.get("ONEGUY_TASKLIST_FILE") or hermes_home() / "tasklist.md")


def local_prefs(update: dict | None = None) -> dict:
    """The canvas's small remembered choices (sounds on, hint seen): what a web page would keep
    in localStorage, which a sandboxed frame does not have. Strings only, capped."""
    import json
    path = data_dir() / "local.json"
    try:
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
        cur = cur if isinstance(cur, dict) else {}
    except (OSError, ValueError):
        cur = {}
    if update is not None:
        cur = {str(k)[:80]: str(v)[:20000] for k, v in list(update.items())[:50]
               if isinstance(k, str) and isinstance(v, str)}
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f)
        os.replace(tmp, path)
    return cur


DIAG_KEEP = 400          # lines kept in client-errors.jsonl
_HOME_RE = None


def _clean(v, n=300) -> str:
    """One short line with any home-directory path shortened: enough to debug, nothing personal."""
    global _HOME_RE
    import re
    if _HOME_RE is None:
        _HOME_RE = re.compile(r"(/Users|/home|[A-Za-z]:\\\\Users)[/\\\\][^/\\\\\s\"']+")
    return _HOME_RE.sub("~", " ".join(str(v if v is not None else "").split()))[:n]


def diag_append(entries: list, meta: dict | None = None) -> int:
    """Append what the desktop half and the canvas report about themselves (errors, policy
    refusals, failed calls, load timings) to client-errors.jsonl, capped to the newest lines."""
    import json
    import time
    path = data_dir() / "client-errors.jsonl"
    meta = {k: _clean(v, 120) for k, v in (meta or {}).items() if k in ("version", "ua", "canvas")}
    rows = []
    for e in (entries or [])[:40]:
        if not isinstance(e, dict):
            continue
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": _clean(e.get("kind"), 24) or "error",
               "message": _clean(e.get("message"))}
        if e.get("where"):
            row["where"] = _clean(e.get("where"), 160)
        if isinstance(e.get("ms"), (int, float)):
            row["ms"] = round(float(e["ms"]))
        rows.append(dict(row, **meta))
    if not rows:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            old = f.readlines()[-(DIAG_KEEP - len(rows)):]
    except OSError:
        old = []
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(old)
        f.writelines(json.dumps(r) + "\n" for r in rows)
    os.replace(tmp, path)
    return len(rows)


def diag_tail(n: int = 50) -> list:
    import json
    try:
        with open(data_dir() / "client-errors.jsonl", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def vendored_env() -> dict:
    """The ONEGUY_* variables the vendored 1guy modules read at import. Everything points
    inside this Hermes home; nothing points at a network service."""
    home, data = hermes_home(), data_dir()
    return {
        "ONEGUY_HOME": str(data),
        "ONEGUY_STATE_DB": str(state_db()),
        "ONEGUY_CRON_DIR": str(cron_dir()),
        "ONEGUY_HOME_STATE": str(data / "home-state.json"),
        "ONEGUY_THREADS_FILE": str(data / "threads.json"),
        "ONEGUY_DELEG_LIVE": str(home / "cache" / "delegation" / "live"),
        "ONEGUY_TASKLIST_RUNS": str(data / "runs"),
        "ONEGUY_HANDOFFS": str(data / "handoffs"),
        "ONEGUY_ESCALATE": "0",          # the feed never files tasks by itself
        "ONEGUY_TASKLIST_API": "oneguy-local://tasklist",
    }
