"""outcomes: what a quiet session got done, wrapped once and kept briefly.

A session the user started (talk-bar ask, or a thread linked to a tasklist item) used
to just decay off the board. Now, once it has been idle for more than
WRAP_AFTER_S (2h), the board generates ONCE a one-line outcome ("looked into
the mac pings — flood silenced, handoff running") from its last few turns,
through the Hermes API server (cheap glm turn, like headlines). The line is
cached in ~/.hermes/1guy/outcomes.json keyed by session id (atomic tmp+rename):

    {"version": 1, "outcomes": {"<session_id>": {"outcome": "...", "at": iso,
                                                 "model": "..."} },
     "failed": {"<session_id>": {"at": epoch}}}

A cached outcome turns the block's status into "wrapped" with the line as its
summary; the block stays on the board for KEEP_S (3 days, rank decays with its
updated ts) and then ages out. Generation never blocks the board: sessions.fetch
attaches from the cache and kicks a single background thread for the rest.

Faces stay honest: the line is validated like a headline (headlines.validate_line
— one short line, no paths, no ids) and the card face still goes through the
headlines.py path for title/why.

The user-initiated sessions (j_initiated()): from threads.json — a `talk:*` key (the user
asked through the talk bar) or a `task:*` key (a thread opened on one of the user's
tasklist items). Stdlib only.
"""
import json
import os
import threading
import time

import threads as T

from . import util
from . import headlines as HL

WRAP_AFTER_S = 2 * 3600     # idle this long -> wrap it once
KEEP_S = 3 * 86400          # a wrapped block stays on the board this long, then ages out
RETRY_S = 30 * 60           # after a failed generation, try again this much later
MAX_TEXT = 1200             # conversation text fed to the LLM

PROMPT = """Do NOT use any tools. Just answer from the text below.

You are the one guy, the user's AI companion. The conversation below just went
quiet. Write ONE line saying what it got done or where it stands — plain words,
no paths, no ids or hashes, no #tags. lowercase, at most 14 words, like:
"looked into the mac pings — flood silenced, handoff running".

Conversation (the user's last line, then your last reply):
%(text)s

Reply with ONLY strict JSON: {"outcome": "..."}"""


# ---------- who the user started ----------
def j_initiated(data=None, p=None):
    """-> {session_id: thread key} for sessions the user initiated (talk:* / task:* keys)."""
    data = data if data is not None else T.load(p)
    out = {}
    for key, rec in (data.get("threads") or {}).items():
        if not isinstance(rec, dict):
            continue
        if not (key.startswith("talk:") or key.split(":", 1)[0] == "task"):
            continue
        for role in ("primary", "children"):
            if role == "primary":
                sids = [(rec.get("primary") or {}).get("session_id")]
            else:
                sids = [c.get("session_id") for c in rec.get("children") or []
                        if isinstance(c, dict)]
            for s in filter(None, sids):
                out.setdefault(s, key)
    return out


# ---------- cache ----------
def path():
    home = os.environ.get("ONEGUY_HOME")
    if home:
        return os.path.join(home, "outcomes.json")
    return util.oneguy("outcomes.json")


def load():
    raw = util.read_json(path(), None)
    if isinstance(raw, dict) and isinstance(raw.get("outcomes"), dict):
        raw.setdefault("failed", {})
        return raw
    return {"version": 1, "outcomes": {}, "failed": {}}


def save(data):
    data["updated"] = util.iso(time.time())
    util.write_json_atomic(path(), data)


# ---------- the line ----------
def text_for(b):
    """The last few turns of the conversation, clipped."""
    d = b.get("detail") or {}
    t = f"The user last said: {d.get('last_user') or '(nothing recorded)'}\n" \
        f"the agent last said: {d.get('last_assistant') or '(nothing recorded)'}"
    return util.clip(util.redact(t), MAX_TEXT)


def make_prompt(b):
    return PROMPT % {"text": text_for(b)}


def generate(b, llm=None):
    """-> the outcome line, or None. One cheap API-server turn, validated like a headline."""
    try:
        reply = (llm or HL.chat)(make_prompt(b))
        line = HL.validate_line(HL.parse_json(reply).get("outcome"))
    except Exception:   # noqa: BLE001 - a failed wrap must never break the board
        return None
    return line


def wrap_block(b, line, model=None, now=None):
    """Turn a session block into its wrapped form (in place)."""
    now = now or time.time()
    b["status"] = "wrapped"
    b["summary"] = line
    d = b.setdefault("detail", {})
    d["outcome"] = line
    d["wrapped_at"] = util.iso(now)
    if model:
        d["wrapped_by"] = model
    return b


# ---------- attach (board path: cache reads only) ----------
def attach_block(b, now=None):
    """-> 'kept' (wrapped from cache), 'drop' (aged out), 'pending' (needs a wrap)
    or None (not wrappable). Never blocks, never raises."""
    try:
        now = now or time.time()
        if b.get("kind") not in ("session", "talk") or b.get("status") not in ("idle", "active"):
            return None
        last = util.parse_ts(b.get("updated"))
        if last is None:
            return None
        age = now - last
        if age <= WRAP_AFTER_S:
            return None     # still warm: leave it alone (active or fresh-idle)
        sid = (b.get("detail") or {}).get("session_id")
        data = load()
        rec = data["outcomes"].get(sid) if sid else None
        if isinstance(rec, dict) and rec.get("outcome"):
            if age > KEEP_S:
                return "drop"       # wrapped long enough: age it out
            return wrap_block(b, rec["outcome"], rec.get("model"), now) and "kept"
        return "pending"
    except Exception:       # noqa: BLE001
        return None


def apply(blocks, now=None):
    """Attach cached outcomes in place. Returns (pending_blocks, dropped_count)."""
    now = now or time.time()
    pending, dropped = [], 0
    for b in blocks or []:
        r = attach_block(b, now)
        if r == "pending":
            pending.append(b)
        elif r == "drop":
            b["_age_out"] = True
            dropped += 1
    return pending, dropped


# ---------- background generation (single flight, like headlines.kick) ----------
_kick = {"inflight": False}
_kick_lock = threading.Lock()


def _generate_pending(pending, llm=None):
    data = load()
    dirty = False
    for b in pending:
        sid = (b.get("detail") or {}).get("session_id")
        if not sid or sid in data["outcomes"]:
            continue
        fail = data["failed"].get(sid)
        if isinstance(fail, dict) and time.time() - (fail.get("at") or 0) < RETRY_S:
            continue
        line = generate(b, llm=llm)
        if line:
            data["outcomes"][sid] = {"outcome": line, "at": util.iso(time.time()),
                                     "model": HL.model_label()}
            data["failed"].pop(sid, None)
        else:
            data["failed"][sid] = {"at": time.time()}
        dirty = True
    if dirty:
        try:
            save(data)
        except OSError:
            pass


def kick(pending, llm=None):
    """Start at most one background wrap for these blocks. Returns True if started."""
    if not pending or os.environ.get("ONEGUY_OUTCOMES", "on").lower() in ("off", "0", "false", "no"):
        return False
    with _kick_lock:
        if _kick["inflight"]:
            return False
        _kick["inflight"] = True

    def run():
        try:
            _generate_pending(pending, llm=llm)
        except Exception:   # noqa: BLE001 - never let the thread die loudly
            pass
        finally:
            _kick["inflight"] = False
    threading.Thread(target=run, name="outcomes-wrap", daemon=True).start()
    return True
