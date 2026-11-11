"""Headlines: the one guy's plain-language layer over the raw board.

For the loudest open blocks (top TOP_N by attention, plus every needs-you,
failed or proposed one) he writes a card face the user gets at a glance:

    block["headline"] = {title (<= 8 words), why (<= 14 words),
                         ask? {question, options: [{label (<= 3 words), say}] x 2-3},
                         first_message, hash, model, at}

Written in batches (one LLM call per <= BATCH blocks, strict JSON) through the
Hermes API server (glm flash, cheap). Cached in ~/.hermes/1guy/headlines.json,
keyed by block id + a content hash, so a headline is regenerated only when the
block's content changes (or PROMPT_VERSION does). Ids gone from the board for
PRUNE_S are pruned.

The board NEVER waits on the LLM: attach() only reads the cache (stale
headlines are attached too, marked `stale: true`), and kick() starts at most
one background refresh at a time, at most every KICK_MIN_S, backing off after
errors. A cross-process lock file keeps the CLI and the server from refreshing
at once.

CLI (warm the cache from cron or by hand; run from the repo root):
    python3 -m app.board.headlines --refresh        # missing + stale only
    python3 -m app.board.headlines --refresh --all  # regenerate every target
    python3 -m app.board.headlines --show           # print title | why | ask
    python3 app/board/headlines.py --refresh        # same, as a script path
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

if __package__ in (None, ""):          # run as a script path: python3 app/board/headlines.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "board"
    import board  # noqa: F401  (makes the relative imports below resolve)

from . import util

PROMPT_VERSION = "h5"
TOP_N = 25                 # open blocks by attention that get a headline
BATCH = 12                 # blocks per LLM call
MAX_BATCHES = 4            # per background run (the CLI has no cap)
KICK_MIN_S = 90.0          # at most one background refresh this often
ERROR_BACKOFF_S = 300.0    # after a failed LLM call
RETRY_INVALID_S = 6 * 3600  # a block whose headline failed validation is retried after this
SWEEP_S = 6 * 3600         # refresh `seen` / prune even when nothing is missing
PRUNE_S = 14 * 86400       # forget ids gone from the board this long
TIMEOUT_S = 180.0
SKIP_KINDS = {"project"}   # project tiles are already named
CLOSED = {"done", "parked"}

API_BASE = os.environ.get("ONEGUY_HERMES_API", "http://oneguy.invalid")
ENV_PATH = os.devnull   # never reads a key file
CONFIG_PATH = os.path.join(util.HOME, ".hermes", "config.yaml")
ENABLED = os.environ.get("ONEGUY_HEADLINES", "on").lower() not in ("off", "0", "false", "no")

TITLE_WORDS, WHY_WORDS, LABEL_WORDS, QUESTION_WORDS = 8, 14, 3, 12
FILLER_OPTION = re.compile(r"^(you decide|your call|other|something else|not sure|skip|later|dunno)$", re.I)
FIRST_WORDS, LINE_WORDS = 60, 18


def path():
    return util.oneguy("headlines.json")


# ---------- content hash ----------
def _clip(s, n):
    return util.clip(util.redact(str(s or "")), n)


def hash_parts(b):
    """The parts of a block whose change means the headline should be rewritten.
    Volatile bits (message counts, last-run times) are left out on purpose."""
    d = b.get("detail") or {}
    k = b.get("kind")
    parts = [k, b.get("status"), str(b.get("due") or ""), b.get("title") or ""]
    if k == "task":
        parts += [d.get("text") or b.get("summary"), d.get("section")]
    elif k == "crew":
        parts += [(d.get("body") or "")[:1500], ",".join(d.get("labels") or []), str(d.get("verdict"))]
    elif k == "proposal":
        parts += [b.get("summary"), d.get("why")]
    elif k == "job":
        parts += [str(d.get("last_status"))]
        if b.get("status") == "failed":
            parts.append(re.sub(r"\d", "", (d.get("last_output_excerpt") or "")[:200]))
    elif k == "session":
        pass                       # title + status: a live thread shouldn't churn its headline
    else:
        parts += [b.get("summary")]
    return [str(p or "") for p in parts]


def content_hash(b):
    raw = PROMPT_VERSION + "\x1f" + "\x1f".join(hash_parts(b))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ---------- which blocks ----------
def targets(blocks, n=TOP_N):
    """-> blocks that deserve a headline, loudest first."""
    out, seen = [], set()
    open_ = [b for b in blocks if b.get("status") not in CLOSED and b.get("kind") not in SKIP_KINDS
             and not (b.get("headline") or {}).get("own")]
    open_.sort(key=lambda b: -(b.get("attention") or 0))
    for b in open_[:n]:
        out.append(b)
        seen.add(b["id"])
    for b in open_[n:]:
        if b.get("status") in ("needs-you", "failed", "proposed") and b["id"] not in seen:
            out.append(b)
            seen.add(b["id"])
    return out


# ---------- validation ----------
PATH_RE = re.compile(r"(~/|\$HOME|(?:^|\s)/[\w.-]+/|\b[\w-]+/[\w.-]+/|\b[\w-]+\.(?:md|py|js|json|sh|ya?ml|html|txt|mp4|css|db)\b)", re.I)
ID_RE = re.compile(r"(\d{8}_\d{6}|\b(?=[0-9a-f]*\d)[0-9a-f]{7,}\b|(?:^|\s)#\w|"
                   r"\b(?:task|session|crew|proposal|job|media|project|goal):\S|\bapi-[0-9a-f]{6,}|"
                   r"\b(?:api|bg|cron|deleg|sess)?_?\d{6,13}_[0-9a-f]{4,})", re.I)


def _words(s):
    return len(str(s or "").split())


def _plain_problems(s, what):
    probs = []
    if PATH_RE.search(s):
        probs.append(f"{what} has a path")
    if ID_RE.search(s):
        probs.append(f"{what} has an id/tag")
    return probs


def _tidy(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip().strip('"').strip()
    return s


def _fit_words(s, cap):
    """Too long by a little? Keep the leading clause if that fits; else None."""
    if _words(s) <= cap:
        return s
    if _words(s) > cap + 8:
        return None
    best = None
    for m in re.finditer(r"[,;:.\u2014]\s|\s[\u2014-]\s", s):
        head = s[:m.start()].strip()
        if 4 <= _words(head) <= cap:
            best = head
    return best


def validate(raw):
    """raw dict from the LLM -> (clean {title, why, ask, first_message} | None, [problems]).
    Title/why problems reject the headline; a bad ask is dropped (kept headline)."""
    probs = []
    if not isinstance(raw, dict):
        return None, ["not an object"]
    title = _tidy(raw.get("title")).rstrip(".")
    why = _tidy(raw.get("why"))
    why = _fit_words(why, WHY_WORDS) or why
    if not title:
        probs.append("empty title")
    elif _words(title) > TITLE_WORDS:
        probs.append(f"title > {TITLE_WORDS} words")
    if not why:
        probs.append("empty why")
    elif _words(why) > WHY_WORDS:
        probs.append(f"why > {WHY_WORDS} words")
    probs += _plain_problems(title, "title") + _plain_problems(why, "why")
    if probs:
        return None, probs
    out = {"title": title, "why": why, "ask": None}
    ask = raw.get("ask")
    if ask:
        ask_probs = []
        q = _tidy(ask.get("question")) if isinstance(ask, dict) else ""
        opts = ask.get("options") if isinstance(ask, dict) else None
        clean = []
        if not q or _words(q) > QUESTION_WORDS:
            ask_probs.append("bad question")
        ask_probs += _plain_problems(q, "question")
        if not isinstance(opts, list) or not 2 <= len(opts) <= 3:
            ask_probs.append("options not 2-3")
        else:
            for o in opts:
                lab = _tidy((o or {}).get("label")).rstrip(".") if isinstance(o, dict) else ""
                say = _tidy((o or {}).get("say")) if isinstance(o, dict) else ""
                if not lab or _words(lab) > LABEL_WORDS or not say or _words(say) > 40:
                    ask_probs.append(f"bad option {o!r:.60}")
                    continue
                if FILLER_OPTION.match(lab):
                    probs.append(f"filler option dropped: {lab}")
                    continue
                ask_probs += _plain_problems(lab, "label")
                clean.append({"label": lab, "say": say})
            if len(clean) < 2:
                ask_probs.append("fewer than 2 real options")
            if len({c["label"].lower() for c in clean}) != len(clean):
                ask_probs.append("duplicate labels")
        if ask_probs:
            probs += ["ask dropped: " + p for p in ask_probs]
        else:
            out["ask"] = {"question": q, "options": clean}
    fm = _tidy(raw.get("first_message"))
    if _words(fm) > FIRST_WORDS:
        fm = " ".join(fm.split()[:FIRST_WORDS]).rstrip(",;:") + "…"
    out["first_message"] = fm or f"let's work on this: {title}."
    return out, probs


def validate_line(line):
    line = _tidy(line)
    if not line or _words(line) > LINE_WORDS or PATH_RE.search(line) or ID_RE.search(line):
        return None
    return line


# ---------- cache file ----------
_mem = {"mtime": None, "data": None}
_mem_lock = threading.Lock()


def _empty():
    return {"version": 1, "updated": None, "items": {}, "briefing": None, "last_error": None}


def _sig(p):
    st = os.stat(p)
    return (p, st.st_ino, st.st_mtime_ns, st.st_size)


def load():
    """Cached by (inode, mtime, size): the warm board path pays one stat()."""
    p = path()
    try:
        mt = _sig(p)
    except OSError:
        return _empty()
    with _mem_lock:
        if _mem["mtime"] == mt and _mem["data"] is not None:
            return _mem["data"]
    data = util.read_json(p, None)
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        data = _empty()
    with _mem_lock:
        _mem["mtime"], _mem["data"] = mt, data
    return data


def save(data):
    data["updated"] = util.iso(time.time())
    util.write_json_atomic(path(), data)
    with _mem_lock:          # what we just wrote is the truth, whatever the clock granularity
        try:
            _mem["mtime"], _mem["data"] = _sig(path()), data
        except OSError:
            _mem["mtime"], _mem["data"] = None, None


def _update(fn):
    """Read-modify-write under the in-process lock (the file lock guards other processes)."""
    with _write_lock:
        data = json.loads(json.dumps(load()))   # private copy
        fn(data)
        save(data)
        return data


_write_lock = threading.Lock()


# ---------- attach (the board path: cache reads only) ----------
def attach(blocks, data=None):
    """Put cached headlines on blocks in place. Returns how many were attached."""
    data = data if data is not None else load()
    items = data.get("items") or {}
    n = 0
    for b in blocks:
        e = items.get(b.get("id"))
        if not e or not e.get("headline") or (b.get("headline") or {}).get("own"):
            continue                 # a block that states its own headline (the inbox's count of people) keeps it
        h = dict(e["headline"])
        h["hash"] = e.get("hash")
        if e.get("hash") != content_hash(b):
            h["stale"] = True
        b["headline"] = h
        n += 1
    return n


def todo(blocks, data=None, force=False):
    """-> target blocks whose headline is missing or stale (loudest first)."""
    data = data if data is not None else load()
    items = data.get("items") or {}
    now = time.time()
    out = []
    for b in targets(blocks):
        e = items.get(b["id"])
        h = content_hash(b)
        if force or not e or e.get("hash") != h:
            out.append(b)
        elif not e.get("headline") and now - (e.get("failed_at") or 0) > RETRY_INVALID_S:
            out.append(b)
    return out


# ---------- the LLM ----------
def api_key():
    return ""


_model_label = None


def model_label():
    global _model_label
    if _model_label is None:
        _model_label = "host"
        try:
            txt = open(CONFIG_PATH, encoding="utf-8").read()
            m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+default:\s*(\S+)", txt, re.M)
            if m:
                _model_label = m.group(1).strip("'\"")
        except OSError:
            pass
    return _model_label


def chat(prompt, timeout=TIMEOUT_S):
    raise RuntimeError("the plugin build has no API-server path; og_core.talk supplies this seam")


def parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, z = t.find("{"), t.rfind("}")
    if a < 0 or z < a:
        raise ValueError("no JSON object in reply")
    return json.loads(t[a:z + 1])


KIND_WORDS = {
    "task": "a to-do on the user's list", "crew": "a crew member's report waiting on the user's verdict",
    "proposal": "something the one guy suggests (the user keeps or dismisses it)",
    "job": "a scheduled background job", "session": "a chat thread with an agent",
    "goal": "one of the user's life goals", "media": "a video clip", "note": "a note",
}
STATUS_WORDS = {"needs-you": "needs the user", "failed": "broken", "proposed": "suggested",
                "active": "in motion", "waiting": "waiting", "idle": "quiet"}


def item_text(b):
    d = b.get("detail") or {}
    k = b.get("kind")
    if k == "task":
        t = d.get("text") or b.get("summary") or b.get("title")
    elif k == "crew":
        t = b["title"] + "\n" + (d.get("body") or "")[:900]
    elif k == "proposal":
        t = f"{b['title']}\n{b.get('summary') or ''}\nwhy: {d.get('why') or ''}"
    elif k == "job":
        t = (f"job '{b['title']}', runs {d.get('schedule')}, last run: {d.get('last_status')}"
             f"\n{d.get('last_output_excerpt') or ''}")
    elif k == "session":
        t = (f"thread '{b['title']}' ({d.get('message_count')} messages)\nThe user last said: {d.get('last_user') or ''}"
             f"\nagent last said: {d.get('last_assistant') or ''}")
    else:
        t = f"{b['title']}\n{b.get('summary') or ''}"
    t = re.sub(r"_Generated by .*", "", t)
    t = re.sub(r"\s*\(from: [^)]*\)", "", t)          # tasklist provenance, not content
    return _clip(t, 900)


def _due_words(b, now):
    ts = util.parse_ts(b.get("due"))
    if ts is None:
        return None
    days = round((ts - now) / 86400)
    if days < 0:
        return f"overdue by {-days} day(s)"
    return "today" if days == 0 else f"in {days} day(s)"


PROMPT = """Do NOT use any tools. Don't look anything up. Just answer from the text below, in one reply.

You are the one guy: the user's single AI companion ("i" in your lines is you; "you" is the user; the crew are agents working for you both). The user opens his home screen and sees a few cards. The raw items below were written by agents and scripts, full of jargon. For each one, write the card face he sees, in your voice.

Voice: a warm, dry, competent friend. Short. lowercase (names of people and products can keep capitals). Plain words a smart person who is NOT an engineer gets in two seconds. No file paths, no ids or hashes, no #tags, no branch names, no config keys, no acronyms he'd have to decode (say "the websites" not "Caddy", "the calendar watcher" not "family-cal-watch", "a backup AI provider" not "fallback_providers"). Model or product names only when picking between them is the point. If the user's own words are already plain, keep them.

For each item:
- title: at most 8 words (aim for 4-6). what it is, plainly. no ending period.
- why: at most 14 words (count them; aim for 8-12). why it matters now, or what happens next. one short line, no padding like "your call".
- ask: ONLY when the raw text itself poses a choice the user has to make between options it names ("stay on X or step up to Y?", "merge, iterate, or drop", "full skip vs partial"). Then {"question": at most 10 words, "options": 2 or 3 of {"label": at most 3 words, "say": the sentence the user would send to pick it}}. Take the options from the text; never invent them, and no filler options like "you decide" or "something else". A plain to-do, a report, a running thread or a broken job is NOT a choice: use null. For a crew report asking for approval, options can be approve / steer / reject only if the text asks for that.
- first_message: one or two sentences you'd say to open a work session on it, starting with what you'd do first ("let's ...").

Two examples (style only, made up):
raw: "Car: state inspection due 2026-11-12, not booked yet (#car) — Mavis or the dealer?"
-> {"title": "book the car inspection", "why": "due in 9 days and nothing's booked yet", "ask": {"question": "where should it go?", "options": [{"label": "mavis", "say": "book the inspection at Mavis"}, {"label": "the dealer", "say": "book the inspection at the dealer"}]}, "first_message": "let's get the inspection booked. i'll check what's open this week."}
raw: "backup-sync cron: last run error: 401 Unauthorized from storage API (token expired?)"
-> {"title": "the nightly backup stopped working", "why": "its login expired, so nothing's been saved since", "ask": null, "first_message": "let's get the backup running again. first i'll check whether the login just needs renewing."}

Reply with ONLY strict JSON, no prose and no code fences:
{"items": [{"ref": "b1", "title": "...", "why": "...", "ask": null, "first_message": "..."}]}

Items (today is %(today)s):
%(items)s
"""


def build_prompt(batch, projects=None, now=None):
    now = now or time.time()
    projects = projects or {}
    rows = []
    for i, b in enumerate(batch, 1):
        row = {"ref": f"b{i}", "kind": KIND_WORDS.get(b.get("kind"), b.get("kind")),
               "status": STATUS_WORDS.get(b.get("status"), b.get("status"))}
        if b.get("project") and projects.get(b["project"]):
            row["project"] = projects[b["project"]]
        due = _due_words(b, now)
        if due:
            row["due"] = due
        if b.get("priority"):
            row["priority"] = f"p{b['priority']}"
        row["text"] = item_text(b)
        rows.append(json.dumps(row, ensure_ascii=False))
    return PROMPT % {"today": time.strftime("%A %b %d", time.localtime(now)), "items": "\n".join(rows)}


def generate(batch, projects=None, llm=None):
    """One LLM call for <= BATCH blocks -> {block id: (headline | None, problems)}."""
    llm = llm or chat
    reply = llm(build_prompt(batch, projects))
    data = parse_json(reply)
    got = {str(it.get("ref")): it for it in data.get("items") or [] if isinstance(it, dict)}
    out = {}
    for i, b in enumerate(batch, 1):
        raw = got.get(f"b{i}")
        out[b["id"]] = validate(raw) if raw else (None, ["missing from reply"])
    return out


LINE_PROMPT = """Do NOT use any tools. Just answer.

You are the one guy, the user's AI companion. The user just opened his home screen. Say your opening line: at most 14 words, lowercase, warm and dry, like a friend who's already looked at everything. Say how many things want him today and give the shape of them (e.g. "three things today. one's a quick yes or no."). Don't list them, don't greet him by name, no exclamation marks.%(broke)s

The things for him today:
%(items)s

Reply with ONLY strict JSON: {"line": "..."}
"""


def line_prompt(picks, broke):
    rows = []
    for b in picks:
        h = b.get("headline") or {}
        ask = h.get("ask")
        rows.append(f"- {h.get('title') or b['title'][:60]}: {h.get('why') or ''}"
                    + (f" (a choice: {' / '.join(o['label'] for o in ask['options'])})" if ask else ""))
    br = ""
    if broke:
        h = broke[0].get("headline") or {}
        br = f"\nAlso, one thing broke: {h.get('title') or broke[0]['title'][:60]}. Mention it briefly at the end."
    return LINE_PROMPT % {"items": "\n".join(rows) or "- nothing", "broke": br}


# ---------- refresh ----------
def refresh(blocks, projects=None, force=False, max_batches=MAX_BATCHES, llm=None, log=None):
    """Write headlines for missing/stale targets, then the briefing line.
    Blocking; call it from a thread or the CLI. Returns a summary dict."""
    from . import briefing
    log = log or (lambda *_: None)
    t0 = time.time()
    summary = {"asked": 0, "written": 0, "invalid": 0, "batches": 0, "errors": [], "line": None}
    pending = todo(blocks, force=force)
    model = model_label()
    batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
    if max_batches:
        batches = batches[:max_batches]
    for batch in batches:
        summary["batches"] += 1
        summary["asked"] += len(batch)
        try:
            res = generate(batch, projects, llm=llm)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            summary["errors"].append(msg)
            log("batch failed:", msg)
            _update(lambda d: d.__setitem__("last_error", {"at": util.iso(time.time()), "error": msg}))
            break
        at = util.iso(time.time())
        byid = {b["id"]: b for b in batch}

        def put(d, res=res, at=at, byid=byid):
            for bid, (h, probs) in res.items():
                e = {"hash": content_hash(byid[bid]), "seen": time.time(), "problems": probs or None}
                if h:
                    h.update(model=model, at=at)
                    e["headline"] = h
                else:
                    prev = (d["items"].get(bid) or {}).get("headline")
                    e["headline"] = prev            # keep an older headline over none
                    e["failed_at"] = time.time()
                d["items"][bid] = e
            d["last_error"] = None
        _update(put)
        for bid, (h, probs) in res.items():
            if h:
                summary["written"] += 1
            else:
                summary["invalid"] += 1
                log("invalid:", bid, probs)

    # seen sweep + prune
    live = {b["id"] for b in blocks}

    def sweep(d):
        now = time.time()
        for bid in list(d["items"]):
            e = d["items"][bid]
            if bid in live:
                e["seen"] = now
            elif now - (e.get("seen") or 0) > PRUNE_S:
                del d["items"][bid]
        d["swept"] = now
    _update(sweep)

    # the briefing line, keyed by what's picked
    work = [dict(b) for b in blocks]
    attach(work)
    picks, broke = briefing.choose(work)
    key = briefing.line_key(picks, broke)
    cur = load().get("briefing") or {}
    if (force or cur.get("key") != key) and (picks or broke):
        try:
            line = validate_line(parse_json((llm or chat)(line_prompt(picks, broke))).get("line"))
            if line:
                rec = {"key": key, "line": line, "model": model, "at": util.iso(time.time())}
                _update(lambda d: d.__setitem__("briefing", rec))
                summary["line"] = line
        except Exception as e:
            summary["errors"].append(f"line: {type(e).__name__}: {e}"[:300])
    summary["took_s"] = round(time.time() - t0, 1)
    return summary


# ---------- background kick (never blocks the board) ----------
_kick = {"inflight": False, "last": 0.0, "err_at": 0.0, "last_summary": None}
_kick_lock = threading.Lock()


def _locked_refresh(blocks, projects, **kw):
    """Cross-process single flight: skip if the CLI (or another server) is refreshing."""
    import fcntl
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    with open(path() + ".lock", "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"skipped": "another refresh holds the lock"}
        try:
            return refresh(blocks, projects, **kw)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def needs_work(blocks, data=None):
    from . import briefing
    data = data if data is not None else load()
    if todo(blocks, data):
        return True
    if time.time() - (data.get("swept") or 0) > SWEEP_S:
        return True
    picks, broke = briefing.choose(blocks)
    return bool(picks or broke) and (data.get("briefing") or {}).get("key") != briefing.line_key(picks, broke)


def kick(board, llm=None, now=None):
    """Start a background refresh if one is due. Returns True if a thread started."""
    if not ENABLED:
        return False
    now = now or time.time()
    blocks = board.get("blocks") or []
    with _kick_lock:
        if _kick["inflight"] or now - _kick["last"] < KICK_MIN_S or now - _kick["err_at"] < ERROR_BACKOFF_S:
            return False
        if not needs_work(blocks):
            return False
        _kick["inflight"], _kick["last"] = True, now
    projects = {p["id"]: p["title"] for p in board.get("projects") or []}

    def run():
        try:
            s = _locked_refresh(blocks, projects, llm=llm)
            _kick["last_summary"] = s
            if s.get("errors"):
                _kick["err_at"] = time.time()
        except Exception as e:  # never let a refresh thread die loudly
            _kick["last_summary"] = {"errors": [f"{type(e).__name__}: {e}"]}
            _kick["err_at"] = time.time()
        finally:
            _kick["inflight"] = False
            _kick["last"] = time.time()
    threading.Thread(target=run, name="headlines-refresh", daemon=True).start()
    return True


def status():
    return {"inflight": _kick["inflight"], "last_kick": util.iso(_kick["last"]) if _kick["last"] else None,
            "last": _kick["last_summary"]}


# ---------- CLI ----------
def _board_for_cli():
    from . import build_board, headlines as pkg_mod
    global ENABLED
    ENABLED = pkg_mod.ENABLED = False  # the CLI refreshes itself, synchronously (as __main__ this
    return build_board()               # module is a second copy; switch off the package's too)


def show(bd):
    for b in bd["blocks"]:
        h = b.get("headline")
        if not h:
            continue
        ask = h.get("ask")
        a = f"{ask['question']} [{' / '.join(o['label'] for o in ask['options'])}]" if ask else "-"
        mark = "★ " if b.get("for_you") else ""
        print(f"{b['attention']:.2f} {mark}{h['title']} | {h['why']} | {a}{'  (stale)' if h.get('stale') else ''}")
    br = bd.get("briefing") or {}
    print("\nbriefing:", br.get("line"), "| for_you:", br.get("for_you"), "| broke:", br.get("broke"))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="warm the one guy's headline cache")
    ap.add_argument("--refresh", action="store_true", help="write missing/stale headlines")
    ap.add_argument("--all", action="store_true", help="with --refresh: regenerate every target")
    ap.add_argument("--show", action="store_true", help="print title | why | ask for the board")
    ap.add_argument("--prompt", action="store_true", help="print the first batch's prompt and exit")
    a = ap.parse_args(argv)
    bd = _board_for_cli()
    projects = {p["id"]: p["title"] for p in bd.get("projects") or []}
    if a.prompt:
        print(build_prompt(todo(bd["blocks"], force=True)[:BATCH], projects))
        return 0
    if a.refresh:
        s = _locked_refresh(bd["blocks"], projects, force=a.all, max_batches=0,
                            log=lambda *x: print(*x, file=sys.stderr))
        print(json.dumps(s, ensure_ascii=False))
        bd = _board_for_cli()
    if a.show or not a.refresh:
        show(bd)
    return 0


if __name__ == "__main__":
    # run the package's own copy (board.headlines), so build_board() and the CLI share one module
    _APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _APP not in sys.path:
        sys.path.insert(0, _APP)
    from board import headlines as _real
    sys.exit(_real.main())
