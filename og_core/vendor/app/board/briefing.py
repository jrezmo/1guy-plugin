"""Briefing: what the one guy leads with when the user opens home.

    choose(blocks) -> (picks, broke)
        picks: at most MAX_PICKS blocks the user should act on today. Order of
               preference: needs-you (decisions) -> due within DUE_SOON_D days
               -> the one guy's proposals -> p1 tasks in motion. Within a tier,
               blocks whose headline carries an `ask` (a quick choice) come
               first, then attention. At most PER_PROJECT from one project
               (blocks with no project don't count against each other).
        broke: at most one failed block (a job that broke), loudest first.
    apply(board) marks the chosen blocks `for_you: true` and sets
        board["briefing"] = {line, for_you: [ids], broke: [ids], by}
        where `line` is the LLM line cached by headlines.py for exactly this
        pick (key = ids + titles), else a deterministic template.

    moved(board) marks what changed since the user last looked, and wasn't his doing (frontier 3):
        block["changed"] = {what, by, at} and board["briefing"]["moved"] = {ids, line}.
        look(board=True) / look(item=id) record that he looked (ONEGUY_HOME/seen.json).

Pure and cheap: no I/O beyond the headline cache read and seen.json; never calls the LLM.
"""
import hashlib
import os
import re
import threading
import time

from . import util

MAX_PICKS = 200   # v1.2: the briefing is an unbounded horizontal rail — no 3-card cap
PER_PROJECT = 2
DUE_SOON_D = 3
ACT_KINDS = {"task", "crew", "proposal"}
NUM = {1: "one", 2: "two", 3: "three"}
def _num(n):
    if n in NUM: return NUM[n]
    if n <= 12: return {4:"four",5:"five",6:"six",7:"seven",8:"eight",9:"nine",10:"ten",11:"eleven",12:"twelve"}[n]
    return str(n)   # the rail is unbounded (v1.2) — just say the number


def tier(b, now=None):
    """-> 0..3 (lower = sooner) or None if it isn't a today thing."""
    if b.get("kind") not in ACT_KINDS or b.get("status") in ("done", "parked"):
        return None
    if b.get("status") == "needs-you":
        return 0
    due = util.parse_ts(b.get("due"))
    if due is not None and (due - (now or time.time())) <= DUE_SOON_D * 86400:
        return 1
    if b.get("status") == "proposed":
        return 2
    if b.get("kind") == "task" and b.get("status") == "active" and b.get("priority") == 1:
        return 3
    return None


def _has_ask(b):
    return bool((b.get("headline") or {}).get("ask"))


def choose(blocks, now=None):
    now = now or time.time()
    cands = []
    for i, b in enumerate(blocks):
        t = tier(b, now)
        if t is not None:
            cands.append((t, not _has_ask(b), -(b.get("attention") or 0), i, b))
    cands.sort(key=lambda c: c[:4])
    picks, per = [], {}
    for *_, b in cands:
        p = b.get("project") or b["id"]     # no project = its own group
        if per.get(p, 0) >= PER_PROJECT:
            continue
        picks.append(b)
        per[p] = per.get(p, 0) + 1
        if len(picks) >= MAX_PICKS:
            break
    failed = [b for b in blocks if b.get("status") == "failed"]
    failed.sort(key=lambda b: (b.get("kind") != "job", -(b.get("attention") or 0)))
    return picks, failed[:1]


def line_key(picks, broke):
    raw = "|".join(f"{b['id']}={(b.get('headline') or {}).get('title') or ''}" for b in picks)
    raw += "||" + "|".join(b["id"] for b in broke)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def template_line(picks, broke):
    n = len(picks)
    asks = [b for b in picks if _has_ask(b)]
    if not n:
        s = "nothing needs you today."
        return s + (" one thing broke, though." if broke else " it's all ticking over.")
    s = "one thing today." if n == 1 else f"{_num(n)} things today."
    if len(asks) == 1:
        opts = len(asks[0]["headline"]["ask"].get("options") or [])
        quick = "a quick yes or no" if opts == 2 else "a quick pick"
        s += f" it's {quick}." if n == 1 else f" one's {quick}."
    elif len(asks) > 1:
        s += " both are quick calls." if len(asks) == n == 2 else (
            " all quick calls." if len(asks) == n else f" {_num(len(asks))} are quick calls.")
    if broke:
        s += " and one thing broke."
    return s


def door_line(door):
    """Door A's opener (docs/product.md): what he found when he went through the user's Hermes.
    Every number is counted by the caller (untangle.facts / untangle.needs_you); nothing is rounded
    or invented. '' on a home with no chats and no jobs: there was nothing to go through."""
    chats, jobs, n = int(door.get("chats") or 0), int(door.get("jobs") or 0), len(door.get("need") or [])
    found = []
    if chats:
        found.append(f"{chats} chat{'' if chats == 1 else 's'} in two weeks")
    if jobs:
        found.append(f"{jobs} scheduled job{'' if jobs == 1 else 's'}")
    if not found:
        return ""
    s = "i went through your hermes. " + ", ".join(found) + "."
    if not n:
        return s + " nothing needs you right now."
    return s + (" one needs you." if n == 1 else f" {_num(n)} need you.")


_CLOCK = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b", re.I)
_ASIDE = re.compile(r"^\s*(?:heads[ -]?up|fyi|note|reminder|ps|p\.s\.)\b", re.I)


def tidy_line(line):
    """His opener is ONE short line. A written line may arrive as three sentences with a heads-up
    and a clock time baked in ("…approve the restart so it runs at 4:13pm"), and it is cached, so it
    gets read hours later (seen on a real board). Keep at most the first two sentences, drop any
    that is an aside or names a time of day; '' when nothing usable is left (the caller falls back
    to the template). A heads-up belongs on its own card, never in the opener."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", str(line or "").strip()) if p.strip()]
    keep = [p for p in parts if not _ASIDE.search(p) and not _CLOCK.search(p)][:2]
    return " ".join(keep)


def apply(board, data=None, now=None, picks=None, whys=None, door=None):
    """Mark for_you on the board's blocks and set board['briefing']. Returns the briefing.
    picks/whys: pre-chosen by selector.py (phase 3.5); when given, they win and
    choose() is skipped — the LLM line still keys off exactly this pick.
    door: door A's first-run facts {chats, jobs, need: [{id, kind, why}]} (untangle.py). When given
    and there is something to count, the opener is door_line(door) and a failed job among the three
    stays among the three instead of moving to `broke`."""
    from . import headlines
    blocks = board.get("blocks") or []
    if picks is None:
        picks, broke = choose(blocks, now)
    else:
        mine = {n["id"] for n in (door or {}).get("need") or []} if door and door_line(door) else set()
        broke = [b for b in blocks if b.get("status") == "failed" and b["id"] not in mine]
        broke.sort(key=lambda b: (b.get("kind") != "job", -(b.get("attention") or 0)))
        broke = broke[:1]
        # by id, not identity: the selector may hand back copies of the board's blocks, and then the
        # broken thing stayed in the picks — counted in "ten things today" AND in "one thing broke"
        gone = {b["id"] for b in broke}
        picks = [b for b in picks if b["id"] not in gone]
    for b in picks + broke:
        b["for_you"] = True
    data = data if data is not None else headlines.load()
    cached = data.get("briefing") or {}
    key = line_key(picks, broke)
    line = tidy_line(cached.get("line")) if cached.get("key") == key else ""
    opener = door_line(door) if door else ""
    if opener:
        line, by = opener, "door"
    elif line:
        by = "guy"
    else:
        line, by = template_line(picks, broke), "template"
    br = {"line": line, "for_you": [b["id"] for b in picks], "broke": [b["id"] for b in broke], "by": by}
    if whys:
        br["whys"] = whys
    if opener:
        br["door"] = {"chats": door.get("chats") or 0, "jobs": door.get("jobs") or 0,
                      "need": [n["id"] for n in door.get("need") or []]}
    board["briefing"] = br
    return br


# ---------- what moved since you left (frontier 3: change outlines) ----------
# Anything the one guy, the crew or the user's Hermes changed since the user last looked is marked until he
# looks at it. ONEGUY_HOME/seen.json:
#   board_seen_at   when the board was last in front of him for a few seconds (the client posts it)
#   item_seen_at    {id: when he last focused that item}; focusing is what clears a mark
#   known           {id: [status, pile]} as of the last board build: the baseline the next build diffs
#   at              when that baseline was taken
#   moved           {id: {what, by, at}} marks still standing
# the user's own hands never mark: every gesture and yes/no of his is a ledger line without `by`, and a
# block he touched since the baseline is taken as his. A ledger line WITH `by` is a crew judgment.
MOVED_MAX_S = 3 * 86400       # a mark nobody looked at for three days stops asking
MOVED_MAX = 12                # and the desk never wears more than this many outlines
SLACK_S = 2.0                 # clocks and write order: a reply this close to his look was read
HIS_S = 120.0                 # a source can take this long to show what his own gesture did
THE_GUY = "the guy"
HERMES = "hermes"
REPLIED = "replied in its thread"
_WRITER_NAME = {"untangle": THE_GUY, "guy": THE_GUY, "oneguy": THE_GUY, "1guy": THE_GUY}
_STATUS_WHAT = {"failed": "this broke", "needs-you": "now waiting on you"}
_NEW_WHAT = {"proposal": "a new idea", "crew": "new from the crew", "artifact": "made while you were away",
             "task": "added to your list"}
_CREW_WHAT = {"park": "put away for later", "unpark": "brought back out", "done": "marked done",
              "promote": "moved up", "bury": "moved down", "hold": "a slot held for it",
              "release": "its slot let go"}
_J_NAMES = {"", "j", "me", "user", "you"}
_SKIP_KINDS = ("project", "widget", "plugin", "talk")
_SEEN_LOCK = threading.Lock()


def seen_path():
    home = os.environ.get("ONEGUY_HOME")
    return os.path.join(os.path.expanduser(home), "seen.json") if home else util.oneguy("seen.json")


def _seen():
    st = util.read_json(seen_path(), None)
    st = st if isinstance(st, dict) else {}
    for k in ("item_seen_at", "moved"):
        if not isinstance(st.get(k), dict):
            st[k] = {}
    return st


def look(board=False, item=None, now=None):
    """The user looked: at the board (visible a few seconds) and/or at one item (he focused it). Focusing
    takes the item's mark off for good. -> {board_seen_at, item, cleared}"""
    now = now or time.time()
    with _SEEN_LOCK:
        st = _seen()
        cleared = False
        if board:
            st["board_seen_at"] = now
        if item:
            st["item_seen_at"][str(item)] = now
            cleared = st["moved"].pop(str(item), None) is not None
            if len(st["item_seen_at"]) > 4000:      # the oldest looks go; an unlisted item falls back to board_seen_at
                st["item_seen_at"] = dict(sorted(st["item_seen_at"].items(), key=lambda kv: -kv[1])[:3000])
        util.write_json_atomic(seen_path(), st)
    return {"board_seen_at": st.get("board_seen_at"), "item": item or None, "cleared": cleared}


def _who(name):
    n = str(name or "").strip().lower()
    return _WRITER_NAME.get(n) or util.clip(n, 24) or THE_GUY


def _pile(pid, board):
    for p in board.get("projects") or []:
        if p.get("id") == pid:
            return re.sub(r"\s*\([^)]*\)\s*$", "", str(p.get("title") or "")).strip().lower()
    return str(pid or "").split(":", 1)[-1].replace("-", " ")


def _judged(rows):
    """ledger rows -> ({block: last time the user touched it}, {block: the latest crew row for it})"""
    mine, crew = {}, {}
    for r in rows or []:
        bid, at = r.get("block"), util.parse_ts(r.get("at"))
        if not bid or at is None:
            continue
        if str(r.get("by") or "").strip().lower() in _J_NAMES:
            mine[bid] = max(mine.get(bid, 0), at)
        elif at >= crew.get(bid, {}).get("_at", 0):
            crew[bid] = dict(r, _at=at)
    return mine, crew


def _crew_what(row, board):
    kind, target = row.get("kind"), row.get("target")
    if kind == "cluster":
        return "moved to " + _pile(target, board) if target else "moved to another pile"
    if kind == "schedule":
        return "put on your calendar" if target else "taken off your calendar"
    return _CREW_WHAT.get(kind) or "changed"


def _replies(threads):
    """threads.json -> {item key: when its thread last had a turn land}"""
    out = {}
    for key, rec in ((threads or {}).get("threads") or {}).items():
        at = ((rec or {}).get("surface") or {}).get("last_activity")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
            out[key] = float(at)
    return out


def _load_threads():
    try:
        import threads as T          # app/threads.py (vendored beside board/)
        return T.load()
    except Exception:   # noqa: BLE001 - no thread map is an honest "no replies"
        return {}


def _one(b, board, prev, his, first):
    """One block against its baseline -> {what, by} or None. `his`: the user touched it since the baseline."""
    d, kind = b.get("detail") or {}, b.get("kind")
    if prev is None:                                  # wasn't on the board last time
        if first:
            return None
        if kind == "proposal":
            return {"what": _NEW_WHAT[kind], "by": _who(d.get("writer"))}
        if kind == "crew":
            return {"what": _NEW_WHAT[kind], "by": "the crew"}
        if kind == "artifact":
            return {"what": _NEW_WHAT[kind], "by": _who(d.get("writer"))}
        origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
        if kind == "task" and origin.get("label") and not his:     # an agent wrote it onto his list
            return {"what": _NEW_WHAT[kind], "by": _who(origin["label"])}
        return None
    if his or not isinstance(prev, list) or len(prev) != 2:
        return None
    if prev[0] != b.get("status") and b.get("status") in _STATUS_WHAT:
        who = (b.get("world") or {}).get("who")
        return {"what": _STATUS_WHAT[b["status"]], "by": _who(who) if who else HERMES}
    if prev[1] != b.get("project") and b.get("project") and kind != "proposal":
        return {"what": "moved to " + _pile(b["project"], board), "by": THE_GUY}
    return None


def changes(board, st, now, rows=None, threads=None, here=False):
    """Pure: the board + the seen state + ledger rows + the thread map -> {id: {what, by, at}}, every
    mark that should stand right now. No baseline yet (a first look) means nothing new is marked.
    here: he has been at the desk since the last build, so a card that appears or flips now did it
    in front of him (his own pile proposals landing on a first run, a chat he is in): not "while you
    were away". A crew judgment or a thread reply still marks; those are said by someone."""
    blocks = [b for b in board.get("blocks") or [] if b.get("kind") not in _SKIP_KINDS]
    known, since = st.get("known"), float(st.get("at") or 0)
    first = not isinstance(known, dict)
    known = {} if first else known
    item_seen, board_seen = st.get("item_seen_at") or {}, float(st.get("board_seen_at") or 0)
    if not board_seen and not item_seen:
        # he has never looked at this desk, so he has not been away from it: what turns up between the
        # first build and his first look (the piles land on the second build) is the desk he opens on.
        # A crew judgment still marks: someone did that, and the ledger says who.
        here = True
    mine, crew = _judged(rows)
    replies = _replies(threads)
    ids = {b["id"] for b in blocks}
    out = {}
    for bid, m in (st.get("moved") or {}).items():       # marks still standing from earlier builds
        at = util.parse_ts(m.get("at")) if isinstance(m, dict) else None
        if bid not in ids or at is None or now - at > MOVED_MAX_S:
            continue
        if mine.get(bid, 0) > at or float(item_seen.get(bid) or 0) > at:   # he dealt with it, or looked
            continue
        out[bid] = {"what": m.get("what"), "by": m.get("by"), "at": at}
    for b in blocks:
        bid = b["id"]
        looked = float(item_seen.get(bid) or 0)
        his = mine.get(bid, 0) > 0 and mine[bid] >= since - HIS_S
        c = crew.get(bid)
        if c and c["_at"] > max(since - SLACK_S, looked):          # a crew judgment says who and what
            out[bid] = {"what": _crew_what(c, board), "by": _who(c.get("by")), "at": c["_at"]}
        else:
            m = None if here else _one(b, board, known.get(bid), his, first)
            if m:
                at = util.parse_ts(b.get("updated"))
                out[bid] = dict(m, at=at if at and since <= at <= now else now)
        # a reply he hasn't read: its thread moved after he last looked at it (never focused: at the board)
        r, read = replies.get(bid), looked or board_seen
        if r and bid not in out and read and r > read + SLACK_S and now - r <= MOVED_MAX_S:
            out[bid] = {"what": REPLIED, "by": THE_GUY, "at": r}
    keep = sorted(out, key=lambda i: out[i]["at"], reverse=True)[:MOVED_MAX]
    return {i: {"what": " ".join(str(out[i]["what"] or "changed").split()[:8]), "by": out[i]["by"] or THE_GUY,
                "at": util.iso(out[i]["at"])} for i in keep}


def moved_line(n):
    if not n:
        return ""
    return "one thing moved while you were away." if n == 1 else f"{_num(n)} things moved while you were away."


def moved(board, now=None, rows=None, threads=None, persist=True, here=False):
    """Mark what moved on the board's blocks (block['changed']) and say how many in the briefing
    (briefing['moved'] = {ids oldest first, line}). Rolls the baseline forward in seen.json.
    Never raises: a board without marks is still a board."""
    now = now or time.time()
    try:
        from . import ledger
        with _SEEN_LOCK:
            st = _seen()
            rows = ledger.tail(800) if rows is None else rows
            threads = _load_threads() if threads is None else threads
            marks = changes(board, st, now, rows, threads, here)
            new = dict(st, at=now, moved=marks,
                       known={b["id"]: [b.get("status"), b.get("project")] for b in board.get("blocks") or []})
            if persist and (new["moved"] != st.get("moved") or new["known"] != st.get("known")):
                util.write_json_atomic(seen_path(), new)
        for b in board.get("blocks") or []:
            if b["id"] in marks:
                b["changed"] = marks[b["id"]]
        ids = sorted(marks, key=lambda i: util.parse_ts(marks[i]["at"]) or 0)
        if ids:
            board.setdefault("briefing", {})["moved"] = {"ids": ids, "line": moved_line(len(ids))}
        return marks
    except Exception:   # noqa: BLE001 - defensive: the outline is a nicety, the board is not
        return {}
