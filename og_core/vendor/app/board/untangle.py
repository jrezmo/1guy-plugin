"""untangle: door A's engine (docs/product.md). Go through a lived-in Hermes and say two things:
which three need the user, and which things belong in a pile together.

    chat_need(row, now)         -> {"kind": "question"|"midtask", "why"} | None    one chat's last turn
    needs_you(board)            -> at most 3 x {id, kind: question|reply|failed|midtask, why (<= 12 words)}
    untangle(board, chat=None)  -> at most 5 x {name (2-3 plain words), ids (>= 3), why (<= 12 words)}
    propose(piles)              -> each pile becomes a proposal (writer "untangle") whose edits are
                                   [{op: "move", id, to_pile: "pile:<slug>"}], so it previews before yes
    kick(board)                 -> untangle + propose in the background; the board never waits
    first_run(board)            -> True until the user has judged something (open piles don't reopen the door)
    lead(board, picks, whys)    -> on a first run the three and the piles go in front of the briefing

What it reads: the board's own blocks (to-dos, jobs, chats) and board["_history"], the two-week
list of chats the sessions source already read from state.db (read-only). Nothing else.

The model: ONE call per real change, through `chat` (an injectable hook: the plugin points it at
ctx.llm). Left as None it borrows headlines.chat, the hook the plugin already wires. Model off or
failed -> a deterministic fallback (shared title words, same #tag). The result is cached in
ONEGUY_HOME/untangle.json under a content hash, so a page load never calls the model.

A pile the user said no to is never proposed again: read from the judgment ledger
(judgments.jsonl: not-now / verdict reject on `proposal:untangle-<slug>`) and from the
proposal file's dismissed entries (which remember the members). Stdlib only.
"""
import hashlib
import json
import re
import threading
import time

from . import ledger, proposals, util

WRITER = "untangle"
MAX_NEED = 3
MAX_PILES = 5
MIN_PILE = 3
WHY_WORDS = 12
HISTORY_DAYS = 14
QUIET_S = 15 * 60            # a chat still moving isn't "left" anywhere yet
POOL_CHATS, POOL_JOBS, POOL_TASKS = 120, 40, 60      # prompt bounds
LINE_CHARS = 140
RETRY_S = 6 * 3600           # after a model failure the fallback stands this long
KICK_MIN_S = 600.0           # at most one model run this often
OVERLAP = 0.6                # this much of a pile inside a rejected one = the same pile again
LEAD = ("needs", "piles")    # first-run order in the briefing rail

chat = None                  # the plugin sets this (or headlines.chat) to ctx.llm

_LOCK = threading.Lock()
_STATE = {"running": False, "last": 0.0}


def path():
    return util.oneguy("untangle.json")


def _words(s):
    return len(str(s or "").split())


def _fit(s, cap=WHY_WORDS):
    w = str(s or "").split()
    return " ".join(w[:cap]).rstrip(",;:")


def _ago(span_s):
    s = max(0.0, span_s or 0.0)
    if s < 3600:
        return f"{max(1, int(s // 60))}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d} days ago"


# ------------------------------------------------------------------ the three that need you
_OFFER = re.compile(r"^(want me to|would you like|shall i|should i (also|go ahead)|anything else|"
                    r"need anything|let me know|does that (help|work)|make sense|sound good|ok\??$)", re.I)
_DONE_ENDS = {"cron_complete", "compression", "session_reset"}   # ended on purpose; nothing was cut off


def _last_sentence(tail):
    """The last sentence of an assistant message, markdown noise stripped."""
    t = re.sub(r"[`*_>#~]+", "", str(tail or "")).strip()
    t = re.sub(r"[\s\W]+$", lambda m: "?" if "?" in m.group(0) else "", t) if t else t
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+|\n+", t) if p.strip()]
    return parts[-1] if parts else ""


def chat_need(row, now=None):
    """row: one chat from the sessions source's history ({last_role, finish, tail, last, …}).
    question: the last word in the chat is the assistant's, and it asks the user something.
    midtask:  the last turn never finished (cut off inside a tool call, hit the length limit, or
              the user's message got no answer) and the chat has gone quiet."""
    now = now or util.now()
    last = row.get("last") or 0
    quiet = now - last
    if quiet < QUIET_S or not row.get("last_role"):
        return None
    role, fin = row.get("last_role"), (row.get("finish") or "")
    if role == "assistant" and fin in ("", "stop", "end_turn"):
        sent = _last_sentence(row.get("tail"))
        if sent.endswith("?"):
            return {"kind": "question", "why": _fit(f"ended on a question to you {_ago(quiet)}, no reply since"),
                    "soft": bool(_OFFER.search(sent))}
        return None
    if (row.get("end_reason") or "") in _DONE_ENDS:
        return None
    if role == "tool" or (role == "assistant" and fin in ("tool_calls", "tool_use")):
        return {"kind": "midtask", "why": _fit(f"stopped in the middle of a step {_ago(quiet)}")}
    if role == "assistant" and fin in ("length", "error", "interrupted", "cancelled", "aborted"):
        return {"kind": "midtask", "why": _fit(f"its last reply was cut off {_ago(quiet)}")}
    if role == "user":
        return {"kind": "midtask", "why": _fit(f"your last message {_ago(quiet)} never got an answer")}
    return None


def pinned_sessions(history, now=None, cap=60):
    """Session ids that must be cards even past the sessions source's cap: the chats that need the
    user (newest few of each kind) and every chat an open or kept pile names."""
    now = now or util.now()
    out, per = [], {}
    for r in sorted(history or [], key=lambda r: -(r.get("last") or 0)):
        n = r.get("need")
        if n and per.get(n["kind"], 0) < MAX_NEED:
            per[n["kind"]] = per.get(n["kind"], 0) + 1
            out.append(r["id"])
    doc = util.read_json(util.oneguy("proposals", WRITER + ".json"), None)
    for p in (doc or {}).get("proposals") or [] if isinstance(doc, dict) else []:
        if isinstance(p, dict) and (p.get("status") or "proposed") in ("proposed", "kept"):
            for e in p.get("edits") or []:
                bid = str((e or {}).get("id") or "")
                if bid.startswith("session:") and bid[8:] not in out:
                    out.append(bid[8:])
    return out[:cap]


def needs_you(board, now=None):
    """-> at most MAX_NEED of {id, kind, why}. One of each kind first (a chat that ended on a
    question, a job whose last run failed, a thread left mid-task), then the newest of what's left.
    A real question outranks a polite offer ("want me to…?")."""
    now = now or util.parse_ts((board or {}).get("generated_at")) or util.now()
    cands = []
    for b in (board or {}).get("blocks") or []:
        d = b.get("detail") or {}
        ts = util.parse_ts(b.get("updated")) or 0
        if b.get("kind") == "session" and isinstance(d.get("needs"), dict) and b.get("status") != "parked":
            n = d["needs"]
            cands.append({"id": b["id"], "kind": n.get("kind"), "why": _fit(n.get("why")),
                          "_ts": ts, "_soft": bool(n.get("soft"))})
        elif b.get("kind") == "note" and (d.get("needs") or {}).get("kind") == "reply" and b.get("status") == "needs-you":
            # an email a person is waiting on (board/mailcal.py, from the user's own Hermes digest)
            cands.append({"id": b["id"], "kind": "reply", "why": _fit(d["needs"].get("why")), "_ts": ts, "_soft": False})
        elif b.get("kind") == "job" and b.get("status") == "failed":
            streak = d.get("failure_streak") or 0
            why = f"failed its last {streak} runs" if streak > 1 else "its last run failed"
            if ts:
                why += f", {_ago(now - ts)}"
            cands.append({"id": b["id"], "kind": "failed", "why": _fit(why), "_ts": ts, "_soft": False})
    cands.sort(key=lambda c: (c["_soft"], -c["_ts"], c["id"]))
    out, seen = [], set()
    for kind in ("question", "reply", "failed", "midtask"):   # a person waiting on a reply outranks a thread left mid-task
        c = next((c for c in cands if c["kind"] == kind), None)
        if c:
            out.append(c)
            seen.add(c["id"])
    for c in cands:
        if len(out) >= MAX_NEED:
            break
        if c["id"] not in seen:
            out.append(c)
            seen.add(c["id"])
    return [{k: v for k, v in c.items() if not k.startswith("_")} for c in out[:MAX_NEED]]


# ------------------------------------------------------------------ what there is to pile
def pool(board):
    """-> [{id, kind: chat|job|todo, title, first?, tags}] bounded, newest chats first.
    Things already filed in a kept pile are left out."""
    board = board or {}
    blocks = board.get("blocks") or []
    filed = _kept_members()
    items, seen = [], set()

    def add(bid, kind, title, first="", tags=()):
        if bid in seen or bid in filed or not title:
            return
        seen.add(bid)
        items.append({"id": bid, "kind": kind, "title": util.safe_excerpt(title, LINE_CHARS),
                      "first": util.safe_excerpt(first, LINE_CHARS), "tags": [str(t).lower() for t in tags or []]})

    hist = sorted(board.get("_history") or [], key=lambda r: -(r.get("last") or 0))[:POOL_CHATS]
    for r in hist:
        add("session:" + str(r["id"]), "chat", r.get("title") or r.get("first"), r.get("first"))
    if not hist:                                     # no history list: the chats on the board
        for b in [b for b in blocks if b.get("kind") == "session"][:POOL_CHATS]:
            add(b["id"], "chat", b.get("title"), (b.get("detail") or {}).get("first_user"))
    for b in [b for b in blocks if b.get("kind") == "job"][:POOL_JOBS]:
        add(b["id"], "job", b.get("title"))
    for b in [b for b in blocks if b.get("kind") == "task" and b.get("status") not in ("done", "parked")][:POOL_TASKS]:
        add(b["id"], "todo", b.get("title"), tags=b.get("tags"))
    return items


def _doc():
    doc = util.read_json(util.oneguy("proposals", WRITER + ".json"), None)
    return [p for p in (doc or {}).get("proposals") or [] if isinstance(p, dict)] if isinstance(doc, dict) else []


def _members(p):
    return {str(e.get("id")) for e in p.get("edits") or [] if isinstance(e, dict) and e.get("id")}


def _kept_members():
    out = set()
    for p in _doc():
        if p.get("status") == "kept":
            out |= _members(p)
    return out


def rejected():
    """-> {"slugs": {…}, "sets": [frozenset(member ids)]}: every pile the user said no to."""
    slugs, sets = set(), []
    for p in _doc():
        if p.get("status") == "dismissed":
            slugs.add(str(p.get("id") or "").replace(WRITER + "-", "", 1))
            sets.append(frozenset(_members(p)))
    pre = "proposal:" + WRITER + "-"
    gone = {}
    for j in ledger.tail(5000):
        blk = str(j.get("block") or "")
        if not blk.startswith(pre):
            continue
        if j.get("kind") == "not-now" or (j.get("kind") == "verdict" and j.get("target") == "reject"):
            gone[blk[len(pre):]] = True
        elif j.get("kind") in ("keep", "cluster"):
            gone.pop(blk[len(pre):], None)
    slugs |= set(gone)
    return {"slugs": slugs, "sets": [s for s in sets if s]}


def _is_rejected(slug, ids, rej):
    if slug in rej["slugs"]:
        return True
    s = set(ids)
    return any(len(s & r) >= OVERLAP * len(s) for r in rej["sets"])


# ------------------------------------------------------------------ validation
_NAME = re.compile(r"^[a-z][a-z'’&-]{0,19}( [a-z0-9][a-z0-9'’&-]{0,19}){1,2}$")
_IDISH = re.compile(r"\d{4,}|[0-9a-f]{6,}|[_/\\#:@^]|\.\w")


def clean_name(raw):
    """2-3 plain lowercase words, or '' (no ids, paths or #tags)."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip().strip(".!?\"'").lower()
    if not s or _IDISH.search(s) or not _NAME.match(s):
        return ""
    return s


def validate(raw, items, rej=None):
    """Whatever came back (the model's piles, or the fallback's) -> the piles we stand behind:
    every id is a real item, no item sits in two piles, >= MIN_PILE each, a plain 2-3 word name,
    a why of <= WHY_WORDS words, never a rejected pile, at most MAX_PILES."""
    rej = rej or {"slugs": set(), "sets": []}
    byid = {i["id"]: i for i in items}
    handles = {i.get("h"): i["id"] for i in items if i.get("h")}
    out, used, slugs = [], set(), set()
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        name = clean_name(p.get("name"))
        slug = util.slugify(name)
        if not name or not slug or slug in slugs:
            continue
        ids = []
        for x in p.get("ids") if isinstance(p.get("ids"), list) else []:
            bid = handles.get(str(x)) or (str(x) if str(x) in byid else None)
            if bid and bid not in used and bid not in ids:
                ids.append(bid)
        if len(ids) < MIN_PILE or _is_rejected(slug, ids, rej):
            continue
        why = _fit(util.redact(re.sub(r"\s+", " ", str(p.get("why") or "")).strip()))
        if not why or _IDISH.search(why):
            why = _default_why(ids, byid)
        used |= set(ids)
        slugs.add(slug)
        out.append({"name": name, "ids": ids, "why": why})
        if len(out) >= MAX_PILES:
            break
    return out


def _noun(ids, byid, n=None):
    kinds = {byid[i]["kind"] for i in ids}
    one = {"chat": "chat", "job": "scheduled job", "todo": "to-do"}
    if len(kinds) == 1:
        w = one[next(iter(kinds))]
        return w if (n or len(ids)) == 1 else w + "s"
    return "things"


def _default_why(ids, byid):
    return f"{len(ids)} {_noun(ids, byid)} about the same thing"


# ------------------------------------------------------------------ the deterministic fallback
_STOP = set("""a an and are as at be but by can do does for from get got have how i if in into is it its
let like make me my need new not of on one or our out over should so some than that the their them then
there these this to up us was we what when where which who why will with you your about after again all
also any app best chat check quick subagent session question thing things stuff test help""".split()) - {"app"}


def _tokens(text):
    return [w for w in re.findall(r"[a-z][a-z']{2,}", str(text or "").lower()) if w not in _STOP]


def _keys(it):
    """What an item can be grouped by: its title words, adjacent word pairs, the words before a
    'Topic:' prefix as one phrase, and its #tags."""
    title = it["title"]
    toks = _tokens(title)
    keys = set(toks) | {t for t in it.get("tags") or [] if len(t) >= 3}
    keys |= {a + " " + b for a, b in zip(toks, toks[1:])}
    head = title.split(":", 1)[0] if ":" in title[:40] else ""
    hw = re.findall(r"[a-z][a-z']+", head.lower())
    if 1 <= len(hw) <= 3:
        keys.add(" ".join(hw))
    return keys


def fallback(items, rej=None):
    """Group the obvious: the phrase (or word, or #tag) shared by the most unfiled items makes a
    pile, biggest first. No model."""
    rej = rej or {"slugs": set(), "sets": []}
    byid = {i["id"]: i for i in items}
    keys = {i["id"]: _keys(i) for i in items}
    left = [i["id"] for i in items]
    raw = []
    for _ in range(MAX_PILES * 3):
        df = {}
        for bid in left:
            for k in keys[bid]:
                df.setdefault(k, []).append(bid)
        cands = [(len(v) * (2 if " " in k else 1), k) for k, v in df.items()
                 if len(v) >= MIN_PILE and (len(items) < 8 or len(v) <= 0.6 * len(items))]
        if not cands:
            break
        cands.sort(key=lambda c: (-c[0], c[1]))
        key = cands[0][1]
        ids = list(df[key])
        words = key.split()
        ids += [b for b in left if b not in ids and set(words) & set(byid[b].get("tags") or [])]   # #job joins "job search"
        left = [b for b in left if b not in ids]
        name = key if 2 <= len(words) <= 3 else f"{key} {_noun(ids, byid, 2).replace('scheduled ', '')}"
        if not clean_name(name) or _is_rejected(util.slugify(clean_name(name)), ids, rej):
            continue
        raw.append({"name": name, "ids": ids, "why": f"{len(ids)} {_noun(ids, byid)} that all say {key}"})
    return validate(raw, items, rej)


# ------------------------------------------------------------------ the one model call
def build_prompt(items):
    lines = []
    for i in items:
        s = f'{i["h"]} [{i["kind"]}] {i["title"]}'
        if i.get("first") and i["first"].lower()[:60] != i["title"].lower()[:60]:
            s += f' :: {i["first"]}'
        if i.get("tags"):
            s += " " + " ".join("#" + t for t in i["tags"][:3])
        lines.append(s)
    return (
        "You are tidying someone's desk. Below are their recent chats, scheduled jobs and to-dos, one per line: "
        "a handle, its kind, its title, sometimes its first line.\n"
        f"Propose at most {MAX_PILES} piles of things that are plainly the same project or topic. Rules:\n"
        f"- a pile has at least {MIN_PILE} handles; a handle goes in at most one pile; leave out anything that doesn't clearly belong\n"
        "- name: 2 or 3 plain lowercase words a person would say out loud (\"kitchen renovation\"), no ids, paths, #tags or digits\n"
        f"- why: one plain line, at most {WHY_WORDS} words, lowercase, no handles\n"
        "- fewer good piles beat more weak ones; an empty list is a fine answer\n"
        'Reply with JSON only: {"piles": [{"name": "...", "ids": ["a1", "a2", "a3"], "why": "..."}]}\n\n'
        + "\n".join(lines))


def _parse(text):
    t = str(text or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    obj = json.loads(m.group(0) if m else t)
    return obj.get("piles") if isinstance(obj, dict) else obj


def _llm(hook=None):
    if hook is not None:
        return hook
    if chat is not None:
        return chat
    from . import headlines
    return headlines.chat if headlines.ENABLED else None


def content_key(items, rej, model_on):
    raw = json.dumps([[i["id"], i["title"], i.get("tags")] for i in sorted(items, key=lambda i: i["id"])]
                     + [sorted(rej["slugs"]), sorted(sorted(s) for s in rej["sets"]), bool(model_on), "u1"],
                     ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def untangle(board, chat=None, now=None, force=False):   # noqa: A002 - the hook is named for what it is
    """-> the piles (validated). Cached by content hash: the model is asked once per real change."""
    now = now or util.now()
    items = pool(board)
    for n, it in enumerate(items, 1):
        it["h"] = f"a{n}"
    rej = rejected()
    hook = _llm(chat)
    key = content_key(items, rej, hook is not None)
    cached = util.read_json(path(), None)
    if not force and isinstance(cached, dict) and cached.get("key") == key:
        retry = cached.get("by") == "fallback" and cached.get("error") and now - (cached.get("at_ts") or 0) > RETRY_S
        if not retry:
            return validate(cached.get("piles"), items, rej)
    piles, by, err = [], "fallback", None
    if hook is not None and len(items) >= MIN_PILE:
        try:
            piles, by = validate(_parse(hook(build_prompt(items))), items, rej), "model"
        except Exception as e:   # noqa: BLE001 - any model trouble means the fallback, never an error
            err = f"{type(e).__name__}: {e}"[:200]
    if by == "fallback":
        piles = fallback(items, rej)
    util.write_json_atomic(path(), {"key": key, "by": by, "error": err, "at": util.iso(now), "at_ts": now,
                                    "items": len(items), "piles": piles})
    return piles


# ------------------------------------------------------------------ piles -> proposals
def _title(p, byid):
    ids = [i for i in p["ids"] if i in byid]
    topic = re.sub(r" (things|chats|jobs|to-dos)$", "", p["name"])     # "kitchen things" -> "… are all kitchen"
    return f"these {len(p['ids'])} {_noun(ids, byid) if ids else 'things'} are all {topic}"


def propose(piles, items=None):
    """Write the piles as proposals (preview before yes). Open piles that are no longer proposed
    go; anything the user already answered stays answered (proposals.put)."""
    byid = {i["id"]: i for i in items or []}
    out = []
    for p in piles or []:
        slug = util.slugify(p["name"])
        out.append({"id": f"{WRITER}-{slug}", "title": _title(p, byid), "summary": p["why"], "why": p["why"],
                    "kind": "pile", "pile": p["name"],
                    "edits": [{"op": "move", "id": i, "to_pile": "pile:" + slug} for i in p["ids"]]})
    have = _doc()
    if not out and not any((p.get("status") or "proposed") == "proposed" for p in have):
        return None                                  # nothing to say and nothing to take back
    return proposals.put(WRITER, out, replace_open=True)


def run(board, chat=None, force=False):   # noqa: A002
    piles = untangle(board, chat=chat, force=force)
    propose(piles, pool(board))
    return piles


def kick(board, chat=None):   # noqa: A002
    """Start one background untangle when the content changed. Never blocks, never raises."""
    try:
        items = pool(board)
        if len(items) < MIN_PILE and not _doc():
            return False
        hook = _llm(chat)
        cached = util.read_json(path(), None)
        key = content_key(items, rejected(), hook is not None)
        if isinstance(cached, dict) and cached.get("key") == key and not (
                cached.get("error") and util.now() - (cached.get("at_ts") or 0) > RETRY_S):
            return False
        with _LOCK:
            now = time.time()
            if _STATE["running"] or (hook is not None and now - _STATE["last"] < KICK_MIN_S):
                return False
            _STATE.update(running=True, last=now)
        snap = {"blocks": [dict(b) for b in board.get("blocks") or [] if b.get("kind") in ("session", "job", "task")],
                "_history": list(board.get("_history") or [])}

        def work():
            try:
                run(snap, chat=chat)
            except Exception:   # noqa: BLE001
                pass
            finally:
                with _LOCK:
                    _STATE["running"] = False
        threading.Thread(target=work, name="untangle", daemon=True).start()
        return True
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------ the first run
def open_piles(board):
    return [b for b in (board or {}).get("blocks") or []
            if b.get("kind") == "proposal" and (b.get("detail") or {}).get("writer") == WRITER
            and b.get("status") == "proposed"]


def first_run(board):
    """The door is still open: the user hasn't judged anything yet. Open piles do not reopen it: on a
    judged board they keep their cards (board.plain_layer), never the first-visit opener."""
    return not ledger.tail(1)


def facts(board):
    """The numbers the opener says, each one counted: chats in the last two weeks, scheduled jobs."""
    board = board or {}
    hist = board.get("_history")
    chats = len(hist) if hist is not None else (board.get("history") or {}).get("chats", 0)
    jobs = sum(1 for b in board.get("blocks") or [] if b.get("kind") == "job")
    return {"chats": chats, "days": HISTORY_DAYS, "jobs": jobs}


def lead(board, picks, whys, need):
    """On a first run the three that need the user and the pile proposals go in front."""
    byid = {b["id"]: b for b in (board or {}).get("blocks") or []}
    groups = {"needs": [(n["id"], n["why"]) for n in need],
              "piles": [(b["id"], "goes together") for b in open_piles(board)]}
    front, whys = [], dict(whys or {})
    for g in LEAD:
        for bid, why in groups[g]:
            if bid in byid and bid not in {b["id"] for b in front}:
                front.append(byid[bid])
                whys[bid] = why
    ids = {b["id"] for b in front}
    return front + [b for b in picks if b["id"] not in ids], whys
