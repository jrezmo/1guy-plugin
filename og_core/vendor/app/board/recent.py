"""recent: nothing the user touched gets lost.

    build(limit=20)                                   -> the list the canvas's ↺ drawer shows
    recent(threads_file, judgments_file, blocks, …)   -> the pure function behind it (tests)

Lives in app/board/ (not in a route) so the plugin vendors it with the rest of the board: a host
only needs a thin `GET /recent` shim that calls build(). Reads two files under ONEGUY_HOME — the
thread map and the judgment ledger — and the board it is given. No network, no model call. On a
sparse home (no threads, no ledger) it returns an empty list.
"""
import json
import re
import time

import threads as T

from . import util


def recent(threads_file, judgments_file, blocks, limit=20, now=None):
    """Newest-first list of what the user recently touched, built from the two records that survive
    everything: the thread map (every conversation, with its last activity) and the judgment ledger
    (every decision). Joined to the board when the item is still there — but an item that was put
    away, aged out or never was on the board STAYS in the list, with its Hermes session as the way back.
      -> [{key, title, at, last, session_id, deep_link, on_board, status, kind}]"""
    now = now or time.time()
    data = T.load(threads_file)
    by_id = {b["id"]: b for b in blocks or []}
    back = {}
    for a, t in (data.get("aliases") or {}).items():
        back.setdefault(t, []).append(a)
    items = {}

    def entry(key):
        k = T.canonical(key, data)
        return items.setdefault(k, {"key": k, "at": 0.0, "last": None, "session_id": None, "title": None, "kind": None})

    for k, rec in (data.get("threads") or {}).items():
        if k == "home":
            continue
        prim, surf = rec.get("primary") or {}, rec.get("surface") or {}
        ats = [prim.get("at"), surf.get("last_activity")] + [c.get("at") for c in rec.get("children") or []]
        at = max([float(a) for a in ats if isinstance(a, (int, float))] or [0.0])
        e = entry(k)
        e.update(session_id=prim.get("session_id"), title=rec.get("title"), kind=rec.get("kind"))
        if at >= e["at"]:
            e.update(at=at, last="talked")
    try:
        with open(judgments_file, encoding="utf-8") as f:
            lines = f.readlines()[-400:]
    except OSError:
        lines = []
    for ln in lines:
        try:
            j = json.loads(ln)
            at = time.mktime(time.strptime(j["at"], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, KeyError, TypeError):
            continue
        bid = j.get("block")
        if not bid or j.get("kind") in ("nudge", "reset", "select"):
            continue
        e = entry(bid)
        if at >= e["at"]:
            e.update(at=at, last=j.get("kind"))
    out = []
    for e in sorted(items.values(), key=lambda x: -x["at"])[: max(1, min(60, int(limit)))]:
        b = by_id.get(e["key"]) or next((by_id[a] for a in back.get(e["key"], []) if a in by_id), None)
        h = (b or {}).get("headline") or {}
        title = (h.get("title") if isinstance(h, dict) else None) or (b or {}).get("title") or e["title"] or e["key"]
        title = re.sub(r"^(?:1guy\s*·\s*[\w-]+:\s*)+", "", str(title)).strip()
        sid = e["session_id"]
        out.append({"key": (b or {}).get("id") or e["key"], "title": title[:120], "at": T_iso(e["at"]), "last": e["last"],
                    "session_id": sid, "deep_link": f"hermes://open/{sid}" if sid else None,
                    "on_board": b is not None, "status": (b or {}).get("status"),
                    "kind": (b or {}).get("kind") or e["kind"] or e["key"].split(":", 1)[0]})
    return out


def T_iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) if ts else None


def build(threads_file=None, judgments_file=None, limit=20):
    """The route's whole job: default paths under ONEGUY_HOME + the current board."""
    from . import build_board
    try:
        blocks = build_board().get("blocks") or []
    except Exception:
        blocks = []
    return recent(threads_file or util.oneguy("threads.json"), judgments_file or util.oneguy("judgments.jsonl"),
                  blocks, limit)
