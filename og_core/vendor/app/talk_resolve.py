"""The adaptive talk bar's deterministic resolver (Phase 2, unified audit §5.2, A5).

POST /api/talk {text, focus, guess} → a shape-hinted reply, decided WITHOUT a model call:

    {"shape": "landed",        target, op, args, result, undo:{token,label}, summary}
    {"shape": "confirm",       target, token, question}          # risky op: tap-to-confirm on the target
    {"shape": "clarify",       target?, candidates:[{id,title}], question}   # → promote into the column
    {"shape": "conversation",  thread:{key, session_id|null}}    # the column, resolve-or-start client-side
    {"shape": "show",          show:{kind, src, title, why}}     # the stage

Rules (A4/A5): heuristic only, no model call (ONEGUY_CLASSIFIER stays off). When unsure →
conversation (D10). Risky ops (delete, anything touching other people/money, service
restarts) land as tap-to-confirm on the lit target. Every landed change carries an undo
token (5s window, memory-only — the undo is for the eye, not an audit trail) and every
landed change is recorded to ~/.hermes/1guy/talk-history.json (principle 5: files, not chat).

Verb grammar (small, deliberately):
  add/put            → tasklist add (the tasklist quick-add grammar handles #tag/@due/!pN)
  move/reschedule    → edit due (weekday words) or /move (section names)
  put/move/schedule <item> on|for|to <day>[ at <time>|morning|afternoon|evening]
                     → schedule (lane D): the same op:schedule + judgment + undo as a drag on the Time lens
  mark/tick/done     → toggle
  delete/drop/remove → delete (confirm)
  run/restart        → cron job run (confirm: services)
  show/open          → stage (own pages only, deterministic map)
Anything else, or any question, → conversation. A command that names no resolvable target
→ conversation too (the column resolves it better than a wrong action ever could).
Stdlib only.
"""
import json
import os
import re
import time
import uuid

# ------------------------------------------------------------------ vocabulary
VERBS = ("add", "put", "move", "reschedule", "push", "schedule", "book", "mark", "tick", "done", "complete",
         "delete", "drop", "remove", "run", "restart", "pause", "show", "open")
RISKY = {"delete", "remove", "drop", "run", "restart", "pause"}
SECTION_WORDS = {"todo": "To-do", "to-do": "To-do", "to dos": "To-do", "to-dos": "To-do",
                 "doing": "Doing", "now": "Doing", "in progress": "Doing",
                 "someday": "Someday", "later": "Later", "someday maybe": "Someday"}
WIDGETS = {"my list": "todo", "the list": "todo", "my to-dos": "todo", "my todo": "todo",
           "the to-do list": "todo", "tasklist": "todo", "my tasklist": "todo",
           "the tasklist": "todo", "to-dos": "todo", "to do list": "todo",
           "my to-do list": "todo", "the list of things": "todo"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SHOW_PAGES = [("run", "/runs", "what's running"), ("runs", "/runs", "what's running"),
              ("news", "/app/news.html", "the morning news"), ("reel", "/reel", "the reel")]
_DROP = re.compile(r"^\W*(please|hey|ok|okay|can you|could you|would you|i want to|i need to)\s+", re.I)
_QUESTION = re.compile(r"\?|^\s*(what|why|how|when|where|who|should|could|can|is|are|do|does|did|tell me|let'?s|help me|what do you think)\b", re.I | re.M)
_POLITE = re.compile(r"^(hey|ok|okay|so|um|uh)[, ]+", re.I)

_HISTORY_MAX = 300


def history_path(home=None):
    return os.path.join(home or os.path.expanduser(os.environ.get("ONEGUY_HOME", "~/.hermes/1guy")),
                        "talk-history.json")


def record_history(entry, home=None):
    try:
        p = history_path(home)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            data = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        if isinstance(data, list):
            data.append(entry)
            json.dump(data[-_HISTORY_MAX:], open(p, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except OSError:
        pass  # history is best-effort; the change itself already happened


# ------------------------------------------------------------------ text helpers
def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


_STOP = set("the a an and or of to in on for with my me it is are be this that at by from as into j's j".split())


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", _norm(s)) if w not in _STOP}


def parse_line(text):
    """-> {op, body, ok}. The op is the leading verb, body is the rest."""
    t = _DROP.sub("", _norm(text))
    t = _POLITE.sub("", t).strip()
    m = re.match(r"^([a-z]+)\b[,: ]*\s*(.*)$", t)
    if not m:
        return {"op": None, "body": t, "ok": False}
    op, body = m.group(1), m.group(2).strip()
    if op == "mark" and body.startswith("done"):
        op, body = "done", body[4:].strip()
    if op in ("complete", "finish") and op not in VERBS:
        op = "done"
    return {"op": op if op in VERBS else None, "body": body, "ok": True}


# ------------------------------------------------------------------ targets
def match_items(body, items, min_score=1):
    """Fuzzy match body against item faces. -> (exact|None, candidates). A single best
    (score strictly above the rest) is exact; a tie at the top is ambiguous."""
    bt = _tokens(body)
    scored = []
    for it in items or []:
        title = _norm(it.get("title"))
        faces = {title} | {_norm((it.get("headline") or {}).get("title"))}
        faces.discard("")
        s = 0
        if body and (body in title or title in body):
            s = 4 + min(len(body), len(title)) // 8
        else:
            for f in faces:
                ft = _tokens(f)
                if ft and bt and bt <= ft:
                    s = max(s, 3)
                ov = len(bt & ft)
                if ov:
                    s = max(s, 1 + (2 if len(bt) and ov >= max(1, len(bt) - 1) else 0))
        if s >= min_score:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return None, []
    top = scored[0][0]
    cands = [it for s, it in scored if s >= top and s > top - 2]
    if len(cands) == 1:
        return cands[0], cands
    return None, cands[:4]


def match_widget(body):
    b = _norm(body)
    if not b:
        return None
    if b in WIDGETS:
        return WIDGETS[b]
    for w in WIDGETS:
        if w in b:
            return WIDGETS[w]
    return None


def _next_weekday(word, today=None):
    import datetime
    today = today or datetime.date.today()
    idx = WEEKDAYS.index(word)
    d = today + datetime.timedelta(days=(idx - today.weekday()) % 7 or 7)
    return d.isoformat()


def match_destination(dest, today=None):
    """'to <friday|to-do|doing|...>' -> {due: ...} | {section: ...} | None"""
    d = _norm(dest).removeprefix("to ").strip()
    if not d:
        return None
    w = d.split()[0]
    if w in WEEKDAYS or (len(w) > 3 and any(x.startswith(w) for x in WEEKDAYS)):
        full = next((x for x in WEEKDAYS if x.startswith(w)), None)
        return {"due": _next_weekday(full, today)}
    if d in SECTION_WORDS:
        return {"section": SECTION_WORDS[d]}
    if w in ("today",):
        import datetime
        return {"due": (today or datetime.date.today()).isoformat()}
    if w in ("tomorrow",):
        import datetime
        return {"due": ((today or datetime.date.today()) + datetime.timedelta(days=1)).isoformat()}
    return None


# ------------------------------------------------------------------ slots: "thursday afternoon", "tomorrow at 3pm"
_DAYW = r"(?:(?:this|next)\s+)?(?:monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun|today|tonight|tomorrow|tmrw|next week)"
_TIMEW = r"(?:\s+(?:at\s+)?(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?|noon|midday|morning|afternoon|evening|night))"
SLOT_RE = re.compile(r"\s+(?:to|for|on|at)\s+(" + _DAYW + _TIMEW + r"?|" + r"(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)" + r")\s*$")
_PART = {"morning": "09:00", "noon": "12:00", "midday": "12:00", "afternoon": "14:00", "evening": "18:00", "night": "20:00", "tonight": "20:00"}


def match_slot(phrase, today=None, now_hm=None):
    """'thursday afternoon' -> '2026-11-12T14:00'; 'tomorrow' -> '2026-11-11'; '3pm' -> today 15:00.
    None if it isn't a slot. Bare hours 1-6 read as pm (nobody books 3 in the morning by talking)."""
    import datetime
    today = today or datetime.date.today()
    p = _norm(phrase)
    if not p:
        return None
    day, hm = None, None
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", p)
    if m and (m.group(3) or "at " in p or ":" in (m.group(0) or "")):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and h < 12:
            h += 12
        elif m.group(3) == "am" and h == 12:
            h = 0
        elif not m.group(3) and 1 <= h <= 6:
            h += 12
        if h > 23 or mi > 59:
            return None
        hm = f"{h:02d}:{mi:02d}"
    for w, t in _PART.items():
        if re.search(r"\b" + w + r"\b", p) and not hm:
            hm = t
    words = p.replace("next week", "nextweek").split()
    for w in words:
        if w in ("today", "tonight"):
            day = today
        elif w in ("tomorrow", "tmrw"):
            day = today + datetime.timedelta(days=1)
        elif w == "nextweek":
            day = today + datetime.timedelta(days=7)
        else:
            full = next((x for x in WEEKDAYS if len(w) >= 3 and x.startswith(w)), None)
            if full:
                day = datetime.date.fromisoformat(_next_weekday(full, today)) if full != WEEKDAYS[today.weekday()] or "next" in words else today
        if day:
            break
    if day is None and hm:
        day = today
    if day is None:
        return None
    return day.isoformat() + (f"T{hm}" if hm else "")


def split_slot(body, today=None):
    """'the dentist on thursday afternoon' -> ('the dentist', '2026-11-12T14:00') | (body, None)"""
    m = SLOT_RE.search(" " + body)
    if not m:
        return body, None
    slot = match_slot(m.group(1), today)
    return ((" " + body)[:m.start()].strip(), slot) if slot else (body, None)


# ------------------------------------------------------------------ the resolver
def resolve(text, focus=None, items=None, jobs=None, today=None):
    """-> shape dict (never raises). `items` are the board's item faces
    ({id, key?, title, kind, headline}); `jobs` the cron job list."""
    parsed = parse_line(text)
    body = parsed["body"]
    if _QUESTION.search(text) or not parsed["ok"] or not parsed["op"]:
        return {"shape": "conversation", "reason": "question" if _QUESTION.search(text) else "no-op"}
    op, body = parsed["op"], body

    # ---- show me (the stage; own pages only, deterministic)
    if op in ("show", "open"):
        b = _norm(body)
        if focus:
            return {"shape": "conversation", "reason": "about-focus"}
        for word, src, title in SHOW_PAGES:
            if word in b or (op == "show" and not b):
                return {"shape": "show", "show": {"kind": "url", "src": src, "title": title,
                                                  "why": "you asked to see it"}}
        return {"shape": "conversation", "reason": "show-what"}

    # ---- run / restart: cron jobs only (guardrail: no service restarts from 1guy)
    if op in ("run", "restart", "pause"):
        name = _norm(body)
        hit = None
        for j in jobs or []:
            if name and (name in _norm(j.get("name") or "") or name in _norm(j.get("id") or "")):
                hit = j
                break
        if not hit:
            return {"shape": "conversation", "reason": "which-job"}
        return {"shape": "confirm", "op": "job", "target": {"id": "job:" + hit["id"], "title": hit.get("name") or hit["id"]},
                "args": {"job_id": hit["id"]}, "token": "", "question": f"run “{hit.get('name') or hit['id']}” now?"}

    # ---- a slot named for an existing item = schedule it (lane D: same op as a drag on the Time lens)
    if op in ("put", "move", "reschedule", "push", "schedule", "book"):
        what, slot = split_slot(body, today)
        if slot and what and not match_widget(what):
            it, cands = match_items(what, [x for x in items or [] if x.get("kind") == "task"])
            if it is None and cands:
                return {"shape": "clarify", "target": cands[0], "candidates": cands,
                        "question": "which one? " + " · ".join(_norm(c.get("title"))[:28] for c in cands)}
            if it is not None:
                return {"shape": "landed-pending", "op": "task", "target": {"id": it["id"], "title": _norm(it.get("title"))},
                        "args": {"op": "schedule", "id": it["id"], "due": slot}}
        if op in ("schedule", "book"):
            return {"shape": "conversation", "reason": "schedule-what"}

    # ---- add: the list widget is the default target
    if op in ("add", "put"):
        if match_widget(body):
            payload = re.sub(r"\s*(?:to|into|on)\s+(?:my |the )?(?:list|tasklist|to-?dos?|to-?do list)\b\s*", " ", body).strip(" ,")
            if not payload:
                return {"shape": "clarify", "question": "add what, exactly?"}
            return {"shape": "landed-pending", "op": "task", "target": {"id": "widget:todo", "title": "my list"},
                    "args": {"op": "add", "text": payload}}
        it, cands = match_items(re.sub(r"\s*(to|into|on)\s+(my |the )?(list|tasklist|to-?dos?)\s*$", "", body), items)
        if it:
            return {"shape": "clarify", "target": it, "candidates": cands,
                    "question": f"add what to “{_norm(it.get('title'))}”? say it in the column."}
        return {"shape": "conversation", "reason": "add-what"}

    # ---- every other op needs a resolvable item target
    stripped = re.sub(r"\s+(to|for)\s+(friday|monday|tuesday|wednesday|thursday|saturday|sunday|today|tomorrow|next week)\s*$", "", body).strip()
    dest = body[len(stripped):].strip()
    if op in ("done", "tick", "mark"):
        stripped, dest = body, ""
    widget = match_widget(stripped)
    if widget == "todo" and op in ("done", "tick"):
        return {"shape": "clarify", "question": "tick which one? name it."}
    if widget:
        return {"shape": "conversation", "reason": "widget-op"}
    it, cands = match_items(stripped, items)
    if it is None and focus:
        it = next((x for x in (items or []) if x.get("id") == focus), None)
        if it is None:
            return {"shape": "conversation", "reason": "no-target"}
        cands = [it]
    if not cands:
        return {"shape": "conversation", "reason": "no-target"}
    if it is None:
        return {"shape": "clarify", "target": cands[0], "candidates": cands,
                "question": "which one? " + " · ".join(_norm(c.get("title"))[:28] for c in cands)}
    # the list's verbs (tick, delete, move) only mean something on a to-do. Said on a chat, a routine or anything
    # else ("finish this" on an open chat card), it is a hand-off to him: a conversation, never a list write that
    # fails with "need a tasklist item id".
    if not str(it.get("id") or "").startswith("task:"):
        return {"shape": "conversation", "reason": "not-a-task"}
    args = {"op": {"done": "toggle", "tick": "toggle", "mark": "toggle", "delete": "delete",
                   "remove": "delete", "drop": "delete", "move": "edit", "reschedule": "edit",
                   "push": "edit"}.get(op, op), "id": it["id"].replace("task:", "")}
    if args["op"] == "edit":
        d = match_destination(dest, today) or match_destination(body, today)
        if not d:
            return {"shape": "clarify", "target": it, "candidates": cands,
                    "question": "move it where? a day, or to-do / doing / someday."}
        args.update(d)
    return {"shape": "landed-pending", "op": "task", "target": {"id": it["id"], "title": _norm(it.get("title"))},
            "args": args}


RISKY_OPS = {"delete", "job"}
