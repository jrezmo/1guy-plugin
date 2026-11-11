"""tasks: the user's real tasklist via its JSON API (never the markdown file).

Open items -> task blocks. Done items -> status done (low attention), shown
only for two days after the board first saw them done: the tasklist keeps no
timestamps, so the board remembers first-seen / done-seen times per item id in
~/.hermes/1guy/board/seen.json (private, not in git).

Stable keys (unified audit D1): an item that has a thread carries an inline anchor token
(`^k7f2`, written by the tasklist API, exposed as `anchor`). Its block id is then
`task:^k7f2`, which survives edits, moves and hand edits, so its thread (threads.json),
headline cache and seen record stay attached. Unanchored items keep `task:<content hash>`.
"""
import threading

from . import util
from .block import make, SURFACE

TTL = 5.0
DONE_WINDOW_S = 2 * 86400
SEEN_PRUNE_S = 21 * 86400
NEEDS_YOU_TAGS = {"needs-j", "decision", "needs-you", "ask"}
PARKED_TAGS = {"parked", "someday", "later"}
_seen_lock = threading.Lock()


def seen_path():
    return util.oneguy("board", "seen.json")


def item_ref(it):
    """The block ref: the D1 anchor when the item has one, else the volatile content hash."""
    a = it.get("anchor")
    return a if isinstance(a, str) and a.startswith("^") else it["id"]


def _update_seen(items, now):
    """Returns {id: {"first": t, "done": t|None}}; persists changes."""
    with _seen_lock:
        store = util.read_json(seen_path(), None)
        seeded = store is not None
        store = store if isinstance(store, dict) else {}
        changed = not seeded
        live = set()
        for it in items:
            iid = item_ref(it)
            live.add(iid)
            rec = store.get(iid)
            if rec is None:
                # first run: we don't know when existing items arrived
                rec = {"first": now if seeded else None, "done": None, "last": now}
                store[iid] = rec
                changed = True
            if it.get("done") and not rec.get("done"):
                rec["done"] = now
                changed = True
            if not it.get("done") and rec.get("done"):
                rec["done"] = None
                changed = True
            if now - (rec.get("last") or 0) > 3600:
                rec["last"] = now
                changed = True
        for iid in [k for k, r in store.items()
                    if k not in live and now - (r.get("last") or 0) > SEEN_PRUNE_S]:
            del store[iid]
            changed = True
        if changed:
            try:
                util.write_json_atomic(seen_path(), store)
            except OSError:
                pass
        return store


def _due_iso(it):
    if not it.get("due"):
        return None
    return it["due"] + ("T" + it["due_time"] if it.get("due_time") else "")


def item_status(it, section, updated_ts, now):
    tags = set(it.get("tags") or [])
    if it.get("done"):
        return "done"
    if tags & PARKED_TAGS:
        return "parked"
    if "open question" in section.lower() or "issue" in section.lower() or tags & NEEDS_YOU_TAGS:
        return "needs-you"
    if it.get("priority") == 1:
        return "active"
    if it.get("due"):
        d = util.parse_ts(it["due"])
        if d is not None and d - now < 3 * 86400:
            return "active"
    if updated_ts is not None and now - updated_ts < 3 * 86400:
        return "active"
    return "idle"


def to_blocks(data, now, seen=None):
    seen = seen or {}
    out = []
    for sec in data.get("sections") or []:
        name = sec.get("name") or ""
        for it in sec.get("items") or []:
            ref = item_ref(it)
            rec = seen.get(ref, {})
            if it.get("done"):
                done_at = rec.get("done")
                if done_at is not None and now - done_at > DONE_WINDOW_S:
                    continue
            origin = it.get("origin") or {}
            sid = origin.get("session_id")
            updated_ts = rec.get("done") if it.get("done") else None
            updated_ts = updated_ts or util.session_id_ts(sid) or rec.get("first")
            status = item_status(it, name, updated_ts, now)
            text = it.get("text") or ""
            actions = [
                {"id": "done", "label": "reopen" if it.get("done") else "done",
                 "post": "/api/board/task", "body": {"id": it["id"], "op": "toggle"}},
                {"id": "edit", "label": "edit", "post": "/api/board/task",
                 "body": {"id": it["id"], "op": "edit"}, "note": True},
                {"id": "delete", "label": "delete", "post": "/api/board/task",
                 "body": {"id": it["id"], "op": "delete"}, "confirm": "delete this task?"},
                dict(SURFACE),
            ]
            out.append(make(
                "task", ref, text, summary=text, status=status,
                tags=it.get("tags") or [], priority=it.get("priority"), due=_due_iso(it),
                updated=util.iso(updated_ts), source_type="tasklist",
                links=[f"session:{sid}"] if sid else [],
                actions=actions,
                detail={
                    "text": text,
                    "section": name,
                    "tasklist_id": it["id"],
                    "anchor": it.get("anchor"),
                    "origin": {"label": origin.get("label"), "session_id": sid} if origin else None,
                    "handoff_hint": ("carries the origin session's last turns" if sid
                                     else "starts fresh from the item text"),
                },
                match={"tags": it.get("tags") or [], "text": text},
                decision=status == "needs-you",
            ))
            if isinstance(it.get("live"), dict):   # a bot/watcher says what's moving on it (board/world.py)
                out[-1]["live"] = it["live"]
    return out


def fetch():
    data = util.http_json(util.CFG["tasklist_api"].rstrip("/") + "/tasks", timeout=4)
    now = util.now()
    items = [it for s in data.get("sections") or [] for it in s.get("items") or []]
    seen = _update_seen(items, now)
    blocks = to_blocks(data, now, seen)
    return {"blocks": blocks, "count": len(blocks),
            "open": sum(1 for it in items if not it.get("done")),
            "updated_at": util.iso(data.get("updated_at"))}
