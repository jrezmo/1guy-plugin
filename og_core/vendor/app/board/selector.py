"""Phase 3.5 (audit A6): the three = a real selector, not an attention sort.

    select(blocks, now=None, feed=None) -> (picks, whys)
        picks: at most 3 blocks that actually want the user today, chosen from a
        tiered pool of guidance — not a static attention ranking:

            tier 0  needs-you          "waiting on you 2h"
            tier 1  due-today          "due today"
            tier 2  newly-stuck        "stuck 15m" / "broke 1h ago"
                    (board failed blocks, plus feed events when reachable)
            tier 3  fresh arrivals     "new this morning" / "new 3h ago"
            tier 4  fallback actables  "in motion 2d"   (p1/proposed, old pool)

        whys: {block_id: short reason}. No reason, no pick (A6.1).

    Aging (A6.3): a needs-you block older than NEEDS_YOU_MAX_S (5d) since its
    last event, and a proposal older than PROPOSAL_MAX_S (3d), leaves the pool
    unless re-raised — a new event on the item (block.updated) resets age.

    Cycling (A6.2): state in ~/.hermes/1guy/selector-state.json
        {"date": "YYYY-MM-DD", "shown": [{"key","why","at"}],
         "opened": [key], "led_yesterday": [key]}
      - each board build records the picks it showed (once per key per day);
      - a pick the user opened (POST /api/briefing/seen) drops below unopened picks;
      - anything shown today ranks below fresh faces in its tier;
      - led_yesterday (what led the last active day) can't lead today, and
        ranks below unseen peers — the morning rotation.

    Deterministic tie-break inside a tier: newest event first. Pure: all I/O
    (state load/save, feed read) goes through injectable hooks so tests can
    run on fixtures.
"""
import datetime
import os
import time

from . import util

MAX_PICKS = 200   # v1.2: briefing is an unbounded horizontal rail — no 3-card cap (per-project diversity stays)
PER_PROJECT = 2
NEEDS_YOU_MAX_S = 5 * 86400
PROPOSAL_MAX_S = 3 * 86400
FRESH_S = 24 * 3600
ACT_KINDS = {"task", "crew", "proposal"}


# ---------- state ----------

def state_path():
    # ONEGUY_HOME (scratch servers) isolates the selector state too
    home = os.environ.get("ONEGUY_HOME")
    if home:
        return os.path.join(home, "selector-state.json")
    return util.oneguy("selector-state.json")


def _load_state(now):
    """-> state, rolled over to today if needed (day rollover = the rotation)."""
    raw = util.read_json(state_path(), None)
    st = raw if isinstance(raw, dict) else {}
    today = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    if st.get("date") != today:
        shown = [s for s in (st.get("shown") or []) if isinstance(s, dict)]
        st = {
            "date": today,
            "shown": [],
            "opened": [],
            "led_yesterday": [s.get("key") for s in shown if isinstance(s, dict) and s.get("key")],
        }
    st.setdefault("shown", [])
    st.setdefault("opened", [])
    st.setdefault("led_yesterday", [])
    return st


def _save_state(st):
    try:
        util.write_json_atomic(state_path(), st)
    except Exception:   # pragma: no cover - state is an optimization, never break the board
        pass


def record_opened(key, now=None):
    """The user focused/opened a pick: mark it seen so it drops down the pool."""
    now = now or time.time()
    st = _load_state(now)
    if key not in st["opened"]:
        st["opened"].append(key)
        _save_state(st)


# ---------- pool ----------

def _age(b, now):
    """Seconds since the item's last event (updated); re-raise = fresh age."""
    ts = util.parse_ts(b.get("updated"))
    if ts is None:
        return None
    return max(0.0, now - ts)


def _due_today(b, now):
    due = util.parse_ts(b.get("due"))
    if due is not None:
        d = datetime.datetime.fromtimestamp(due)
        t = datetime.datetime.fromtimestamp(now)
        return d.date() <= t.date()
    # @YYYY-MM-DD in the raw title (the tasklist's inline token)
    import re
    m = re.search(r"@(\d{4}-\d{2}-\d{2})\b", b.get("title") or "")
    if m:
        due = util.parse_ts(m.group(1))
        if due is not None and datetime.datetime.fromtimestamp(due).date() <= datetime.datetime.fromtimestamp(now).date():
            return True
    return False


def _first_seen_ts(b, first_seen):
    """When the board first saw this item, from tasks.py's seen.json map.
    No seen record = not fresh (we don't guess)."""
    if not first_seen:
        return None
    return first_seen.get(b.get("id"))


def _human(span_s):
    m = int(span_s // 60)
    if m < 1:
        return "just now"
    if m < 60:
        return f"{m}m"
    h = int(span_s // 3600)
    if h < 24:
        return f"{h}h"
    d = int(span_s // 86400)
    return f"{d}d"


def tier_and_why(b, now, feed_events, first_seen):
    """-> (tier 0..4 | None, why | None). None = not a today thing."""
    if b.get("kind") == "project" or b.get("status") in ("done", "parked"):
        return None, None
    age = _age(b, now)
    status = b.get("status")

    # aging out (A6.3): stale needs-you / proposals leave the pool
    if status == "needs-you" and age is not None and age > NEEDS_YOU_MAX_S:
        status = None    # too old to lead; may still fall through as actable
    if b.get("kind") == "proposal" and status == "proposed" and age is not None and age > PROPOSAL_MAX_S:
        status = None

    if status == "needs-you":
        why = "waiting on you " + _human(age) if age is not None else "waiting on you"
        return 0, why
    if _due_today(b, now):
        return 1, "due today"
    if status == "failed" and age is not None and age <= FRESH_S:
        return 2, "broke " + _human(age) + " ago"
    if b.get("j_initiated") and b.get("kind") in ("session", "talk") and status == "active":
        return 2, "in motion"    # a session the user started, while it's live
    if feed_events:
        ev = feed_events.get(b.get("id"))
        if ev:
            s = max(0.0, now - (ev.get("ts") or now))
            if s <= FRESH_S:
                return 2, ("stuck " + _human(s)) if s >= 60 else "stuck just now"
    fs = _first_seen_ts(b, first_seen)
    if fs is not None and now - fs <= FRESH_S:
        d = datetime.datetime.fromtimestamp(now)
        same_day = datetime.datetime.fromtimestamp(fs).date() == d.date()
        return 3, ("new this morning" if same_day else "new " + _human(now - fs) + " ago")
    # fallback: the old pool (proposals, p1 in motion), still with a why
    if b.get("kind") in ACT_KINDS and (status == "proposed" or (b.get("kind") == "task" and status == "active" and b.get("priority") == 1)):
        if age is not None:
            return 4, "in motion " + _human(age)
        return 4, "in motion"
    return None, None


def _first_seen_map(blocks):
    """{block id: first-seen ts} from tasks.py's seen cache. Read-only, best effort."""
    try:
        from . import tasks as _tasks
        store = util.read_json(_tasks.seen_path(), None)
        if not isinstance(store, dict):
            return {}
        out = {}
        for b in blocks:
            ref = b.get("id")
            rec = store.get(ref)
            if isinstance(rec, dict) and rec.get("first"):
                out[ref] = rec["first"]
        return out
    except Exception:
        return {}


def _feed_map(feed):
    """feed: {block_id: {ts, kind}} events (newly-stuck), or None."""
    if not isinstance(feed, dict):
        return {}
    return {k: v for k, v in feed.items() if isinstance(v, dict)}


def _feed_events():
    """Newly-stuck events from the feed module (phase 3 owns app/feed.py), if it
    exists yet. Contract: feed.stuck_events() -> {block_id: {"ts": epoch, "kind": "stuck"}}.
    Read WITHOUT importing routes; a missing/odd feed just means an empty map."""
    try:
        from .. import feed as _feed   # noqa - phase 3's module, absent until it lands
        fn = getattr(_feed, "stuck_events", None)
        return fn() if callable(fn) else {}
    except Exception:
        return {}


# ---------- selection ----------

def _pinned_live(blocks):
    """The user's own live sessions (talk-bar/board asks), freshest first, at most one.
    These never silently drop off the three while they're live."""
    out = [b for b in blocks if b.get("j_initiated") and b.get("status") == "active"
           and b.get("kind") in ("session", "talk")]
    out.sort(key=lambda b: -(util.parse_ts(b.get("updated")) or 0))
    return out[:1]


def pick(blocks, now=None, feed=None, first_seen=None, state=None, persist=True):
    """-> (picks, whys). Marks state.shown (each key once per day)."""
    now = now or time.time()
    st = state if state is not None else _load_state(now)
    shown_today = {s.get("key") for s in st["shown"] if isinstance(s, dict)}
    opened = set(st["opened"])
    led_yesterday = set(st["led_yesterday"])
    fmap = _feed_map(feed) if feed is not None else _feed_events()
    fsm = first_seen if first_seen is not None else _first_seen_map(blocks)

    # a live session the user started always keeps one of the three slots (see _pinned_live)
    pinned = _pinned_live(blocks)
    pinned_ids = {b["id"] for b in pinned}

    cands = []
    for i, b in enumerate(blocks):
        if b["id"] in pinned_ids:
            continue
        t, why = tier_and_why(b, now, fmap, fsm)
        if t is None:
            continue
        cands.append((t, b.get("id") in shown_today, b.get("id") in opened,
                      b.get("id") in led_yesterday, (_age(b, now) or 0), i, b, why))
    cands.sort(key=lambda c: c[:6])

    picks, whys, per = [], {}, {}
    for *_, b, why in cands:
        p = b.get("project") or b["id"]     # no project = its own group
        if per.get(p, 0) >= PER_PROJECT:
            continue
        picks.append(b)
        whys[b["id"]] = why
        per[p] = per.get(p, 0) + 1
        if len(picks) >= MAX_PICKS:
            break

    # make room for the pinned live session: it replaces the least urgent pick
    for pb in pinned:
        if pb["id"] in whys:
            continue
        whys[pb["id"]] = "in motion"
        if len(picks) < MAX_PICKS:
            picks.append(pb)
        else:
            tiers = {c[6]["id"]: c[0] for c in cands}
            worst = max(range(len(picks)), key=lambda i: tiers.get(picks[i]["id"], 0))
            gone = picks[worst]
            picks[worst] = pb
            whys.pop(gone["id"], None)

    # the morning rotation: yesterday's leader can't lead today
    if len(picks) > 1 and picks[0]["id"] in led_yesterday:
        swap = next((j for j, b in enumerate(picks[1:], 1) if b["id"] not in led_yesterday), None)
        if swap:
            picks[0], picks[swap] = picks[swap], picks[0]

    if persist:
        changed = False
        at = util.iso(now)
        for b in picks:
            if b["id"] not in shown_today:
                st["shown"].append({"key": b["id"], "why": whys[b["id"]], "at": at})
                changed = True
        if changed:
            _save_state(st)
    return picks, whys


select = pick   # phase-3.5 alias


def apply(board, now=None, feed=None, persist=True):
    """The three, onto the board. Returns the whys ({} on any failure)."""
    try:
        picks, whys = select(board.get("blocks") or [], now=now, feed=feed, persist=persist)
    except Exception:   # pragma: no cover - never break the board
        return {}
    br = board.get("briefing")
    if not isinstance(br, dict) or not picks:
        return whys if isinstance(whys, dict) else {}
    ids = [b["id"] for b in picks]
    old = [i for i in br.get("for_you") or [] if i in ids]
    br["for_you"] = old + [i for i in ids if i not in old]
    br["whys"] = whys
    keep = set(ids) | set(br.get("broke") or [])
    for b in board.get("blocks") or []:
        b["for_you"] = b["id"] in keep
        b.pop("fy_why", None)
        if b["id"] in whys:
            b["fy_why"] = whys[b["id"]]
    return whys
