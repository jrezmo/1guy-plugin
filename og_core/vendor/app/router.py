"""The talk bar's request router.

Runs AFTER talk_resolve says "conversation". One job: decide where this utterance
lives — the focused block's thread, an existing block thread, a new tasklist item
(+ its thread), a brand-new interaction, or (only for genuine small talk) `home`.

    route(text, focus, faces, task_titles, view=None) ->
        {"route": "block"|"new"|"todo"|"home", "key": str|None,
         "title": str, "todo_text": str|None, "refers"?: {"what", "ids"}}

`view` is what the user is looking at when he says it (the client's viewContext(): lens, level,
focused, selected, visible, trail, issues). It is how "this" and "these" get a meaning:
see clean_view / refers / view_line below. No view = the behaviour before it existed.

Order (spec §Router):
1. Deterministic, zero cost: focus present -> block; the utterance names a board
   face through talk_resolve's matcher -> block with that key; a short greeting or
   thanks -> home (explicit small talk only).
2. One cheap LLM classification call through the Hermes API server (model
   `hermes-agent`), strict JSON, prompt includes the current tasklist titles so an
   utterance that continues an existing item routes `block` instead of adding junk.
3. Any LLM error / parse failure / bad key -> fail OPEN to `new` (title = clipped
   utterance). Never falls back to `home`, never runs tools.

Stdlib only. The HTTP and tasklist seams are injectable for tests.
"""
import json
import os
import re
import urllib.error
import urllib.request

import talk_resolve as R                      # app/talk_resolve.py (app/ on sys.path)

HERMES_API = os.environ.get("ONEGUY_HERMES_API", "http://oneguy.invalid")
ENV_PATH = os.path.expanduser("the Hermes env file")
TITLE_MAX = 80

# Greetings / meta about the surface: the ONLY things allowed to land on `home`.
_SMALL_TALK = re.compile(
    r"^(?:hey|hi|hello|yo|hiya|good (?:morning|afternoon|evening)|thanks|thank you|"
    r"what can you do|who are you|what is this(?: screen| page)?|how does this work)[\s!.?]*$", re.I)


def api_key():
    return ""


def clip_title(s, n=TITLE_MAX):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[: n - 1].rstrip() + "…" if len(s) > n else s


def is_small_talk(text):
    return bool(_SMALL_TALK.match(str(text or "").strip()))


# ------------------------------------------------------------------ the view: what the user is looking at
# Every talk turn carries it (frontier-patterns item 5). Ids stay on this side of the wall: the
# thread only ever sees view_line()'s plain words.
VIEW_VISIBLE_MAX = 40
VIEW_ISSUES_MAX = 5
_ISSUE_KINDS = ("clipped", "overlap")
_ID_OK = re.compile(r"^[^\s<>]{1,160}$")


def _ids(v, cap):
    out = []
    for x in (v if isinstance(v, (list, tuple)) else []):
        x = str(x or "").strip()
        if x and _ID_OK.match(x) and x not in out:
            out.append(x)
        if len(out) >= cap:
            break
    return out


def _word(v, default):
    return re.sub(r"[^a-z0-9_-]", "", str(v or "").lower())[:24] or default


def clean_view(view):
    """The client's view object, trimmed to what we trust -> dict, or None when there is none."""
    if not isinstance(view, dict):
        return None
    issues = []
    for it in (view.get("issues") if isinstance(view.get("issues"), list) else []):
        if not isinstance(it, dict) or it.get("kind") not in _ISSUE_KINDS:
            continue
        i = _ids([it.get("id")], 1)
        if not i:
            continue
        rec = dict(id=i[0], kind=it["kind"])
        w = _ids([it.get("with")], 1)
        if w:
            rec["with"] = w[0]
        issues.append(rec)
        if len(issues) >= VIEW_ISSUES_MAX:
            break
    return dict(lens=_word(view.get("lens"), "today"), level=_word(view.get("level"), "blocks"),
                screen=_word(view.get("screen"), ""),
                focused=(_ids([view.get("focused")], 1) or [None])[0],
                selected=_ids(view.get("selected"), VIEW_VISIBLE_MAX),
                visible=_ids(view.get("visible"), VIEW_VISIBLE_MAX),
                trail=_ids(view.get("trail"), 12), issues=issues)


_THESE = re.compile(
    r"\b(?:these|those|all of them|them all|both of them"
    r"|the (?:ones|cards|things|items) (?:on (?:the |my )?screen|here|i(?:'m| am) looking at|i (?:picked|selected|circled))"
    r"|(?:everything|all|what(?:'s| is)) (?:on (?:the |my )?screen|i (?:picked|selected|circled)))\b", re.I)
_THIS = re.compile(
    r"\bthis(?!\s+(?:week|weekend|morning|afternoon|evening|month|year|quarter|time|is|was|"
    r"mon|tue|wed|thu|fri|sat|sun)\w*)\b|\bthat (?:one|card)\b", re.I)


def refers(text, view):
    """What the user's pointing words mean on the screen he has. "these" / "the ones on screen" -> the
    cards he selected when there are any, else the cards on screen; "this" -> the focused card.
    -> {"what": "selected"|"visible"|"focused", "ids": [...]} or None."""
    if not view:
        return None
    text = str(text or "")
    if _THESE.search(text):
        if view.get("selected"):
            return dict(what="selected", ids=list(view["selected"]))
        if view.get("visible"):
            return dict(what="visible", ids=list(view["visible"]))
    if _THIS.search(text) and view.get("focused"):
        return dict(what="focused", ids=[view["focused"]])
    return None


def _count(n, one="card"):
    return f"{n} {one}" if n == 1 else f"{n} {one}s"


def view_line(view, titles=None, text=""):
    """One plain line of the view for the thread, e.g.
        The user is looking at: today lens · focused: Review the sync PR · on screen: 12 cards
    plus, when his line points ("these"), what the pointing means, by name. `titles` maps
    id -> plain title; an id with no title is counted, never printed."""
    if not view:
        return ""
    titles = titles or {}
    name = lambda i: clip_title(titles.get(i) or "", 60)
    parts = [view.get("lens", "today") + " lens"
             + (" (projects)" if view.get("level") == "projects" else "")]
    if view.get("focused"):
        parts.append("focused: " + (name(view["focused"]) or "one card"))
    if view.get("selected"):
        parts.append("selected: " + _count(len(view["selected"])))
    parts.append("on screen: " + _count(len(view.get("visible") or [])))
    line = "The user is looking at: " + " · ".join(parts)
    ref = refers(text, view)
    if ref and ref["what"] != "focused":
        shown = [n for n in (name(i) for i in ref["ids"]) if n][:8]
        more = len(ref["ids"]) - len(shown)
        which = "he selected" if ref["what"] == "selected" else "on his screen"
        if shown:
            line += ("\nBy “these” he means the " + _count(len(ref["ids"])) + " " + which + ": "
                     + " · ".join(shown) + (f" · and {more} more" if more > 0 else ""))
    return line


# ------------------------------------------------------------------ LLM call
PROMPT = """Do NOT use any tools. Don't look anything up. Answer with ONE JSON object and nothing else.

You are the request router for the one guy's home screen. The user just typed a line into the talk bar. Decide where it goes:

- "block": the line is about one of the open items below. Set "key" to that item's id exactly.
- "todo": the line is a new to-do the user wants tracked (not a question, not about an existing item). Set "todo_text" to the to-do, one line, plain words.
- "new": its own new conversation topic (a question, an idea, something to think about). Set "title" to a short plain headline for it.
- "home": ONLY small talk — a greeting, a thanks, or a question about this screen itself. Never for real work.

The line is often part of an item the user already has: if it clearly continues one of the tasklist items or open blocks below, route "block" to it instead of creating a duplicate to-do.

Open blocks (id · title):
{faces}

Current tasklist items (titles only):
{tasks}

Reply exactly: {{"route": "...", "key": "...", "title": "...", "todo_text": "..."}}
(Omit "key" unless route is "block"; omit "todo_text" unless route is "todo"; "title" for "new".)

the user's line: {text}"""


def _llm_classify(text, faces, task_titles, timeout=12.0, post=None):
    """One classification turn -> parsed dict. Raises on any failure."""
    prompt = PROMPT.format(
        faces="\n".join(f"{f.get('id')} · {f.get('title')}" for f in (faces or []) if f.get("id")) or "(none)",
        tasks="\n".join(task_titles or []) or "(none)",
        text=clip_title(text, 400))
    if post is not None:
        data = post(prompt)
        reply = data["choices"][0]["message"]["content"]
    else:
        key = api_key()
        if not key:
            raise RuntimeError("API_SERVER_KEY not found")
        body = {"model": "hermes-agent", "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(HERMES_API + "/v1/chat/completions",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json",
                                              "X-Hermes-Session-Key": "oneguy-router",
                                              "User-Agent": "1guy-router/1"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
            sid = r.headers.get("X-Hermes-Session-Id")
        if sid:   # scratch classification turn: don't leave it in the user's session list
            try:
                dreq = urllib.request.Request(f"{HERMES_API}/api/sessions/{sid}", method="DELETE",
                                              headers={"Authorization": "Bearer " + key})
                urllib.request.urlopen(dreq, timeout=10).close()
            except (urllib.error.URLError, OSError):
                pass
        reply = data["choices"][0]["message"]["content"]
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (reply or "").strip())
    a, z = t.find("{"), t.rfind("}")
    if a < 0 or z < a:
        raise ValueError("no JSON object in reply")
    return json.loads(t[a:z + 1])


# ------------------------------------------------------------------ the router
_SWITCH = re.compile(r"^\s*(?:no[,.]?\s+|actually[,.]?\s+|ok(?:ay)?[,.]?\s+)?(?:let'?s\s+)?"
                     r"(?:start with|switch to|go to|move (?:on )?to|jump to|do|open|take|work on|look at|focus on)\b"
                     r"|\binstead\s*[.!]?\s*$|\bfirst\s*[.!]?\s*$", re.I)


def is_switch(text):
    """True when the line reads as 'leave this, take that one': it opens with a switch verb or ends
    in 'instead' / 'first'. Only ever used together with a face match, never on its own."""
    return bool(_SWITCH.search(str(text or "")))


def route(text, focus=None, faces=None, task_titles=None, classify=None, view=None):
    """-> routing decision dict. Never raises. `classify` overrides the LLM seam
    (tests): a callable (text, faces, task_titles) -> dict, which may raise.
    `view` (clean_view) gives pointing words a meaning: the decision carries `refers` when the
    line says "this" / "these", and a line that points at exactly ONE card goes to that card."""
    text = str(text or "").strip()
    faces = [f for f in (faces or []) if f.get("id")]
    ref = refers(text, view)
    if ref and not focus and len(ref["ids"]) == 1 and any(f.get("id") == ref["ids"][0] for f in faces):
        focus = ref["ids"][0]
    d = _route(text, focus, faces, task_titles, classify)
    if ref:
        d["refers"] = ref
    return d


def _route(text, focus, faces, task_titles, classify):

    # 1. deterministic: a focused block owns the line — unless the user is plainly steering at ANOTHER card
    #    ("start with the sync retries PR instead", "switch to the plumber one"). A switch cue plus an
    #    unambiguous match on a different board face moves the conversation there; the canvas then
    #    brings that card into focus. Anything less stays with the focused block.
    if focus:
        if is_switch(text):
            it, _c = R.match_items(text, [f for f in faces if f.get("id") != focus])
            if it and it.get("id"):
                return {"route": "block", "key": it["id"], "title": clip_title(text), "todo_text": None,
                        "switched_from": focus}
        return {"route": "block", "key": focus, "title": clip_title(text), "todo_text": None}

    # 2. deterministic: the line names a board face (talk_resolve's own matcher)
    it, _cands = R.match_items(text, faces)
    if it and it.get("id"):
        return {"route": "block", "key": it["id"], "title": clip_title(text), "todo_text": None}

    # 3. deterministic: explicit small talk -> home (the only door to home)
    if is_small_talk(text):
        return {"route": "home", "key": "home", "title": clip_title(text), "todo_text": None}

    # 4. one cheap LLM call; anything unclear is still NEW work, never home
    fn = classify or _llm_classify
    try:
        raw = fn(text, faces, task_titles or [])
        d = raw if isinstance(raw, dict) else {}
        kind = str(d.get("route") or "").strip().lower()
        if kind == "block":
            key = str(d.get("key") or "").strip()
            if key and any(f.get("id") == key for f in faces):
                return {"route": "block", "key": key, "title": clip_title(text), "todo_text": None}
            raise ValueError("block key not on the board")
        if kind == "todo":
            todo = clip_title(d.get("todo_text") or text, 200)
            return {"route": "todo", "key": None, "title": todo, "todo_text": todo}
        if kind == "home":
            return {"route": "home", "key": "home", "title": clip_title(text), "todo_text": None}
        if kind == "new":
            return {"route": "new", "key": None,
                    "title": clip_title(d.get("title") or text), "todo_text": None}
        raise ValueError("unknown route " + kind)
    except Exception:
        return {"route": "new", "key": None, "title": clip_title(text), "todo_text": None}
