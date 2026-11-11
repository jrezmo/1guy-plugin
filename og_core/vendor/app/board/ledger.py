"""ledger: the judgment ledger (ONEGUY_HOME/judgments.jsonl), one line per decision.

  append(kind, block, target=None, note=None)   -> the record written
  tail(limit)                                   -> the last lines, newest last

Every gesture the user makes on the canvas is a judgment, and so is every edit a proposal makes when he
says yes to it (proposals.verdict). The route (app/routes/judgments.py) and the plugin are thin
shims over this. Stdlib only.
"""
import json
import os
import time

from . import util

# drags: cluster (into a pile) · nudge · park / unpark · reset (tidy) · promote · bury · schedule
#        hold / release (the Time lens: a slot held for a card, or let go)
# verbs: done · keep · not-now · delete · run · verdict (target = approve|steer|reject) · decide ·
#        steer · next (note = go | not now)
# mail:  not-now (later: hidden until tomorrow) · not-mine (hidden for good) · talk (a thread opened on it);
#        block = mail:<message id>. Judgments only: nothing is ever done to the mailbox (board/mailcal.py)
# undo:  he took a change back (target = the kind that was undone)
# select: he drew a box round some cards and asked about them together (block = the first,
#        target = {"ids": [...]}, note = his words): "these go together", before any pile exists
KINDS = {"select", "cluster", "nudge", "park", "unpark", "reset", "promote", "bury", "schedule", "hold", "release",
         "done", "keep", "not-now", "delete", "run", "verdict", "decide", "steer", "next", "undo", "not-mine", "talk"}


def path():
    return util.oneguy("judgments.jsonl")


def append(kind, block=None, target=None, note=None, file=None, by=None):
    """by: who judged, when it wasn't the user (a crew name). The user's own lines carry no `by`: that absence is
    how briefing.moved tells his hands from the crew's."""
    if kind not in KINDS:
        raise ValueError("kind must be one of: " + ", ".join(sorted(KINDS)))
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "block": block or None,
           "target": target or None, "note": note or None}
    if by:
        rec["by"] = str(by)[:40]
    file = file or path()
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def tail(limit=100, file=None):
    try:
        with open(file or path(), encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
