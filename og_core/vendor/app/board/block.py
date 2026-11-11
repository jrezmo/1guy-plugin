"""Block constructor: every block has every contract key, in contract order.

Private keys (leading underscore) carry hints from a source to the assembler
and are stripped before the board is returned:
  _match    {"tags": [...], "text": "..."}  -> project assignment
  _decision bool                            -> attention DECISION boost
  _project  "project:<slug>"                -> explicit project (if it exists)
"""
from . import util

KINDS = ("task", "project", "goal", "session", "crew", "proposal", "job", "media", "note", "artifact", "talk", "widget", "plugin", "event")
STATUSES = ("needs-you", "active", "waiting", "idle", "done", "parked", "proposed", "failed")
SURFACE = {"id": "surface", "label": "open work surface", "surface": True}


def make(kind, ref, title, *, summary="", status="idle", project=None, tags=None,
         priority=None, due=None, updated=None, source_type, url=None, links=None,
         actions=None, detail=None, **private):
    assert kind in KINDS, kind
    assert status in STATUSES, status
    b = {
        "id": f"{kind}:{ref}",
        "kind": kind,
        "title": util.clip(title, 160) or f"{kind} {ref}",
        "summary": util.clip(summary, 200),
        "status": status,
        "attention": 0.0,
        "project": project,
        "tags": list(tags or []),
        "priority": priority,
        "due": due,
        "updated": updated if isinstance(updated, str) or updated is None else util.iso(updated),
        "source": {"type": source_type, "ref": str(ref), "url": url},
        "links": list(links or []),
        "actions": list(actions or []),
        "detail": detail or {},
    }
    for k, v in private.items():
        b["_" + k.lstrip("_")] = v
    return b


# ---------- lens fields (optional, additive; docs/home-contract-deltas-lenses.md §2) ----------
# The same nodes, seen three ways. The Board lens needs nothing new. The Time lens reads
# block["when"], the Inbox lens reads block["inbox"]. Both are derived here, in one place, from
# fields every source already fills; a source that knows better (a calendar feed, a mail feed, a
# pending approval) sets `when` / `inbox` on its block itself and this leaves it alone.
_VERB_LABEL = {"approve": "approve", "steer": "steer", "reject": "reject", "keep": "keep",
               "dismiss": "not now", "done": "done", "run": "run it again", "surface": "talk it through"}


def _verbs(b, ids):
    have = {a.get("id"): a for a in b.get("actions") or []}
    return [{"id": i, "label": _VERB_LABEL.get(i) or have[i].get("label") or i} for i in ids if i in have]


def lens_fields(b):
    """Sets b["when"] = {at, end?, kind: due|scheduled|activity|event, all_day?} and
    b["inbox"] = {why, group: decide|idea|broke, verbs: [{id, label}], since?} where they apply.
    A verb id is one of the block's own action ids, so the client runs it the usual way."""
    d = b.get("detail") or {}
    st, kind = b.get("status"), b.get("kind")
    if "when" not in b:
        when = None
        if kind == "task" and b.get("due") and st != "done":
            event = bool({t.lower() for t in b.get("tags") or []} & {"calendar", "cal", "event", "events", "appointment"})
            when = {"at": b["due"], "kind": "event" if event else "due", "all_day": "T" not in b["due"]}
        elif kind == "job" and d.get("next_run") and "paused" not in (b.get("tags") or []):
            when = {"at": d["next_run"], "kind": "scheduled"}
        elif kind in ("session", "crew", "proposal", "artifact", "talk") and b.get("updated") \
                and st not in ("done", "parked"):
            when = {"at": b["updated"], "kind": "activity"}
        if when:
            b["when"] = when
    if "inbox" not in b and kind not in ("project", "widget", "goal"):
        box = None
        if st == "failed":
            box = {"why": "this broke", "group": "broke", "verbs": _verbs(b, ["run", "surface"])}
        elif kind == "crew" and st == "needs-you":
            box = {"why": "needs your verdict", "group": "decide",
                   "verbs": _verbs(b, ["approve", "steer", "reject"])}
        elif kind == "proposal" and st == "proposed":
            box = {"why": "an idea from me", "group": "idea", "verbs": _verbs(b, ["keep", "dismiss"])}
        elif st == "needs-you":
            box = {"why": "waiting on your answer", "group": "decide",
                   "verbs": _verbs(b, ["surface", "done"])}
        if box:
            if b.get("updated"):
                box["since"] = b["updated"]
            b["inbox"] = box
    return b
