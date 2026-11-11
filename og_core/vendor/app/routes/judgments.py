"""judgment ledger: every canvas gesture logged as relational metadata.

  POST /api/judgment   body {kind, block, target?, note?}  -> append one line
  GET  /api/judgments?limit=50 -> the tail, newest last

The canvas no longer stores pixels as meaning (docs/adaptive-surfaces.md,
REFINEMENT 3). A drag is a judgment — 'cluster' (moved into a pile), 'nudge'
(transient personal offset), 'park' (flicked to the rail), 'reset' (tidied the
table), 'schedule' (dropped on the Time lens: target = the ISO slot, or null =
taken off the calendar) — and judgments are the training data for the arrangement engine.
The ledger itself is app/board/ledger.py (vendored into the plugin); this is the thin HTTP face.
"""
from board import ledger as _ledger

LOG = _ledger.path()          # tests point this at a temp file
KINDS = _ledger.KINDS


def post_judgment(query, body):
    body = body or {}
    _ledger.append(body.get("kind"), body.get("block"), body.get("target"), body.get("note"), file=LOG,
                   by=body.get("by"))     # a crew name; the user's own gestures send none
    return {"ok": True}


def get_judgments(query, body):
    q = query or {}
    try:
        limit = max(1, min(500, int(q.get("limit", 100))))
    except ValueError:
        limit = 100
    return {"judgments": _ledger.tail(limit, file=LOG)}


ROUTES = {
    ("POST", "/api/judgment"): post_judgment,
    ("GET", "/api/judgments"): get_judgments,
}
