"""world: ONE plain line of what's moving out in the world on an item, and who's moving it.

docs/card-grammar.md §6 and adaptive-surfaces.md REFINEMENT 6: a face never says a bare
"moving" or "idle". When something real is happening on an item, the face wears one line of
what it is and who's doing it: `asking Priya for Thu or Fri · 2h`, `conflict: pickup overlaps
the standup`, `he's on it · 12m`, `next run 6:30pm`. Nothing real happening = no line.

    apply(blocks, now, live=None)   sets b["world"] = {line, who, kind, since?, session_id?, src}

`kind` is one of: running, quiet, waiting, waiting-on-you, conflict, confirmed, failed, scheduled.
`since` (epoch seconds) is when the motion started; the client renders its age so it stays fresh
between board polls. Signals, strongest first (the first that speaks wins):
  1. the item's own `live` field, set by a source that knows (a scheduling bot, a watcher)
  2. a live session or helper working on it right now (the feed, keyed through threads/crosslinks)
  3. a watcher's open note that points at it (a proposal from the watcher linking the item)
  2b. a slot he's holding while he coordinates it ('held for Priya', from the judgment ledger)
  4. a scheduled job's last failure
  5. a crew item's state (waiting on the user's ok, or on the crew)
  6. a scheduled job's next run
"""
import datetime as _dt
import json
import os
import re

from . import util

# what's moving out there can need the user now: a conflict or something waiting on his answer lifts the
# card (attention 0..1, board/attention.py), so it is never folded away or drawn dim (backlog D #40)
LIFT = {"conflict": 0.18, "waiting-on-you": 0.12}

KINDS = ("running", "quiet", "waiting", "waiting-on-you", "conflict", "confirmed", "failed", "scheduled", "held")
_WHO_ACTOR = {"subagent": "a helper", "surface": "he", "run": "a tasklist run", "cron": "the routine"}


def _clock(ts, now=None):
    t = _dt.datetime.fromtimestamp(ts)
    s = t.strftime("%I:%M%p").lstrip("0").lower().replace(":00", "")
    today = _dt.date.fromtimestamp(now) if now else _dt.date.today()
    if t.date() == today:
        return s
    if t.date() == today + _dt.timedelta(days=1):
        return "tomorrow " + s
    return t.strftime("%a ").lower() + s


def _w(line, who, kind, since=None, session_id=None, src=""):
    w = {"line": util.clip(line, 90), "who": who, "kind": kind if kind in KINDS else "running", "src": src}
    if since:
        w["since"] = round(since)
    if session_id:
        w["session_id"] = session_id
    return w


def from_item(b, now):
    """1. a source-set `live` field: {line, who?, kind?, since?|since_min?, session_id?}."""
    lv = b.get("live")
    if not isinstance(lv, dict) or not str(lv.get("line") or "").strip():
        return None
    since = util.parse_ts(lv.get("since"))
    if since is None and lv.get("since_min") is not None:
        try:
            since = now - float(lv["since_min"]) * 60
        except (TypeError, ValueError):
            since = None
    return _w(str(lv["line"]).strip(), lv.get("who") or "", lv.get("kind") or "running", since,
              lv.get("session_id"), "item")


def from_event(ev, now):
    """2. live work on the item: 'he's on it · 12m' / 'a helper went quiet · 40m'."""
    if not ev or not ev.get("live"):
        return None
    who = _WHO_ACTOR.get(ev.get("actor"), "he")
    if ev.get("status") == "quiet":
        return _w(f"{who} went quiet", who, "quiet", ev.get("last_move") or ev.get("started"),
                  ev.get("session_id"), "feed")
    verb = "'s on it" if who == "he" else " is on it"
    return _w(f"{who}{verb}", who, "running", ev.get("started"), ev.get("session_id"), "feed")


def watcher_notes(blocks):
    """3. open watcher proposals -> {target block id: proposal block}."""
    out = {}
    for p in blocks:
        if p.get("kind") != "proposal" or p.get("status") != "proposed":
            continue
        if (p.get("detail") or {}).get("writer") != "watcher":
            continue
        for l in p.get("links") or []:
            if not l.startswith("project:"):
                out.setdefault(l, p)
    return out


def from_block(b, now):
    """4-6. what the block's own source already knows."""
    d = b.get("detail") or {}
    kind, st = b.get("kind"), b.get("status")
    if kind == "job":
        if st == "failed":
            n = d.get("failure_streak") or 0
            line = "last run broke" + (f" ({n} in a row)" if n > 1 else "")
            return _w(line, "the routine", "failed", util.parse_ts(d.get("last_run")), src="job")
        nxt = util.parse_ts((b.get("when") or {}).get("at")) or util.parse_ts(d.get("next_run"))   # rolled forward if stale
        if nxt and nxt > now and "paused" not in (b.get("tags") or []):
            return _w("next run " + _clock(nxt, now), "the routine", "scheduled", src="job")
        return None
    if kind == "crew":
        who = d.get("author") or "the crew"
        since = util.parse_ts(b.get("updated"))
        if st == "needs-you":
            return _w(f"{who} is waiting on your ok", who, "waiting-on-you", since, src="crew")
        if st == "active":
            return _w(f"{who} is on your note", who, "running", since, src="crew")
        if st == "waiting":
            return _w(f"with {who}", who, "waiting", since, src="crew")
    return None


def holds(now=None, path=None, tail=400):
    """-> {block id: {who, at, since}} for slots he's holding while he coordinates (lane D).
    Read from the judgment ledger: a 'schedule' whose note is 'held for …' starts one (the ledger's own
    kinds, no new one); a later plain 'schedule', 'done' or 'delete' on the same block ends it, and so
    does the slot passing."""
    now = now or util.now()
    path = path or util.oneguy("judgments.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-tail:]
    except OSError:
        return {}
    out = {}
    for ln in lines:
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        bid, kind = j.get("block"), j.get("kind")
        if not bid:
            continue
        note = str(j.get("note") or "").strip()
        if kind == "hold" or (kind == "schedule" and note.startswith("held for")):
            out[bid] = {"who": note, "at": j.get("target"), "since": util.parse_ts(j.get("at"))}
        elif kind in ("schedule", "release", "done", "delete"):
            out.pop(bid, None)
    return {k: v for k, v in out.items() if not v["at"] or (util.parse_ts(v["at"]) or now) > now - 3600}


def from_hold(h):
    if not h:
        return None
    who = h["who"].removeprefix("held for ").strip()
    return _w(f"held for {who}" if who else "held while he asks", "the one guy", "held", h.get("since"), src="hold")


def line_for(b, now, ev=None, note=None, hold=None):
    w = from_item(b, now) or from_hold(hold) or from_event(ev, now)
    if w:
        return w
    if note is not None:
        title = (note.get("headline") or {}).get("title") or note.get("title") or ""
        return _w("the watcher flagged: " + title[:1].lower() + title[1:], "the watcher", "conflict",
                  util.parse_ts(note.get("updated")), src="watcher")
    return from_block(b, now)


# ---------- a routine sits at its NEXT run (bug: a weekly job that ran ok was filed as overdue) ----------
def _cron_field(f, lo, hi):
    """'*', '5', '1,3', '1-5', '*/15', '0-30/10' -> sorted set of ints in [lo, hi], or None if unreadable."""
    out = set()
    for part in str(f).split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/", 1)
            if not st.isdigit() or int(st) < 1:
                return None
            step = int(st)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            if not (x.isdigit() and y.isdigit()):
                return None
            a, b = int(x), int(y)
        elif part.isdigit():
            a = b = int(part)
        else:
            return None
        out.update(range(a, b + 1, step))
    return {v for v in out if lo <= v <= hi} or None


def next_run(expr, after):
    """Next time a 5-field cron expression fires strictly after `after` (epoch, local time), or None.
    Covers what Hermes jobs use: numbers, lists, ranges, steps, '*'. Day-of-month and day-of-week
    combine the cron way (either matches when both are restricted)."""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    mins, hrs = _cron_field(parts[0], 0, 59), _cron_field(parts[1], 0, 23)
    doms, mons = _cron_field(parts[2], 1, 31), _cron_field(parts[3], 1, 12)
    dows = _cron_field(parts[4].replace("7", "0"), 0, 6)
    if None in (mins, hrs, doms, mons, dows):
        return None
    dom_any, dow_any = parts[2] == "*", parts[4] == "*"
    t = _dt.datetime.fromtimestamp(after).replace(second=0, microsecond=0) + _dt.timedelta(minutes=1)
    for _ in range(400):                       # days to look ahead (a yearly job still resolves)
        cron_dow = (t.weekday() + 1) % 7       # cron: 0 = Sunday
        day_ok = (t.day in doms) if dow_any else (cron_dow in dows) if dom_any else (t.day in doms or cron_dow in dows)
        if t.month in mons and day_ok:
            for h in sorted(hrs):
                if h < t.hour:
                    continue
                for m in sorted(mins):
                    if h == t.hour and m < t.minute:
                        continue
                    return t.replace(hour=h, minute=m).timestamp()
        t = (t + _dt.timedelta(days=1)).replace(hour=0, minute=0)
    return None


def roll_routines(blocks, now):
    """A job whose recorded next run is already past (the scheduler hasn't rewritten it yet, or the
    record is stale) is not overdue: it runs again at its schedule's next time. Its when moves there."""
    for b in blocks:
        w = b.get("when") or {}
        if b.get("kind") != "job" or w.get("kind") != "scheduled":
            continue
        at = util.parse_ts(w.get("at"))
        if at is None or at > now:
            continue
        nxt = next_run((b.get("detail") or {}).get("expr"), now)
        if nxt:
            w["at"] = util.iso(nxt)
        else:
            b.pop("when", None)                # no readable schedule: better off the week than 'overdue'


def apply(blocks, now=None, live=None):
    """Sets b["world"] on every block where something real is moving; removes stale ones. Never raises."""
    now = now or util.now()
    live = live or {}
    try:
        roll_routines(blocks, now)
    except Exception:   # noqa: BLE001 - a malformed schedule never sinks the board
        pass
    try:
        notes = watcher_notes(blocks)
    except Exception:   # noqa: BLE001 - a malformed proposal never sinks the board
        notes = {}
    try:
        held = holds(now)
    except Exception:   # noqa: BLE001
        held = {}
    for b in blocks:
        try:
            w = line_for(b, now, live.get(b["id"]), notes.get(b["id"]), held.get(b["id"]))
        except Exception:   # noqa: BLE001
            w = None
        if w:
            b["world"] = w
            lift = LIFT.get(w["kind"], 0.0)
            if lift and b.get("status") not in ("done", "parked"):
                b["attention"] = round(min(1.0, (b.get("attention") or 0.0) + lift), 4)
                w["lift"] = lift
        else:
            b.pop("world", None)
    return blocks


# ---------- he says the conflict out loud (backlog D #31) ----------
_PREFIX = re.compile(r"^(conflict|the watcher flagged|heads up)\s*:\s*", re.I)


def _today_at(b, now):
    """-> epoch of the item's when if it's today (and has a clock time), else None."""
    w = b.get("when") or {}
    at = util.parse_ts(w.get("at")) if w.get("at") and "T" in str(w.get("at")) else None
    if at is None:
        return None
    day = _dt.date.fromtimestamp(now)
    return at if _dt.date.fromtimestamp(at) == day else None


def heads_up(blocks, now=None):
    """-> (sentence, block id) naming today's loudest conflict still ahead, or (None, None).
    'conflict: pickup overlaps the standup' on School pickup (17:30, now 15:00) ->
    'heads up: pickup overlaps the standup.' Only conflicts a source reports on the item itself
    (a calendar watcher, a scheduling bot; not a watcher's note on a proposal), only for a to-do or
    event still ahead today or with no time at all, and never with a clock time baked in: the
    sentence can be read hours later. No LLM."""
    now = now or util.now()
    best = None
    for b in blocks:
        w = b.get("world") or {}
        if w.get("kind") != "conflict" or w.get("src") != "item" or b.get("status") in ("done", "parked"):
            continue
        when = b.get("when") or {}
        if when:
            if when.get("kind") not in ("due", "event"):
                continue
            at = _today_at(b, now)
            if at is None or at < now:
                continue
        else:
            at = None
        rank = (-(b.get("attention") or 0), at or 0)
        if best is None or rank < best[0]:
            best = (rank, b, w)
    if not best:
        return None, None
    _, b, w = best
    what = _PREFIX.sub("", str(w.get("line") or "")).strip().rstrip(".")
    return (f"heads up: {what}.", b["id"]) if what else (None, None)


def speak(board, now=None):
    """Puts his heads-up on the briefing as its own thing (briefing.heads_up + heads_up_id), never
    inside his opener: the opener stays one line, and the card carries the details."""
    br = board.get("briefing")
    if not isinstance(br, dict):
        return board
    try:
        h, bid = heads_up(board.get("blocks") or [], now)
    except Exception:   # noqa: BLE001 - the briefing never breaks over a garnish
        h, bid = None, None
    if h:
        br["heads_up"], br["heads_up_id"] = h, bid
    return board


def live_events():
    """The feed's live events by board key (cached by the feed; {} if it can't answer).
    ONEGUY_WORLD_FEED=off skips it (tests: the feed reads real activity paths)."""
    if os.environ.get("ONEGUY_WORLD_FEED") == "off":
        return {}
    try:
        import feed   # app/feed.py: one activity feed (sessions, helpers, runs, crons)
        return feed.live_by_key(feed.cached()["events"])
    except Exception:   # noqa: BLE001 - motion lines are a garnish, never a dependency
        return {}
