"""WS-F work surfaces: one Hermes session per block, carried context, streaming, interrupt.

Every block on the canvas can open its OWN work surface: a Hermes session (created via the
API server's session resource API, so it has a real title and shows in Hermes Desktop's
"API" sidebar section) whose first message carries that block's context. Focus is the prompt.

Upstream (Hermes API server, gateway/platforms/api_server.py):
  POST /api/sessions {title, source}            -> {session:{id,...}}            (_handle_create_session)
  POST /api/sessions/{id}/chat/stream {message} -> SSE: run.started{run_id} ·
       assistant.delta{delta} · tool.started · assistant.completed{content} ·
       run.completed|cancelled|failed · done                                     (_handle_session_chat_stream)
  POST /v1/runs/{run_id}/stop                   -> hard interrupt               (api_server_runs._handle_stop_run)

Why long-poll and not SSE to the browser: serve.py's route registry JSON-encodes whatever a
route returns (send_json), so a route module cannot hold the socket open. Instead a worker
thread per reply consumes the upstream SSE into memory and the browser long-polls
GET /api/surface/poll, which returns the instant new deltas land (a Condition wakes it).
That keeps the first token in the browser within a few ms of the server receiving it.

Routes (all JSON):
  POST /api/surface/open       {block}                      -> {surface_id, session_id|null, title, history, live, desktop}
  POST /api/surface/say        {block_id, text, mode, block?, linked?, board_top?, shown?}
                                                            -> {run_id} | {queued:{id,text}} (+ interrupted for steer)
  GET  /api/surface/poll       ?block_id&run&since&wait     -> {delta, total, done, status, next_run_id, queue, ...}
  POST /api/surface/interrupt  {block_id, shown}            -> {heard, unheard, pieces, complete}
  POST /api/surface/unqueue    {block_id, id}               -> drop a held queue item
  GET  /api/surface/list                                    -> {surfaces:[{block_id, session_id, last_activity, message_count, ...}]}
  POST /api/surface/transcribe {audio_b64, mime}            -> {text, language}  (proxy to the tasklist's whisper)

Queue semantics (server-side, per block): mode=queue while a reply streams holds the text;
when that reply finishes NORMALLY the next held item is sent automatically. A plain interrupt
(stop) holds the queue (the chips stay in the rail until summoned). A steer interrupts,
sends the steer, and the queue drains after the steer's reply. Held items are persisted to
~/.hermes/1guy/surface-queue.json (atomic tmp+rename, 600) on every change and reloaded at
import, so a server restart keeps them (they come back as queue chips on /open and drain after
the block's next normal finish, or when the user taps the chip).
Stdlib only.
"""
import base64
import json
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

HOME_1GUY = os.path.expanduser(os.environ.get("ONEGUY_HOME", "~/.hermes/1guy"))
SURFACES_FILE = os.path.join(HOME_1GUY, "surfaces.json")
QUEUE_FILE = os.path.join(HOME_1GUY, "surface-queue.json")   # held queue items (survive restarts)
BRAIN_DIR = os.path.join(HOME_1GUY, "brain")
STATE_DB = os.path.expanduser(os.environ.get("ONEGUY_STATE_DB", "~/.hermes/state.db"))
HERMES_BASE = os.environ.get("ONEGUY_HERMES_BASE", "http://oneguy.invalid")
HERMES_ENV = os.path.expanduser("the Hermes env file")
TRANSCRIBE_URL = os.environ.get("ONEGUY_TRANSCRIBE_URL", "http://oneguy.invalid/t/transcribe")

CARRIED_TOKEN_CAP = 2400          # ~2.5k tokens; estimated at 4 chars/token
CTX_OPEN = "<<1guy carried context>>"
CTX_CLOSE = "<</1guy carried context>>"
STREAM_TIMEOUT = 600
MAX_TEXT = 4000

STANDING_RULES = (
    "You are the one guy: the user's single companion. This surface is ONE thread about this block "
    "(other blocks have their own threads). Keep state in files and the tasklist, not in chat. "
    "Propose, don't silently change infra. Read AGENTS.md before touching the server. "
    "Talk like a person: short, concrete, a few sentences unless the user asks for more. "
    "The user may cut you off: a line starting 'The user steered' means drop what you were saying and pivot; "
    "'The user queued' is an add-on to handle after the current point."
)

# ------------------------------------------------------------------ redaction
_REDACT = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[redacted-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[redacted-token]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "[redacted-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[redacted-jwt]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-~+/=]{12,}"), r"\1 [redacted]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)(\s*[=:]\s*)[\"']?[^\s\"',;]{6,}"),
     r"\1\2[redacted]"),
]


def redact(text):
    s = str(text or "")
    for rx, rep in _REDACT:
        s = rx.sub(rep, s)
    return s


# ------------------------------------------------------------------ classification
_STEER_LEAD = re.compile(r"^\W*(wait|no+|nope|nah|actually|instead|stop|hold on|hang on|scratch that|forget that|never ?mind|rather)\b", re.I)
_STEER_ANY = re.compile(r"\binstead\b|\bscratch that\b|\bforget (that|it)\b", re.I)
_QUEUE_LEAD = re.compile(r"^\W*(also|and add|and also|btw|by the way|oh,? and|plus|one more( thing)?|add)\b", re.I)
_QUEUE_ANY = re.compile(r"\b(also add|and also|by the way|btw)\b", re.I)


def classify(text, streaming=True):
    """Read HOW the user interrupted. Not streaming → 'say'. Leading words win over anywhere-words."""
    t = (text or "").strip()
    if not streaming or not t:
        return "say"
    if _STEER_LEAD.search(t):
        return "steer"
    if _QUEUE_LEAD.search(t):
        return "queue"
    if _STEER_ANY.search(t):
        return "steer"
    if _QUEUE_ANY.search(t):
        return "queue"
    return "steer"  # talking over him without a by-the-way is a cut-in


# ------------------------------------------------------------------ store (threads.json via app/threads.py)
# Phase 1 (unified audit §4.2): the item -> thread map is ~/.hermes/1guy/threads.json, owned by
# app/threads.py and shared with the tasklist's ⤷/◫ (which reach it over /api/threads/open).
# surfaces.json is retired (the migration folds it in). The runner's per-thread state (turns,
# carried_tokens, board_at, last_activity) lives in each thread's "surface" facet. A record is
# written ONLY once a real session exists: opening a bloom never writes (seam 8).
import sys as _sys
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in _sys.path:
    _sys.path.insert(0, _APP_DIR)
import threads as _threads  # noqa: E402  (app/threads.py)

THREADS_FILE = os.environ.get("ONEGUY_THREADS_FILE") or os.path.join(HOME_1GUY, "threads.json")
TASKLIST_API = os.environ.get("ONEGUY_TASKLIST_API", "http://oneguy.invalid/t")
_store_lock = threading.RLock()


def safe_block_id(bid):
    s = str(bid or "").strip()
    if not s or len(s) > 160 or not re.fullmatch(r"[A-Za-z0-9:_\-.#/@^]+", s):
        raise ValueError("bad block_id")
    return s


def _flat(key, rec, block_id=None):
    """thread record -> the flat shape the runner and routes have always used."""
    if not rec:
        return None
    s = dict(rec.get("surface") or {})
    prim = rec.get("primary") or {}
    out = {"surface_id": key, "key": key, "block_id": block_id or key,
           "session_id": prim.get("session_id"), "engine": prim.get("engine"),
           "opened_from": prim.get("opened_from"), "created": prim.get("at"),
           "title": rec.get("title"), "kind": rec.get("kind"),
           "workspace": rec.get("workspace"), "handoff": rec.get("handoff"),
           "children": rec.get("children") or []}
    out.update(s)
    out.setdefault("turns", 0)
    return out


def load_store(path=None):
    return _threads.load(path or THREADS_FILE)


def get_surface(block_id, path=None):
    with _store_lock:
        key, rec = _threads.resolve(block_id, path or THREADS_FILE)
        return _flat(key, rec, block_id)


def update_surface(block_id, path=None, **fields):
    """Update an existing thread's runner state. With a truthy session_id and no thread yet,
    STARTS the thread (resolve-or-start). session_id=None drops the thread (session gone).
    Never writes a record without a session: returns None instead."""
    path = path or THREADS_FILE
    with _store_lock:
        if "session_id" in fields and not fields["session_id"]:
            _threads.forget(block_id, path)
            return None
        sid = fields.pop("session_id", None)
        key, rec = _threads.resolve(block_id, path)
        if rec is None:
            if not sid:
                return None
            key, rec, _ = _threads.start(
                fields.pop("key", None) or block_id, sid, fields.pop("opened_from", None) or "canvas",
                title=fields.pop("title", None), kind=fields.pop("kind", None),
                aliases=fields.pop("aliases", None) or (), handoff=fields.pop("handoff", None),
                workspace=fields.pop("workspace", None), p=path)
        for k in ("key", "opened_from", "aliases"):
            fields.pop(k, None)
        if fields:
            rec = _threads.update(key, path, **fields) or rec
        return _flat(key, rec, block_id)


# serve.py loads route modules without registering them in sys.modules; hand our live globals
# to app/threads.py so routes/threads.py drives THIS runner (same LIVE runs, queue, ctx).
_threads.RUNNER_GLOBALS = globals()


def tasklist_anchor(task_hash, timeout=4):
    """Ask the tasklist API to anchor a task (D1). -> '^k7f2' or None when unreachable."""
    try:
        req = urllib.request.Request(TASKLIST_API.rstrip("/") + "/anchor", method="POST",
                                     data=json.dumps({"id": task_hash}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            a = (json.load(r) or {}).get("anchor")
        return a if a and re.fullmatch(r"\^[a-z0-9]{4,8}", a) else None
    except Exception:
        return None


def stable_key(block_id, block=None):
    """-> (key, aliases) for a NEW thread. Tasklist items get their D1 anchor first, so the
    thread survives edits; the volatile hash id stays as an alias until the board refreshes."""
    bid = block_id
    if bid.startswith("task:") and not bid.startswith("task:^"):
        anchor = (block or {}).get("anchor") or tasklist_anchor(bid.split(":", 1)[1])
        if anchor:
            return "task:" + anchor, [bid]
    return bid, []


# ------------------------------------------------------------------ state.db (read-only)
def _db():
    return sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=2)


def session_meta(session_id):
    if not session_id:
        return None
    try:
        c = _db()
        try:
            row = c.execute("SELECT id, title, source, message_count, started_at, last_activity_at "
                            "FROM sessions WHERE id=?", (session_id,)).fetchone()
        finally:
            c.close()
    except Exception:
        return None
    if not row:
        return None
    return dict(zip(("id", "title", "source", "message_count", "started_at", "last_activity_at"), row))


def session_exists(session_id):
    """True/False from state.db; None when the DB can't be read (then trust the store)."""
    try:
        c = _db()
        try:
            return c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None
        finally:
            c.close()
    except Exception:
        return None


def _turns(session_id, limit=None, newest=False):
    try:
        c = _db()
        try:
            q = ("SELECT role, content, timestamp FROM messages WHERE session_id=? AND active=1 "
                 "AND role IN ('user','assistant') AND content IS NOT NULL AND TRIM(content) != '' "
                 "ORDER BY id " + ("DESC" if newest else "ASC"))
            args = [session_id]
            if limit:
                q += " LIMIT ?"
                args.append(limit)
            rows = c.execute(q, args).fetchall()
        finally:
            c.close()
    except Exception:
        return []
    return list(reversed(rows)) if newest else rows


_J_PREFIX = re.compile(r"^The user steered \(.*?\):\s*|^The user queued this while you were talking:\s*|^The user says:\s*", re.S)


def unframe_user(content):
    """Turn a persisted user message back into what the user actually said + its tag."""
    s = str(content or "")
    carried = False
    if CTX_OPEN in s and CTX_CLOSE in s:
        s = s.split(CTX_CLOSE, 1)[1].strip()
        carried = True
    mode = "say"
    if s.startswith("The user steered"):
        mode = "steer"
    elif s.startswith("The user queued"):
        mode = "queue"
    s = _J_PREFIX.sub("", s, count=1).strip()
    return s, mode, carried


def history(session_id, limit=60):
    out = []
    for role, content, ts in _turns(session_id, limit=limit, newest=True):
        if role == "user":
            text, mode, carried = unframe_user(content)
            out.append({"role": "j", "text": redact(text), "mode": mode, "carried": carried, "ts": ts})
        else:
            out.append({"role": "guy", "text": redact(str(content).strip()), "ts": ts})
    return out


def catchup_excerpt(session_id, max_turns=6, max_chars=300):
    """Like the tasklist's _catchup_excerpt: last user/assistant turns as prose, redacted."""
    parts = []
    for role, content, _ in _turns(session_id, limit=max_turns, newest=True):
        text = re.sub(r"\s+", " ", redact(content)).strip()
        if CTX_CLOSE in text:
            text = text.split(CTX_CLOSE, 1)[1].strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        parts.append(f"{role}: {text}")
    return "\n".join(parts)


# ------------------------------------------------------------------ brain matching
_STOP = set("the a an and or of to in on for with is are be this that it at by from as into my j's j".split())


def _words(s):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", str(s).lower()) if w not in _STOP}


def brain_sections(block, brain_dir=None, max_sections=3, max_chars=500, budget=1400):
    """The matching sections of ~/.hermes/1guy/brain/*.md (heading-split, keyword overlap)."""
    brain_dir = brain_dir or BRAIN_DIR
    keys = _words(" ".join([str(block.get("title") or ""), " ".join(block.get("tags") or []),
                            str(block.get("project") or "").replace("project:", ""),
                            str(block.get("summary") or "")]))
    if not keys or not os.path.isdir(brain_dir):
        return []
    scored = []
    for name in sorted(os.listdir(brain_dir)):
        if not name.endswith(".md"):
            continue
        try:
            text = open(os.path.join(brain_dir, name), encoding="utf-8").read()
        except OSError:
            continue
        chunks = re.split(r"(?m)^(?=#{1,3} )", text)
        for ch in chunks:
            ch = ch.strip()
            if not ch:
                continue
            head = ch.splitlines()[0]
            hw, bw = _words(head), _words(ch)
            score = 3 * len(keys & hw) + len(keys & bw)
            if score >= 2:
                scored.append((score, name, ch))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for score, name, ch in scored[:max_sections]:
        ch = redact(ch)
        if len(ch) > max_chars:
            ch = ch[: max_chars - 1] + "…"
        if used + len(ch) > budget:
            break
        out.append(f"[{name}] {ch}")
        used += len(ch)
    return out


# ------------------------------------------------------------------ carried context
def est_tokens(s):
    return (len(s) + 3) // 4


def _clip(s, n):
    s = re.sub(r"[ \t]+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _detail_lines(detail, budget=1400):
    lines = []
    for k, v in (detail or {}).items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        lines.append(f"- {k}: {_clip(redact(v), 400)}")
    out = "\n".join(lines)
    return _clip(out, budget) if len(out) > budget else out


def _board_lines(board_top):
    bl = []
    for b in (board_top or [])[:16]:
        if not isinstance(b, dict):
            continue
        if b.get("kind") == "projects":
            bl.append(f"- active projects: {_clip(redact(b.get('title')), 300)}")
        else:
            kind = f" ({b['kind']})" if b.get("kind") else ""
            bl.append(f"- {b.get('id')}{kind}: {_clip(redact(b.get('title')), 80)}"
                      f" [{b.get('status') or '?'}]")
    return "\n".join(bl)


BOARD_REFRESH_S = 1800


def board_refresh(rec, board_top, now=None):
    """The home surface is long-lived: its first message's board goes stale. Past
    BOARD_REFRESH_S since the last snapshot, the next message carries a fresh one (same
    markers, so history un-framing strips it). Returns the preamble or None."""
    now = time.time() if now is None else now
    if not board_top or now - float(rec.get("board_at") or 0) < BOARD_REFRESH_S:
        return None
    return (CTX_OPEN + "\n## the user's board now (refreshed; it changed since you last saw it)\n"
            + _board_lines(board_top) + "\n" + CTX_CLOSE)


def build_carried_context(block, linked=None, board_top=None, brain_dir=None,
                          cap_tokens=CARRIED_TOKEN_CAP, excerpt_fn=None):
    """The first message's preamble: block, its links, origin turns, brain, rules. Capped."""
    excerpt_fn = excerpt_fn or catchup_excerpt
    bid = block.get("id") or "?"
    head = [f"Block id: {bid} | kind: {block.get('kind') or '?'} | status: {block.get('status') or 'n/a'}"
            + (f" | project: {block.get('project')}" if block.get("project") else ""),
            f"Title: {_clip(redact(block.get('title')), 200)}"]
    _norm = lambda x: re.sub(r"\s+", " ", str(x or "")).strip().lower()
    if block.get("summary") and _norm(block.get("summary")) != _norm(block.get("title")):
        head.append(f"Summary: {_clip(redact(block.get('summary')), 300)}")
    for k in ("tags", "priority", "due", "updated"):
        if block.get(k) not in (None, "", []):
            head.append(f"{k}: {block[k] if not isinstance(block[k], list) else ', '.join(map(str, block[k]))}")
    src = block.get("source") or {}
    if src:
        head.append(f"source: {src.get('type')} {src.get('ref') or ''}".strip())
    head.extend(headline_lines(block))

    sections = [("rules", "## Standing rules\n" + STANDING_RULES, 0),
                ("block", "## The block (this surface's subject)\n" + "\n".join(head), 0)]
    # tasklist blocks repeat the title as summary and detail.text: say it once
    det_src = {k: v for k, v in (block.get("detail") or {}).items()
               if not (k == "text" and _norm(v) in (_norm(block.get("title")), _norm(block.get("summary"))))}
    det = _detail_lines(det_src)
    if det:
        sections.append(("detail", "## Block detail\n" + det, 3))
    if linked:
        ll = [f"- {l.get('id')}: {_clip(redact(l.get('title')), 90)}"
              + (f" [{l.get('status')}]" if l.get("status") else "")
              + (f" — {_clip(redact(l.get('summary')), 140)}" if l.get("summary") else "")
              for l in linked[:10] if isinstance(l, dict)]
        sections.append(("linked", "## Linked blocks (tendrils)\n" + "\n".join(ll), 2))
    if board_top:
        sections.append(("board", "## the user's board right now (top 15 items by attention)\n" + _board_lines(board_top), 1))
    origin_sid = None
    det_d = block.get("detail") or {}
    if block.get("kind") == "task":
        origin_sid = (det_d.get("origin") or {}).get("session_id")
    elif block.get("kind") == "session":
        origin_sid = det_d.get("session_id")
    if origin_sid:
        ex = excerpt_fn(origin_sid)
        if ex:
            sections.append(("origin", f"## Last turns of the origin session {origin_sid}\n" + ex, 4))
    brain = brain_sections(block, brain_dir=brain_dir)
    if brain:
        sections.append(("brain", "## From the user's second brain (~/.hermes/1guy/brain)\n" + "\n".join(brain), 5))

    def render(secs):
        return (CTX_OPEN + "\n" + "\n\n".join(s[1] for s in secs) +
                "\n(Session opened from the 1guy home canvas; the user's messages follow.)\n" + CTX_CLOSE)

    # Over the cap: shrink the most expendable sections first (highest priority number).
    secs = list(sections)
    while est_tokens(render(secs)) > cap_tokens:
        cands = [i for i, s in enumerate(secs) if s[2] > 0]
        if not cands:
            break
        i = max(cands, key=lambda j: (secs[j][2], len(secs[j][1])))
        name, text, pr = secs[i]
        overflow = (est_tokens(render(secs)) - cap_tokens) * 4 + 40
        if len(text) - overflow < 160:
            secs.pop(i)
        else:
            secs[i] = (name, text[: len(text) - overflow].rstrip() + "…", pr)
    out = render(secs)
    if est_tokens(out) > cap_tokens:  # rules + block alone too big: hard clip the block head
        out = out[: cap_tokens * 4 - len(CTX_CLOSE) - 2] + "…\n" + CTX_CLOSE
    return out


def headline_lines(block):
    """The plain framing the user sees on the card (board headline: title/why/ask), so the one guy and
    the user start from the same words. Part of the block head: never trimmed."""
    h = block.get("headline")
    if not isinstance(h, dict) or not (h.get("title") or h.get("why")):
        return []
    out = []
    if h.get("title"):
        out.append(f"What the user sees on the card: {_clip(redact(h['title']), 120)}")
    if h.get("why"):
        out.append(f"Why it matters (as shown to the user): {_clip(redact(h['why']), 200)}")
    ask = h.get("ask")
    if isinstance(ask, dict) and ask.get("question"):
        opts = [str(o.get("label")) for o in (ask.get("options") or []) if isinstance(o, dict) and o.get("label")]
        out.append(f"The choice the user was offered: {_clip(redact(ask['question']), 160)}"
                   + (f" (options: {' / '.join(_clip(redact(o), 40) for o in opts[:4])})" if opts else ""))
    out.append("Use the same plain words with the user; no ids or paths unless he asks.")
    return out


# ------------------------------------------------------------------ the view (what the user is looking at)
_TITLES = None      # test seam: callable(ids) -> {id: title}; None = ask the board


def _view_titles(ids):
    """Plain titles (the words on the card) for the few ids a view line names."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    if _TITLES is not None:
        return _TITLES(ids)
    try:
        import board as _board                    # app/board (cached sources)
        out = {}
        for b in _board.build_board().get("blocks") or []:
            if b.get("id") in ids:
                h = b.get("headline") if isinstance(b.get("headline"), dict) else {}
                out[b["id"]] = redact(h.get("title") or b.get("title") or "")
        return out
    except Exception:
        return {}


def view_preamble(bid, view, text, carried=None, block=None):
    """The view as the thread sees it: one plain line (router.view_line), inside the carried-context
    markers so history un-frames it away. Said when it changed since this thread last heard it, or
    when the user's line points at the screen ("these"). -> the new `carried` (or the old one, untouched)."""
    if not view:
        return carried
    try:
        import router as _router                  # app/router.py
        ref = _router.refers(text, view)
        foc = view.get("focused")
        titles = _view_titles([foc] + (ref["ids"][:8] if ref else []))
        if foc and not titles.get(foc) and block:
            # the focused card is this thread's own card under another id (a task's key changes when
            # its thread opens): the thread map knows both, the block knows its name
            same = foc in (bid, block.get("id")) or \
                _threads.resolve(foc, THREADS_FILE)[0] == _threads.resolve(bid, THREADS_FILE)[0]
            h = block.get("headline") if isinstance(block.get("headline"), dict) else {}
            if same and (h.get("title") or block.get("title")):
                titles[foc] = redact(h.get("title") or block.get("title"))
        line = _router.view_line(view, titles, text)
    except Exception:
        return carried
    if not line:
        return carried
    head = line.split("\n", 1)[0]
    if LIVE.view_said.get(bid) == head and head == line and not carried:
        return carried                            # nothing moved and he isn't pointing: don't repeat it
    LIVE.view_said[bid] = head
    if carried and CTX_CLOSE in carried:
        return carried.replace(CTX_CLOSE, line + "\n" + CTX_CLOSE, 1)
    return CTX_OPEN + "\n" + line + "\n" + CTX_CLOSE


def frame_message(text, mode, carried=None, heard_tail=""):
    if mode == "steer":
        tail = _clip(heard_tail, 140)
        body = f"The user steered (he cut you off; he'd heard up to “…{tail}”): {text}" if tail else \
            f"The user steered (he cut you off): {text}"
    elif mode == "queue":
        body = f"The user queued this while you were talking: {text}"
    else:
        body = text
    return (carried + "\n\nThe user says: " + body) if carried else body


def surface_title(block):
    if (block.get("id") or "") == "home":
        return "1guy · home"
    t = re.sub(r"\s+", " ", str(block.get("title") or block.get("id") or "block")).strip()
    t = re.sub(r"^(?:1guy\s*·\s*[\w-]+:\s*)+", "", t) or t      # a talk card's title IS a session title: never "1guy · talk: 1guy · talk: …"
    return _clip(f"1guy · {block.get('kind') or 'block'}: {t}", 90)


# ------------------------------------------------------------------ unheard split
def _label(s, n=6):
    w = re.sub(r"[^\w\s'’-]", " ", s).split()
    return " ".join(w[:n]).lower()


CHIP_LABEL_MAX = 72


def _chip_label(s, cap=CHIP_LABEL_MAX):
    """The real sentence, as written, cut at a word boundary (never a lowercased word mash)."""
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= cap:
        return s
    cut = s[:cap - 1].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return (cut or s[:cap - 1]) + "…"


def split_unheard(text, shown, complete=True, max_pieces=3):
    """Split the reply at the interrupt: heard (shown) vs what didn't land.

    One cut = at most ONE parked piece (one rail chip). Its label is the real first sentence
    the user didn't get: the next whole sentence, or, when the cut lands mid-sentence with more than a
    stub left, that sentence in full. When nothing past the cut was generated yet, the label is
    the sentence he was hearing ("…") and the piece is marked ungenerated."""
    shown = max(0, min(int(shown or 0), len(text)))
    heard, rest = text[:shown], text[shown:].strip()
    pieces = []
    mid_sentence = bool(heard.strip()) and not re.search(r"[.!?:;]\s*$", heard)
    heard_tail = re.split(r"(?<=[.!?;:])\s+|\n+", heard.strip())[-1].strip() if mid_sentence else ""
    if rest:
        sents = [s.strip() for s in re.split(r"(?<=[.!?;:])\s+|\n+|(?=\b(?:second|third|then|finally|also),)", rest) if s and s.strip()]
        first, body = None, rest
        if sents:
            if mid_sentence and len(sents[0].split()) < 4:
                # a stub tail of the sentence the user was hearing ("each.") is no chip: take the next one
                first = sents[1] if len(sents) > 1 else None
                body = rest[rest.find(first):] if first else ""
            elif mid_sentence and heard_tail:
                first = heard_tail + " " + sents[0]   # the cut-off sentence, whole
                body = heard_tail + " " + rest
            else:
                first = sents[0]
        if first and not complete and first == sents[-1] and not re.search(r"[.!?:;]\s*$", rest) \
                and len(first.split()) < 4:
            first = None   # a stub still being generated ("Then") is no sentence yet
        if first and len(_label(first)) >= 2:
            lab = _chip_label(first)
            if not complete and first == sents[-1] and not re.search(r"[.!?:;…]\s*$", lab):
                lab += "…"   # he was still saying it
            pieces.append({"label": lab, "text": _clip(body, 600)})
    if not complete and not pieces:
        # nothing usable past the cut exists yet: name the sentence he was in, in its own words.
        # A stub ("Then") gets the sentence before it so the user can tell where he cut in.
        said = re.split(r"(?<=[.!?;:])\s+|\n+", (heard + rest).strip())
        lab = said[-1].strip() if said else ""
        if lab and len(lab.split()) < 4 and len(said) > 1:
            lab = said[-2].strip() + " " + lab
        if lab and len(lab) > CHIP_LABEL_MAX - 1:
            lab = "…" + lab[-(CHIP_LABEL_MAX - 3):].split(" ", 1)[-1]
        lab = (lab.rstrip(" ,;:-–—") + "…") if lab else "the rest of that answer"
        pieces.append({"label": lab, "text": "", "ungenerated": True})
    return {"heard": heard, "unheard": rest, "pieces": pieces[:1]}


# ------------------------------------------------------------------ Hermes API client
def hermes_key():
    return ""


def _hreq(method, path, body=None, timeout=30):
    raise RuntimeError("the plugin build has no API-server path; og_core.talk supplies this seam")


def create_session(title):
    raise RuntimeError("the plugin build has no API-server path; og_core.talk supplies this seam")


def stop_run(hermes_run_id):
    return {}


# ------------------------------------------------------------------ live runs (in memory)
class Run:
    def __init__(self, block_id, text, mode, queue_id=None):
        self.id = "r_" + uuid.uuid4().hex[:10]
        self.block_id, self.user_text, self.mode, self.queue_id = block_id, text, mode, queue_id
        self.text = ""
        self.done = False
        self.status = "starting"
        self.error = None
        self.tools = []
        self.hermes_run_id = None
        self.resp = None
        self.stopped = None      # None | 'interrupt' | 'steer'
        self.frozen_at = None
        self.t0 = time.time()
        self.t_first = None
        self.t_end = None
        self.next_run_id = None
        self.complete = False
        self.sent = False

    def summary(self, since=0):
        txt = self.text if self.frozen_at is None else self.text[: self.frozen_at]
        since = max(0, min(int(since or 0), len(txt)))
        return {"run_id": self.id, "block_id": self.block_id, "mode": self.mode,
                "user_text": self.user_text, "delta": txt[since:], "since": since, "total": len(txt),
                "done": self.done, "status": self.status, "error": self.error,
                "tools": self.tools[-6:], "interrupted": bool(self.stopped),
                "first_token_s": round(self.t_first - self.t0, 2) if self.t_first else None,
                "elapsed_s": round((self.t_end or time.time()) - self.t0, 2),
                "next_run_id": self.next_run_id, "queue_id": self.queue_id}


class Live:
    def __init__(self):
        self.cond = threading.Condition()
        self.runs = {}        # run_id -> Run
        self.current = {}     # block_id -> run_id (latest)
        self.queue = {}       # block_id -> [{id, text}]
        self.ctx = {}         # block_id -> {block, linked, board_top, view} (last client-sent)
        self.view_said = {}   # block_id -> the view line this thread last heard


LIVE = Live()


# ------------------------------------------------------------------ queue persistence
def load_queue(path=None):
    path = path or QUEUE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        qs = data.get("queues") if isinstance(data, dict) else None
        if isinstance(qs, dict):
            return {str(k): [i for i in v if isinstance(i, dict) and i.get("id") and i.get("text")]
                    for k, v in qs.items() if isinstance(v, list) and v}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def save_queue(path=None):
    """Write LIVE.queue atomically. Call with LIVE.cond held (it guards LIVE.queue)."""
    path = path or QUEUE_FILE
    data = {"version": 1, "updated": time.time(),
            "queues": {k: v for k, v in LIVE.queue.items() if v}}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass  # persistence is best-effort; the in-memory queue still works


def restore_queue(path=None):
    """Load held items from disk into LIVE (at import, i.e. on every server start)."""
    with LIVE.cond:
        LIVE.queue = load_queue(path)
    return LIVE.queue


restore_queue()


def _active(block_id):
    rid = LIVE.current.get(block_id)
    r = LIVE.runs.get(rid) if rid else None
    return r if r and not r.done else None


def _start_run(block_id, text, mode, heard_tail="", queue_id=None, prev=None):
    run = Run(block_id, text, mode, queue_id)
    with LIVE.cond:
        LIVE.runs[run.id] = run
        if prev is not None:
            prev.next_run_id = run.id
        else:
            old = LIVE.runs.get(LIVE.current.get(block_id) or "")
            if old is not None and old.next_run_id is None:
                old.next_run_id = run.id
        LIVE.current[block_id] = run.id
        # keep memory small
        mine = [r for r in LIVE.runs.values() if r.block_id == block_id and r.done]
        for r in sorted(mine, key=lambda r: r.t0)[:-8]:
            LIVE.runs.pop(r.id, None)
    threading.Thread(target=_worker, args=(run, heard_tail), daemon=True).start()
    return run


# One resolve-or-start at a time per item. Two doors can reach "no thread yet" for the same card
# within a second (the talk route opening the thread while the surface sends its first line, or two
# sends racing): each used to create a session, the thread map kept one, and the other was left as an
# unrecorded session nobody could find again (one card once got two sessions a second apart).
_start_locks = {}
_start_guard = threading.Lock()


def start_lock(key):
    with _start_guard:
        return _start_locks.setdefault(str(key), threading.Lock())


def ensure_session(bid, block, opened_from="canvas"):
    """-> (thread record, session id) for `bid`, creating the session at most once."""
    with start_lock(bid):
        rec = get_surface(bid)
        sid = (rec or {}).get("session_id")
        if sid and session_exists(sid) is False:
            # deleted in Desktop (or by a cleanup): start a fresh thread, context and all
            update_surface(bid, session_id=None)
            rec, sid = None, None
        if not sid:
            # resolve-or-start: the thread record is written only once the session exists
            sid, title = create_session(surface_title(block))
            key, aliases = stable_key(bid, block)
            rec = update_surface(bid, session_id=sid, key=key, aliases=aliases, title=title,
                                 kind=block.get("kind"), opened_from=opened_from)
        return rec, sid


def _worker(run, heard_tail):
    bid = run.block_id
    try:
        rec = get_surface(bid)
        ctx = LIVE.ctx.get(bid) or {}
        block = ctx.get("block") or {"id": bid}
        carried = None
        rec, sid = ensure_session(bid, block, ctx.get("opened_from") or "canvas")
        if not rec.get("turns"):
            carried = build_carried_context(block, ctx.get("linked"), ctx.get("board_top"))
            update_surface(bid, carried_tokens=est_tokens(carried),
                           board_at=time.time() if ctx.get("board_top") else None)
        elif bid == "home":
            carried = board_refresh(rec, ctx.get("board_top"))
            if carried:
                update_surface(bid, board_at=time.time())
        carried = view_preamble(bid, ctx.get("view"), run.user_text, carried, block)
        msg = frame_message(run.user_text, run.mode, carried, heard_tail)
        if run.stopped:
            return
        run.sent = True
        resp = _hreq("POST", f"/api/sessions/{sid}/chat/stream", {"message": msg}, timeout=STREAM_TIMEOUT)
        run.resp = resp
        run.status = "streaming"
        ev = None
        for raw in resp:
            if run.stopped:
                break
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:])
            except ValueError:
                continue
            with LIVE.cond:
                if run.stopped:
                    break
                if ev == "run.started":
                    run.hermes_run_id = d.get("run_id")
                elif ev == "assistant.delta" and d.get("delta"):
                    if run.t_first is None:
                        run.t_first = time.time()
                    run.text += d["delta"]
                elif ev in ("tool.started",):
                    run.tools.append(d.get("tool_name") or "tool")
                elif ev == "assistant.completed":
                    content = d.get("content") or ""
                    if content and len(content) > len(run.text):  # non-streamed provider fallback
                        if run.t_first is None:
                            run.t_first = time.time()
                        run.text = content
                    run.complete = bool(d.get("completed", True)) and not d.get("interrupted")
                elif ev and ev.startswith("run."):
                    run.status = ev[4:]
                elif ev == "error":
                    run.error = redact(d.get("message") or "error")[:300]
                LIVE.cond.notify_all()
            if ev == "done":
                break
    except Exception as e:
        if not run.stopped:
            run.error = redact(f"{type(e).__name__}: {e}")[:300]
    finally:
        try:
            if run.resp is not None:
                run.resp.close()
        except Exception:
            pass
        with LIVE.cond:
            run.done = True
            run.t_end = time.time()
            if run.stopped:
                run.status = "interrupted"
            elif run.error:
                run.status = "failed"
            elif run.status in ("starting", "streaming"):
                run.status = "completed"
            LIVE.cond.notify_all()
        if run.sent:
            rec = get_surface(bid) or {}
            update_surface(bid, last_activity=time.time(), turns=int(rec.get("turns") or 0) + 1)
        # Drain one held queue item after a NORMAL finish (a steer sends its own next run).
        if not run.stopped and not run.error:
            with LIVE.cond:
                q = LIVE.queue.get(bid) or []
                item = q.pop(0) if q else None
                if item:
                    save_queue()
            if item:
                _start_run(bid, item["text"], "queue", queue_id=item["id"], prev=run)


def _hard_stop(run, why):
    """Freeze the caption now; stop the upstream run (API stop + drop the SSE socket)."""
    with LIVE.cond:
        if run.done or run.stopped:
            return
        run.stopped = why
        run.frozen_at = len(run.text)
        LIVE.cond.notify_all()
    if run.hermes_run_id:
        stop_run(run.hermes_run_id)
    try:  # unblock the reader thread immediately (disconnect also interrupts upstream)
        sock = run.resp.fp.raw._sock  # type: ignore[union-attr]
        sock.shutdown(socket.SHUT_RDWR)
    except Exception:
        pass


def interrupt(block_id, shown, why="interrupt"):
    run = _active(block_id)
    if run is None:
        rid = LIVE.current.get(block_id)
        last = LIVE.runs.get(rid) if rid else None
        return {"ok": True, "was_streaming": False, "run_id": rid,
                "heard": last.text if last else "", "unheard": "", "pieces": []}
    _hard_stop(run, why)
    text = run.text[: run.frozen_at]
    out = split_unheard(text, shown if shown is not None else len(text), complete=run.complete)
    deadline = time.time() + 8
    with LIVE.cond:
        while not run.done and time.time() < deadline:
            LIVE.cond.wait(0.25)
    out.update({"ok": True, "was_streaming": True, "run_id": run.id, "stopped": run.done,
                "hermes_run_id": run.hermes_run_id})
    return out


# ------------------------------------------------------------------ route handlers
def desktop_affordance(session_id, title):
    if not session_id:
        return None
    return {"session_id": session_id, "title": title,
            "deep_link": f"hermes://open/{session_id}",
            "hint": (f"Hermes Desktop: sidebar section 'API' → '{title}'. The deep link opens it "
                     "when Desktop's active connection is this VPS gateway (that's where the session lives).")}


def r_open(q, body):
    block = (body or {}).get("block") or {}
    if not isinstance(block, dict):
        raise ValueError("need {block:{...}}")
    bid = safe_block_id(block.get("id"))
    if body.get("linked") is not None or body.get("board_top") is not None or block:
        LIVE.ctx[bid] = {"block": block, "linked": body.get("linked"), "board_top": body.get("board_top")}
    rec = get_surface(bid) or {}   # read-only: opening a bloom never writes a thread
    sid = rec.get("session_id")
    meta = session_meta(sid)
    title = (meta or {}).get("title") or rec.get("title") or surface_title(block)
    live = _active(bid)
    with LIVE.cond:
        queue = list(LIVE.queue.get(bid) or [])
    return {"surface_id": rec.get("surface_id"), "thread_key": rec.get("key"), "block_id": bid,
            "session_id": sid, "title": title,
            "history": history(sid) if sid else [], "message_count": (meta or {}).get("message_count", 0),
            "live": live.summary(0) if live else None, "queue": queue,
            "desktop": desktop_affordance(sid, title), "carried_tokens": rec.get("carried_tokens")}


def r_say(q, body):
    body = body or {}
    bid = safe_block_id(body.get("block_id") or (body.get("block") or {}).get("id"))
    text = str(body.get("text") or "").strip()
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f"text must be 1-{MAX_TEXT} chars")
    mode = body.get("mode") or "say"
    if mode not in ("say", "steer", "queue"):
        raise ValueError("mode must be say|steer|queue")
    if isinstance(body.get("block"), dict) or body.get("linked") or body.get("board_top"):
        prev = LIVE.ctx.get(bid) or {}
        LIVE.ctx[bid] = {"block": body.get("block") or prev.get("block") or {"id": bid},
                         "linked": body.get("linked") if body.get("linked") is not None else prev.get("linked"),
                         "board_top": body.get("board_top") if body.get("board_top") is not None else prev.get("board_top")}
    if isinstance(body.get("view"), dict):       # what the user is looking at as he says it (core.js viewContext)
        import router as _router                  # app/router.py
        LIVE.ctx.setdefault(bid, {"block": {"id": bid}})["view"] = _router.clean_view(body["view"])
    active = _active(bid)
    if mode == "queue" and active is None:
        mode = "say"  # nothing to wait behind: just say it
    if mode == "queue" and active is not None:
        item = {"id": "q_" + uuid.uuid4().hex[:8], "text": text, "at": time.time()}
        with LIVE.cond:
            LIVE.queue.setdefault(bid, []).append(item)
            save_queue()
        return {"ok": True, "queued": item, "run_id": active.id}
    interrupted, heard_tail = None, ""
    if active is not None:
        # say or steer over a live reply = a cut-in: stop him first, then pivot.
        interrupted = interrupt(bid, body.get("shown"), why="steer")
        heard_tail = interrupted.get("heard", "")[-160:]
        mode = "steer"
    run = _start_run(bid, text, mode, heard_tail=heard_tail, prev=active)
    return {"ok": True, "run_id": run.id, "mode": mode, "interrupted": interrupted}


def r_poll(q, body):
    bid = safe_block_id(q.get("block_id"))
    rid = q.get("run") or LIVE.current.get(bid)
    since = int(q.get("since") or 0)
    wait = min(max(float(q.get("wait") or 20), 0), 25)
    run = LIVE.runs.get(rid) if rid else None
    if run is None:
        return {"run_id": rid, "missing": True, "done": True, "delta": "", "total": 0,
                "queue": list(LIVE.queue.get(bid) or [])}
    deadline = time.time() + wait
    with LIVE.cond:
        while True:
            cur = len(run.text if run.frozen_at is None else run.text[: run.frozen_at])
            if cur > since or run.done or time.time() >= deadline:
                break
            LIVE.cond.wait(min(1.0, max(0.05, deadline - time.time())))
        out = run.summary(since)
        out["queue"] = list(LIVE.queue.get(bid) or [])
    if run.done and not out.get("next_run_id"):
        # the follow-up queue run may start a hair after done flips; give it a beat
        time.sleep(0.15)
        out["next_run_id"] = run.next_run_id
    rec = get_surface(bid) or {}
    out["session_id"] = rec.get("session_id")
    return out


def r_interrupt(q, body):
    bid = safe_block_id((body or {}).get("block_id"))
    return interrupt(bid, (body or {}).get("shown"))


def r_unqueue(q, body):
    bid = safe_block_id((body or {}).get("block_id"))
    qid = (body or {}).get("id")
    with LIVE.cond:
        before = LIVE.queue.get(bid) or []
        LIVE.queue[bid] = [i for i in before if i["id"] != qid]
        removed = len(before) - len(LIVE.queue[bid])
        if removed:
            save_queue()
    return {"ok": True, "removed": removed}


def r_list(q, body):
    data = load_store()
    out = []
    for bid, trec in data["threads"].items():
        rec = _flat(bid, trec)
        meta = session_meta(rec.get("session_id")) or {}
        out.append({"block_id": bid, "surface_id": rec.get("surface_id"), "session_id": rec.get("session_id"),
                    "title": meta.get("title") or rec.get("title"), "kind": rec.get("kind"),
                    "last_activity": meta.get("last_activity_at") or rec.get("last_activity"),
                    "message_count": meta.get("message_count", 0), "turns": rec.get("turns", 0),
                    "live": bool(_active(bid)), "queued": len(LIVE.queue.get(bid) or []),
                    "desktop": desktop_affordance(rec.get("session_id"), meta.get("title") or rec.get("title"))})
    out.sort(key=lambda s: -(s["last_activity"] or 0))
    return {"surfaces": out}


def r_transcribe(q, body):
    return {"error": "voice input is not part of the plugin yet; type instead"}, 501


ROUTES = {
    ("POST", "/api/surface/open"): r_open,
    ("POST", "/api/surface/say"): r_say,
    ("GET", "/api/surface/poll"): r_poll,
    ("POST", "/api/surface/interrupt"): r_interrupt,
    ("POST", "/api/surface/unqueue"): r_unqueue,
    ("GET", "/api/surface/list"): r_list,
    ("POST", "/api/surface/transcribe"): r_transcribe,
}
