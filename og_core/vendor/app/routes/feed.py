"""GET /api/feed: the one activity feed (app/feed.py), for the board's pulses and the runs page.

GET /api/feed            -> {now, events, live: {key: event}, working, errors, ...}
GET /api/feed?live=1     -> only the live events (what the board polls every ~5s)

Each call may kick the escalation check (failed / quiet-past-15-min runs -> one tasklist item
each, deduped), at most once a minute and off the request thread.
"""
import feed as F

_KEEP = ("id", "actor", "key", "who", "what", "now", "where", "started", "last_move", "status", "live",
         "session_id", "deep_link", "reason", "elapsed_s", "eta", "ended")


def slim(e):
    return {k: e.get(k) for k in _KEEP}


def r_feed(query, body):
    d = F.cached()
    F.maybe_escalate(d["events"])
    evs = d["events"]
    if query.get("live") in ("1", "true"):
        evs = [e for e in evs if e["live"]]
    live = F.live_by_key(d["events"])
    return {"now": d["now"], "events": [slim(e) for e in evs],
            "live": {k: slim(e) for k, e in live.items()},
            "working": sum(1 for e in d["events"] if e["live"]),
            "errors": d["errors"], "quiet_after_s": d["quiet_after_s"], "stall_after_s": d["stall_after_s"]}


ROUTES = {("GET", "/api/feed"): r_feed}
