"""Attention: one transparent number per block (0..1) that drives size and
position on the canvas (focus -> related -> dormant).

    attention = clamp( BASE[status]
                     + DECISION        if the block asks the user for a decision
                     + PRIORITY[p]     tasklist !p1..!p3
                     + due bonus       overdue / today / <=3d / <=7d
                     + RECENCY * 0.5 ** (age_h / HALF_LIFE_H)
                     + ACTIVE_PROJECT  if it belongs to an active project
                     + KIND_NUDGE[kind]
                     - DONE_PENALTY    if done or parked )

Projects: attention = max(own score, CHILD_CARRY * loudest child's attention)
unless the project itself is done/parked (``project_score``).
Every weight is a named constant below; ``explain()`` returns the parts so the
board can show why a card is big (GET /api/board?explain=1 adds ``_why``).
"""
import math

BASE = {
    "needs-you": 0.50,
    "failed": 0.50,
    "proposed": 0.35,
    "active": 0.35,
    "waiting": 0.25,
    "idle": 0.15,
    "done": 0.10,
    "parked": 0.10,
}
DECISION = 0.12            # crew issue labelled decision/needs-j, open question
PRIORITY = {1: 0.20, 2: 0.12, 3: 0.05}
DUE_OVERDUE = 0.25
DUE_TODAY = 0.20
DUE_3D = 0.12
DUE_7D = 0.05
RECENCY = 0.15             # max bonus for something touched just now
HALF_LIFE_H = 36.0         # recency bonus halves every 36 hours
ACTIVE_PROJECT = 0.06
DONE_PENALTY = 0.05        # on top of the low BASE for done/parked
KIND_NUDGE = {             # small, so status/priority dominate
    "project": 0.05, "goal": -0.02, "media": -0.03, "job": -0.05,
}
UNKNOWN_AGE_H = 24 * 14    # blocks with no timestamp decay as if two weeks old


def _due_bonus(due_ts, now):
    if due_ts is None:
        return 0.0
    days = (due_ts - now) / 86400.0
    if days < -0.5:            # due date (local midnight) has passed
        return DUE_OVERDUE
    if days < 1:
        return DUE_TODAY
    if days <= 3:
        return DUE_3D
    if days <= 7:
        return DUE_7D
    return 0.0


def explain(f, now):
    """f: dict with status, kind, decision, priority, due_ts, updated_ts,
    project_active. Returns ordered [(name, delta)] parts."""
    status = f.get("status") or "idle"
    parts = [("base:" + status, BASE.get(status, 0.15))]
    if f.get("decision"):
        parts.append(("decision", DECISION))
    p = f.get("priority")
    if p in PRIORITY and status not in ("done", "parked"):
        parts.append((f"priority:p{p}", PRIORITY[p]))
    if status not in ("done", "parked"):
        d = _due_bonus(f.get("due_ts"), now)
        if d:
            parts.append(("due", d))
    upd = f.get("updated_ts")
    age_h = UNKNOWN_AGE_H if upd is None else max(0.0, (now - upd) / 3600.0)
    parts.append(("recency", round(RECENCY * math.pow(0.5, age_h / HALF_LIFE_H), 4)))
    if f.get("project_active"):
        parts.append(("active-project", ACTIVE_PROJECT))
    k = KIND_NUDGE.get(f.get("kind"))
    if k:
        parts.append(("kind:" + f["kind"], k))
    if status in ("done", "parked"):
        parts.append(("done/parked", -DONE_PENALTY))
    return parts


def score(f, now):
    total = sum(v for _, v in explain(f, now))
    return round(min(1.0, max(0.0, total)), 3)


CHILD_CARRY = 0.9   # a project is at least 90% as loud as its loudest child


def project_score(f, now, child_scores):
    own = score(f, now)
    if f.get("status") in ("done", "parked"):
        return own
    carried = CHILD_CARRY * max(child_scores or [0.0])
    return round(min(1.0, max(own, carried)), 3)
