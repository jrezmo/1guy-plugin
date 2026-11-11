"""sessions: recent Hermes sessions with real work, from ~/.hermes/state.db
opened READ-ONLY (sqlite URI mode=ro).

Also returns `history`: every real chat of the last HISTORY_S (two weeks) as a light row
{id, title, first, last, last_role, finish, need?}; door A (untangle.py) counts and piles those, and a
chat that needs the user, or that a pile names, becomes a block even past the cap.

Rules: last 7 days, not hidden/archived, message_count >= 4, never cron or
oneshot; api_server sessions only when they belong to a 1guy surface (listed in
~/.hermes/1guy/surfaces.json or an app/state/ask-sessions.json). At most
MAX_SESSIONS by recency + size, with at most MAX_CHILDREN subagent sessions.
"""
import glob
import math
import os
import sqlite3

import threads as T

from . import outcomes, util
from .block import make, SURFACE

TTL = 20.0
WINDOW_S = 7 * 86400
ACTIVE_S = 30 * 60
MIN_MESSAGES = 4
MAX_SESSIONS = 12
MAX_CHILDREN = 4
EXCERPT = 400
SKIP_SOURCES = {"cron", "oneshot"}
GATED_SOURCES = {"api_server"}   # only when they belong to a 1guy surface
HISTORY_S = 14 * 86400           # door A: "N chats in two weeks"
HISTORY_MAX = 400
HISTORY_MIN_MESSAGES = 2
NOT_CHATS = SKIP_SOURCES | GATED_SOURCES | {"subagent"}


def _walk_sessions(obj, out):
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, list):
        for v in obj:
            _walk_sessions(v, out)
    elif isinstance(obj, dict):
        for k in ("session_id", "session", "sessions", "id", "ids", "hermes_session_id"):
            if k in obj:
                _walk_sessions(obj[k], out)


def surface_map():
    """session id -> set(block ids) for sessions that belong to a 1guy thread.
    Source of truth: ~/.hermes/1guy/threads.json (app/threads.py; primary + child sessions,
    plus the aliases that point at them). Until the migration has written threads.json, the
    retired surfaces.json and the legacy app/state/ask-sessions.json are still read."""
    found = {}

    def add(bid, val):
        sids = set()
        _walk_sessions(val, sids)
        if ":" not in bid and bid != "home":
            bid = "media:" + bid   # the old workpane keyed asks by media block id
        for s in sids:
            found.setdefault(s, set()).add(bid)

    tpath = util.oneguy("threads.json")
    tdata = util.read_json(tpath, None)
    if isinstance(tdata, dict) and isinstance(tdata.get("threads"), dict):
        for key, rec in tdata["threads"].items():
            # 'home' is the talk bar's own thread, the surface itself, not a work item;
            # 'talk:*' threads get their own cards from the talk source — skip here too
            if not isinstance(rec, dict) or key == "home" or key.startswith("talk:"):
                continue
            sids = [(rec.get("primary") or {}).get("session_id")]
            sids += [c.get("session_id") for c in rec.get("children") or [] if isinstance(c, dict)]
            for s in filter(None, sids):
                found.setdefault(s, set()).add(key)
        return found
    surf = util.read_json(util.oneguy("surfaces.json"), {}) or {}
    if isinstance(surf, dict):
        inner = surf.get("surfaces")
        for bid, val in (inner if isinstance(inner, dict) else surf).items():
            # 'home' is the talk bar's own thread, the surface itself, not a work item
            if isinstance(bid, str) and bid != "home":
                add(bid, val)
    pats = [os.path.join(util.CFG["projects_glob_root"], "1guy*", "app", "state", "ask-sessions.json")]
    for path in sorted(set(p for pat in pats for p in glob.glob(pat))):
        data = util.read_json(path, {})
        if isinstance(data, dict):
            for bid, val in data.items():
                add(str(bid), val)
    return found


def connect():
    db = util.CFG["state_db"]
    if not os.path.exists(db):
        raise FileNotFoundError(db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    return con


def _cols(con):
    return {r[1] for r in con.execute("PRAGMA table_info(sessions)")}


def select(rows, now, surfaces):
    """rows: dicts from the sessions table. Returns the chosen ones, ranked."""
    picked = []
    for r in rows:
        if r.get("hidden") or r.get("archived"):
            continue
        if (r.get("message_count") or 0) < MIN_MESSAGES:
            continue
        src = r.get("source") or ""
        if src in SKIP_SOURCES:
            continue
        if src in GATED_SOURCES and r["id"] not in surfaces:
            continue
        last = r.get("last_activity_at") or r.get("ended_at") or r.get("started_at") or 0
        if now - last > WINDOW_S:
            continue
        age_h = max(0.0, (now - last) / 3600)
        rank = math.pow(0.5, age_h / 24) + 0.15 * math.log10(1 + (r.get("message_count") or 0))
        picked.append((rank, last, r))
    picked.sort(key=lambda x: (-x[0], -x[1]))
    out, children = [], 0
    for rank, last, r in picked:
        if r.get("parent_session_id"):
            if children >= MAX_CHILDREN:
                continue
            children += 1
        out.append(dict(r, _last=last))
        if len(out) >= MAX_SESSIONS:
            break
    return out


def last_turns(con, sid):
    lu = la = None
    lu_ts = la_ts = None
    cur = con.execute(
        "SELECT role, substr(content, 1, 4000) AS c, timestamp FROM messages "
        "WHERE session_id = ? AND role IN ('user','assistant') AND content IS NOT NULL "
        "AND content != '' ORDER BY id DESC LIMIT 40", (sid,))
    for row in cur:
        if row["role"] == "user" and lu is None:
            lu, lu_ts = row["c"], row["timestamp"]
        elif row["role"] == "assistant" and la is None:
            la, la_ts = row["c"], row["timestamp"]
        if lu is not None and la is not None:
            break
    return lu, la, max([t for t in (lu_ts, la_ts) if t] or [0]) or None


def first_user(con, sid):
    row = con.execute("SELECT substr(content,1,400) AS c FROM messages WHERE session_id=? "
                      "AND role='user' AND content IS NOT NULL AND content != '' "
                      "ORDER BY id LIMIT 1", (sid,)).fetchone()
    return row["c"] if row else None


def history(con, rows, now):
    """The real chats among `rows` (two weeks, newest first), each with how its last turn ended.
    Two queries for the lot. `need` is untangle.chat_need's reading of that last turn."""
    from . import untangle
    chats = [r for r in rows if not (r.get("hidden") or r.get("archived") or r.get("parent_session_id"))
             and (r.get("source") or "") not in NOT_CHATS][:HISTORY_MAX]
    if not chats:
        return []
    mcols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    fin = "m.finish_reason" if "finish_reason" in mcols else "NULL"
    live = " AND active = 1" if "active" in mcols else ""
    marks = ",".join("?" * len(chats))
    ids = [r["id"] for r in chats]
    last = {row["session_id"]: row for row in con.execute(
        f"SELECT m.session_id, m.role, {fin} AS finish, substr(m.content, -600) AS tail, m.timestamp "
        f"FROM messages m JOIN (SELECT max(id) AS mid FROM messages WHERE session_id IN ({marks}) "
        f"AND role IN ('user','assistant','tool'){live} GROUP BY session_id) x ON x.mid = m.id", ids)}
    first = {row["session_id"]: row["c"] for row in con.execute(
        f"SELECT m.session_id, substr(m.content, 1, 300) AS c FROM messages m JOIN (SELECT min(id) AS mid "
        f"FROM messages WHERE session_id IN ({marks}) AND role = 'user' AND content IS NOT NULL "
        f"AND content != '' GROUP BY session_id) x ON x.mid = m.id", ids)}
    out = []
    for r in chats:
        lm = last.get(r["id"])
        act = r.get("last_activity_at") or r.get("ended_at") or r.get("started_at") or 0
        row = {"id": r["id"], "title": util.redact(r.get("title") or "") or util.safe_excerpt(first.get(r["id"]), 80),
               "first": util.safe_excerpt(first.get(r["id"]), 160), "source": r.get("source"),
               "last": max(act, (lm["timestamp"] if lm else 0) or 0),
               "last_role": lm["role"] if lm else None, "finish": lm["finish"] if lm else None,
               "end_reason": r.get("end_reason"), "tail": lm["tail"] if lm else None}
        need = untangle.chat_need(row, now)
        row.pop("tail")                       # the text stays here; only the reading travels
        if need:
            row["need"] = need
        out.append(row)
    return out


def to_block(r, lu, la, now, surfaces, first=None):
    sid = r["id"]
    last = r.get("_last") or r.get("started_at")
    active = now - last < ACTIVE_S
    title = r.get("title") or util.safe_excerpt(first, 80) or f"session {sid}"
    title = util.redact(title)
    mc = r.get("message_count") or 0
    last_user = util.safe_excerpt(lu, EXCERPT)
    last_asst = util.safe_excerpt(la, EXCERPT)
    lead = ("live" if active else "idle") + f" · {mc} messages"
    if last_user:
        lead += " · last: " + util.clip(last_user, 120)
    links = sorted(surfaces.get(sid, ()))
    if r.get("parent_session_id"):
        links.append(f"session:{r['parent_session_id']}")
    return make(
        "session", sid, title, summary=lead, status="active" if active else "idle",
        updated=util.iso(last), source_type="hermes-session", links=links,
        actions=[dict(SURFACE)],
        detail={
            "session_id": sid,
            "source": r.get("source"),
            "model": r.get("model"),
            "message_count": mc,
            "started": util.iso(r.get("started_at")),
            "last_user": last_user,
            "last_assistant": last_asst,
            "parent_session_id": r.get("parent_session_id"),
            "open_in_desktop_hint": f"Hermes Desktop → Sessions → search \u201c{util.clip(title, 40)}\u201d"
                                    f" (or `hermes --resume {sid}`)",
        },
        match={"tags": [], "text": " ".join(filter(None, [r.get("title"), r.get("cwd"),
                                                        r.get("git_repo_root")]))},
    )


def fetch():
    now = util.now()
    surfaces = surface_map()
    con = connect()
    try:
        cols = _cols(con)
        want = [c for c in ("id", "title", "source", "model", "message_count", "started_at",
                            "ended_at", "last_activity_at", "hidden", "archived",
                            "parent_session_id", "cwd", "git_repo_root", "end_reason") if c in cols]
        act = "COALESCE(last_activity_at, ended_at, started_at)" if "last_activity_at" in cols \
            else "COALESCE(ended_at, started_at)"
        rows = [dict(r) for r in con.execute(
            f"SELECT {', '.join(want)} FROM sessions WHERE {act} > ? AND message_count >= ? "
            f"ORDER BY {act} DESC LIMIT 800", (now - HISTORY_S, HISTORY_MIN_MESSAGES))]
        all_rows = rows
        hist = history(con, rows, now)
        rows = [r for r in rows if (r.get("message_count") or 0) >= MIN_MESSAGES
                and (r.get("last_activity_at") or r.get("ended_at") or r.get("started_at") or 0) > now - WINDOW_S]
        chosen = select(rows, now, surfaces)
        # door A: a chat that needs the user, or that a pile names, is a card even past the cap
        from . import untangle
        need = {h["id"]: h["need"] for h in hist if h.get("need")}
        have, byid = {r["id"] for r in chosen}, {h["id"]: h for h in hist}
        full = {r["id"]: r for r in all_rows}
        for sid in untangle.pinned_sessions(hist, now):
            if sid not in have and sid in full and sid in byid:
                chosen.append(dict(full[sid], _last=byid[sid]["last"]))
                have.add(sid)
        jids = outcomes.j_initiated()
        blocks = []
        for r in chosen:
            lu, la, msg_ts = last_turns(con, r["id"])
            if msg_ts and msg_ts > (r.get("_last") or 0):
                r["_last"] = msg_ts
            first = None if r.get("title") else first_user(con, r["id"])
            b = to_block(r, lu, la, now, surfaces, first)
            if r["id"] in jids:
                b["j_initiated"] = True     # the user started this one (talk bar or his tasklist)
            if r["id"] in need:
                b["detail"]["needs"] = need[r["id"]]
            blocks.append(b)
    finally:
        con.close()
    # wrap the quiet ones (cache reads; generation kicks in the background), age out the old
    try:
        pending, _dropped = outcomes.apply(blocks, now)
        blocks = [b for b in blocks if not b.pop("_age_out", False)]
        outcomes.kick(pending)
    except Exception:   # noqa: BLE001 - a wrap failure must never break the source
        pass
    return {"blocks": blocks, "count": len(blocks), "candidates": len(rows), "history": hist}
