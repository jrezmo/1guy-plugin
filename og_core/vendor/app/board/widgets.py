"""widgets: the housekeeping glances (unified-audit §5.7) as board blocks.

Three always-present furniture blocks, kind ``widget``:
  widget:todo      REAL — the user's tasklist (To-do section) via its JSON API.
                   Glance: open count + the next thing (soonest due, else first).
                   The bloom is the workable surface: tap a row to toggle it
                   (through /api/board/task, which proxies the tasklist API).
  widget:calendar  and
  widget:email     come from the user's own Hermes by default (board/mailcal.py): a scheduled
                   Hermes job reads mail and calendar with its own skills, read-only, and these
                   tiles read what it wrote. Each is honest: real data, an offer to set the job
                   up, or "your hermes can't read email yet". The week's events also come back
                   as `event` blocks (the Time lens places them at their hour) and reply-needed
                   emails as cards.
                   The plugin build has no direct mail or calendar reader: it makes no network
                   calls and holds no credentials.

Glances never fabricate data: a glance without a source says so, plainly.
"""
import json
import os
import urllib.parse
import urllib.request

from . import util
from .block import make
from . import email_glance
from . import mailcal

util.CFG["email_glance"] = False      # plugin build: no direct mail reader

util.CFG["calendar_feed"] = False     # plugin build: no direct calendar reader
util.CFG["calendar_api"] = ""
util.CFG.setdefault("calendar_ids", ["primary"])
CAL_DAYS = 8          # today + the week the Time lens shows
CAL_MAX = 60

TTL = 20.0
MAX_ROWS = 8          # items carried in the to-do bloom
OVERDUE_GRACE_S = 43200   # a due date counts as overdue after its day is half gone
TASKLIST_URL = "http://oneguy.invalid"


def _todo_section(data):
    for s in data.get("sections") or []:
        if (s.get("name") or "").strip().lower() == "to-do":
            return s
    return None


def _open(items):
    return [it for it in items or [] if not it.get("done")]


def _overdue(items, now):
    n = 0
    for it in items:
        d = util.parse_ts(it.get("due"))
        if d is not None and d < now - OVERDUE_GRACE_S:
            n += 1
    return n


def _row(it):
    """One tasklist item as the bloom renders it. Plain words only on faces."""
    return {
        "id": it.get("id"),
        "text": it.get("text") or "",
        "due": util.iso(util.parse_ts(it.get("due"))) if it.get("due") else None,
        "priority": it.get("priority"),
        "done": bool(it.get("done")),
    }


def _todo_block(data):
    sec = _todo_section(data)
    items = _open(sec.get("items") if sec else [])
    now = util.now()
    overdue = _overdue(items, now)
    dated = [it for it in items if util.parse_ts(it.get("due")) is not None]
    dated.sort(key=lambda it: util.parse_ts(it.get("due")) or 0.0)
    nxt = dated[0] if dated else (items[0] if items else None)
    rows = [_row(it) for it in (dated + [it for it in items if it not in dated])[:MAX_ROWS]]
    status = "active" if overdue else "idle"
    summary = (f"{len(items)} open" + (f" · {overdue} overdue" if overdue else "")
               + (f"; next: {nxt.get('text', '')[:80]}" if nxt else ""))
    actions = [{
        "id": "open-list", "label": "open the full list", "href": TASKLIST_URL,
    }]
    return make(
        "widget", "todo", "to-do", summary=summary, status=status,
        tags=["housekeeping"], updated=util.iso(data.get("updated_at")),
        source_type="tasklist", url=TASKLIST_URL,
        actions=actions,
        detail={
            "widget": "todo", "wired": True,
            "count": len(items), "overdue": overdue,
            "next": _row(nxt) if nxt else None,
            "items": rows,
        },
        match={"tags": ["housekeeping"], "text": "to-do list widget"},
    )


def _stub(ref, title, note):
    return make(
        "widget", ref, title, summary=note, status="idle",
        tags=["housekeeping"], source_type="1guy",
        detail={"widget": ref, "wired": False, "count": None, "overdue": 0,
                "next": None, "items": [], "note": note},
     )


def _email_block():
    """The direct email glance (disabled in the plugin build). Read-only; on failure, says so."""
    try:
        d = email_glance.inbox()
    except Exception as e:
        d = {"widget": "email", "wired": True, "unread": 0, "total": None,
             "items": [], "error": str(e)[:160]}
    own = None
    if d.get("error"):
        summary, n = "inbox isn't answering right now", 0
    elif not d.get("wired"):
        summary, n = "not wired", 0
    else:
        # the inbox as next actions: people first, oldest first (mailcal.waiting_order); the count
        # and the opener are people waiting, never the unread total
        now = util.now()
        raw = [{"id": str(it.get("id")), "from": mailcal._sender(it.get("from")), "addr": mailcal._addr(it.get("from")),
                "subject": util.clip(it.get("subject") or "(no subject)", 120),
                "received": it.get("date") or it.get("received"), "snippet": util.clip(it.get("snippet"), 400),
                "bulk": it.get("bulk") is True} for it in d.get("items") or [] if isinstance(it, dict) and it.get("id")]
        rows = mailcal.waiting_order(mailcal.visible(raw, now), now)
        d = dict(d, **mailcal.mail_detail(rows, now))
        summary, n = (mailcal.mail_summary(rows, now) if rows else "inbox clear"), d["count"]
        own = {"title": d["opener"], "why": "", "own": True}
    b = make(
        "widget", "email", "email", summary=summary,
        status="active" if n else "idle",
        tags=["housekeeping"], updated=util.iso(util.now()),
        source_type="gmail-readonly",
        detail=d,
        match={"tags": ["housekeeping"], "text": "email inbox glance"},
    )
    if own:
        b["headline"] = own
    return b


# ---------- the calendar: read-only events -> the glance + `event` blocks ----------
def _cal_get(path, params, tok):
    raise RuntimeError("the plugin build has no direct calendar reader")


def calendar_events(now=None, token=None):
    """-> [Google event dicts] from today 00:00 for CAL_DAYS, every configured calendar. Raises."""
    import datetime as dt
    now = now or util.now()
    d0 = dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    tok = token
    out = []
    for cid in util.CFG["calendar_ids"]:
        data = _cal_get(f"/calendars/{urllib.parse.quote(cid, safe='@.')}/events", {
            "timeMin": d0.isoformat(), "timeMax": (d0 + dt.timedelta(days=CAL_DAYS)).isoformat(),
            "singleEvents": "true", "orderBy": "startTime", "maxResults": CAL_MAX}, tok)
        out += [e for e in data.get("items") or [] if e.get("status") != "cancelled"]
    return out


def _event_block(e, now):
    st, en = e.get("start") or {}, e.get("end") or {}
    all_day = "date" in st and "dateTime" not in st
    at = st.get("dateTime") or st.get("date")
    if not at:
        return None
    t = util.parse_ts(at)
    people = [a.get("displayName") or (a.get("email") or "").split("@")[0]
              for a in e.get("attendees") or [] if not a.get("self") and not a.get("resource")]
    people = [p for p in people if p][:4]
    where = (e.get("location") or "").split(",")[0][:60]
    summary = " · ".join(x for x in [("with " + ", ".join(people)) if people else "", where] if x)
    b = make("event", str(e.get("id")), e.get("summary") or "(busy)", summary=summary,
             status="active" if t is not None and t - now < 7 * 86400 else "idle",
             updated=e.get("updated"), source_type="calendar", url=e.get("htmlLink"),
             detail={"start": at, "end": en.get("dateTime") or en.get("date"), "all_day": all_day,
                     "location": e.get("location"), "people": people, "readonly": True},
             match={"tags": [], "text": e.get("summary") or ""})
    b["when"] = {"at": at, "kind": "event", "all_day": all_day}   # dateTime keeps its offset: an instant
    if not all_day and en.get("dateTime"):
        b["when"]["end"] = en["dateTime"]
    return b


def _calendar(now=None):
    """-> (glance block, [event blocks]). Off: the honest stub, no events."""
    now = now or util.now()
    if not util.CFG.get("calendar_feed"):
        return _stub("calendar", "calendar", "no live calendar feed is wired in yet"), []
    try:
        evs = calendar_events(now)
    except Exception as e:   # noqa: BLE001 - the glance says so, it never guesses
        g = _stub("calendar", "calendar", "the calendar isn't answering right now")
        g["detail"].update(wired=True, error=str(e)[:160])
        return g, []
    blocks = [b for b in (_event_block(e, now) for e in evs) if b]
    import datetime as dt
    today = dt.date.fromtimestamp(now)
    todays = [b for b in blocks if (util.parse_ts(b["detail"]["start"]) or 0) and
              dt.date.fromtimestamp(util.parse_ts(b["detail"]["start"])) == today]
    ahead = [b for b in todays if b["detail"]["all_day"] or (util.parse_ts(b["detail"]["start"]) or 0) >= now]
    nxt = ahead[0] if ahead else None
    g = make("widget", "calendar", "calendar",
             summary=f"{len(todays)} today" + (f" · next {nxt['title'][:60]}" if nxt else ""),
             status="active" if ahead else "idle", tags=["housekeeping"], updated=util.iso(now),
             source_type="calendar-readonly",
             detail={"widget": "calendar", "wired": True, "readonly": True, "count": len(todays), "overdue": 0,
                     "next": {"text": nxt["title"], "due": nxt["detail"]["start"]} if nxt else None,
                     "items": [{"id": b["id"], "text": b["title"], "due": b["detail"]["start"]} for b in todays[:MAX_ROWS]]},
             match={"tags": ["housekeeping"], "text": "calendar glance"})
    return g, blocks


def fetch():
    """-> {blocks: [todo, calendar, email, events…], sources note}. Never raises (CACHE does)."""
    try:
        data = util.http_json(util.CFG["tasklist_api"].rstrip("/") + "/tasks", timeout=4)
        todo = _todo_block(data or {})
    except Exception:
        # tasklist down: the glance says so instead of vanishing or guessing
        todo = _stub("todo", "to-do", "the list isn't answering right now")
        todo["detail"]["wired"] = True
        todo["detail"]["count"] = None
        todo["detail"]["error"] = True
    # email + calendar: the user's own Hermes digest, unless a direct reader was asked for by flag
    direct_cal, direct_mail = bool(util.CFG.get("calendar_feed")), bool(util.CFG.get("email_glance"))
    cal, events = _calendar() if direct_cal else (None, [])
    mail = _email_block() if direct_mail else None
    rest = mailcal.blocks(want=[w for w, direct in (("calendar", direct_cal), ("email", direct_mail)) if not direct])
    tiles = {b["id"]: b for b in rest if b["kind"] == "widget"}
    return {
        "blocks": [todo, cal or tiles["widget:calendar"], mail or tiles["widget:email"], *events,
                   *[b for b in rest if b["kind"] != "widget"]],
        "updated_at": util.iso(util.now()),
    }
