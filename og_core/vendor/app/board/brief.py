"""brief: turn a pile (or a lasso of cards) into one short brief that points back (frontier 6).

    gather(ids, blocks)                    -> [{id, kind, title, why, status, tail}]   bounded, plain text
    prompt(items, name)                    -> the one model prompt
    write(ids, board, pile=, name=, chat=) -> {ok, id, block_id, title, by: "model"|"plain", sources, path}
                                              or {ok: False, error} when none of the ids is a card

What it writes, all under the artifacts store the `artifacts` board source already reads (the
folder artifacts.REGISTRY lives in, i.e. ONEGUY_HOME):
    briefs/<id>.md      front matter (title, created, by, pile, sources: the block ids) + the brief
    artifacts.jsonl     one line: kind "brief", path, `sources`, `pile`
The artifacts source turns that line into a card that links to its sources (the board makes links
two-way, so each source shows the brief in its connections strip) and sits in the pile it was
made from.

The model: ONE call through `chat`, an injectable hook (the plugin points it at ctx.llm; 1guy pins
no model). Left as None it borrows headlines.chat, the hook the plugin already wires. Model off,
down or answering nonsense: the brief is still written, from the words already on the cards, and
says so in its last line (by: "plain"). Never raises on a model failure. Stdlib only.
"""
import os
import re
import time

from . import artifacts, util

MAX_CARDS = 12           # cards one brief reads by name; the rest are counted
TAIL_CHARS = 320         # each piece of a card's thread tail
ITEM_CHARS = 900         # everything one card contributes to the prompt
BRIEF_CHARS = 6000       # the brief itself, as kept and as shown
TIMEOUT_S = 120.0
SECTIONS = ("What this is", "Where each piece stands", "Open questions", "Suggested next step")
KIND_WORD = {"task": "to-do", "session": "chat", "job": "scheduled job", "crew": "crew ask",
             "proposal": "idea", "artifact": "made thing", "talk": "said", "goal": "goal",
             "media": "clip", "event": "event", "note": "note"}
STATUS_WORD = {"needs-you": "waiting on you", "failed": "broke", "active": "moving", "waiting": "waiting",
               "proposed": "proposed", "done": "done", "parked": "put away", "idle": "quiet"}
_TAILS = (("last_user", "you last said"), ("last_assistant", "the agent last said"), ("outcome", "outcome"),
          ("text", "text"), ("body", "body"), ("why", "why"), ("last_output_excerpt", "last run said"))

chat = None              # the plugin sets this (or headlines.chat) to ctx.llm


def home():
    return os.path.dirname(artifacts.REGISTRY)


def briefs_dir():
    return os.path.join(home(), "briefs")


def _one(s, n):
    return util.clip(util.redact(str(s or "")), n)


def gather(ids, blocks):
    """The cards behind `ids`, in his order, as bounded plain text. Unknown ids and project
    tiles are dropped; at most MAX_CARDS come back."""
    by_id = {b.get("id"): b for b in blocks or [] if isinstance(b, dict) and b.get("kind") != "project"}
    out, seen = [], set()
    for i in ids if isinstance(ids, (list, tuple)) else []:
        if not isinstance(i, str) or i in seen or i not in by_id:
            continue
        seen.add(i)
        b = by_id[i]
        h = b.get("headline") if isinstance(b.get("headline"), dict) else {}
        d = b.get("detail") if isinstance(b.get("detail"), dict) else {}
        title = _one(h.get("title") or b.get("title"), 120) or "a card"
        why = _one(h.get("why") or b.get("summary"), 200)
        tail, room = [], ITEM_CHARS
        for key, word in _TAILS:
            t = _one(d.get(key), TAIL_CHARS)
            if not t or t.lower() in (title.lower(), why.lower()) or room < 40:
                continue
            t = util.clip(t, min(TAIL_CHARS, room))
            tail.append(f"{word}: {t}")
            room -= len(t)
        w = b.get("world") if isinstance(b.get("world"), dict) else {}
        out.append({"id": i, "kind": KIND_WORD.get(b.get("kind"), str(b.get("kind") or "card")),
                    "title": title, "why": "" if why.lower() == title.lower() else why,
                    "status": STATUS_WORD.get(b.get("status"), str(b.get("status") or "")),
                    "now": _one(w.get("text"), 120), "tail": tail})
        if len(out) >= MAX_CARDS:
            break
    return out


def known(ids, blocks):
    """Every id of his that is a card on the board (not capped: the brief's sources)."""
    have = {b.get("id") for b in blocks or [] if isinstance(b, dict) and b.get("kind") != "project"}
    return list(dict.fromkeys(i for i in (ids if isinstance(ids, (list, tuple)) else [])
                              if isinstance(i, str) and i in have))


def prompt(items, name=None, more=0):
    lines = []
    for n, it in enumerate(items, 1):
        head = f"{n}. [{it['kind']}, {it['status'] or 'quiet'}] {it['title']}"
        if it["why"]:
            head += f" ({it['why']})"
        lines.append(head)
        if it["now"]:
            lines.append(f"   right now: {it['now']}")
        lines += ["   " + t for t in it["tail"]]
    about = f'the pile called "{name}"' if name else "a set of cards the user picked"
    return (
        f"You are writing a short brief about {about} on the user's desk. "
        f"Below are the {len(items)} cards" + (f" (there are {more} more, not shown)" if more else "")
        + ", each with its kind, where it stands, and the tail of its thread.\n\n"
        + "\n".join(lines)
        + "\n\nWrite the brief in markdown, under 300 words, in plain words a busy person reads in "
        "one minute. Use exactly this shape:\n"
        "# <a title of at most 5 words>\n"
        + "".join(f"## {s}\n<{h}>\n" for s, h in zip(SECTIONS, (
            "two or three sentences", "one bullet per card, by the words on the card",
            "bullets; only real ones found in the cards, or 'none'", "one sentence")))
        + "\nNo file paths, ids or code. Do not invent facts that are not in the cards. "
        "Reply with the markdown only.")


def clean(reply):
    """The model's reply as a brief, or None when it isn't one."""
    t = util.redact(str(reply or "")).strip()
    t = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", t).strip()
    at = t.find("# ")
    if at > 0 and (t[at - 1] == "\n"):
        t = t[at:]                      # words before the title ("Here is the brief:") go
    if len(t) < 40 or "## " not in t:
        return None
    return t[:BRIEF_CHARS].rstrip()


def title_of(md, fallback):
    m = re.search(r"^#\s+(.+)$", md or "", re.M)
    return util.clip(re.sub(r"[*_`#]", "", m.group(1)), 90) if m else fallback


def plain(items, name=None, more=0):
    """No model: the same shape, from the words already on the cards. Says what it is."""
    n = len(items) + more
    what = f"the {name} pile" if name else "the cards you picked"
    asks = [it for it in items if it["status"] in ("waiting on you", "broke")]
    first = (asks or items)[0]
    # a short title: the brief's own card shows it on one line ("loose ends: where it…" was getting cut)
    out = [f"# {util.clip(name, 40) if name else 'what you picked'} · brief", "",
           f"## {SECTIONS[0]}", f"{n} card{'' if n == 1 else 's'} from {what}, as they read right now.", "",
           f"## {SECTIONS[1]}"]
    for it in items:
        line = f"- **{it['title']}** ({it['kind']}, {it['status'] or 'quiet'})"
        if it["now"] or it["why"]:
            line += ": " + (it["now"] or it["why"])
        out.append(line)
    if more:
        out.append(f"- and {more} more")
    out += ["", f"## {SECTIONS[2]}"]
    out += [f"- {it['title']}: {it['status']}" for it in asks] or ["- none on the cards"]
    out += ["", f"## {SECTIONS[3]}", f"Start with {first['title']}.", "",
            "*Put together from the cards as they are. No model wrote this.*"]
    return "\n".join(out)


def _hook(given):
    if given is not None:
        return given
    if chat is not None:
        return chat
    from . import headlines
    return headlines.chat if headlines.ENABLED else None


def _front(meta, sources):
    q = lambda s: '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'   # noqa: E731
    lines = ["---"] + [f"{k}: {q(v)}" for k, v in meta.items() if v]
    lines += ["sources:"] + [f"  - {q(i)}" for i in sources] + ["---", ""]
    return "\n".join(lines)


def body_of(text):
    """A brief file without its front matter."""
    t = str(text or "")
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end >= 0:
            t = t[end + 4:]
    return t.strip()


def read(path):
    """The brief behind an artifact line, for the card: only ever a file in briefs/."""
    try:
        p = os.path.realpath(os.path.expanduser(str(path or "")))
        if os.path.dirname(p) != os.path.realpath(briefs_dir()) or not p.endswith(".md"):
            return ""
        with open(p, encoding="utf-8") as f:
            return body_of(f.read(BRIEF_CHARS * 2))[:BRIEF_CHARS]
    except OSError:
        return ""


def write(ids, board, pile=None, name=None, chat=None, now=None):   # noqa: A002 - the hook is named for what it is
    blocks = (board or {}).get("blocks") or []
    sources = known(ids, blocks)
    items = gather(sources, blocks)
    if not items:
        return {"ok": False, "error": "none of those are cards on the desk"}
    name = util.clip(re.sub(r"\s+", " ", str(name or "")).strip(), 60) or None
    more = len(sources) - len(items)
    md, by = None, "plain"
    hook = _hook(chat)
    if hook is not None:
        try:
            md = clean(hook(prompt(items, name, more)))
            by = "model" if md else by
        except Exception:   # noqa: BLE001 - a model that is off or down still leaves him a brief
            md = None
    if not md:
        md = plain(items, name, more)
    now = now or time.time()
    title = title_of(md, f"{name or 'These cards'}: a brief")
    aid = f"brief-{util.slugify(name or items[0]['title'])[:32]}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}"
    created = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    pile = pile if isinstance(pile, str) and re.match(r"^(project|pile|cluster):[\w.:^-]{1,80}$", pile) else None
    os.makedirs(briefs_dir(), exist_ok=True)
    path = os.path.join(briefs_dir(), aid + ".md")
    n = 2
    while os.path.exists(path):          # two in one second: never overwrite a brief
        path = os.path.join(briefs_dir(), f"{aid}-{n}.md")
        n += 1
    aid = os.path.basename(path)[:-3]
    with open(path, "w", encoding="utf-8") as f:
        f.write(_front({"title": title, "created": created, "by": by, "pile": pile}, sources) + md + "\n")
    count = len(sources)
    artifacts.append({
        "id": aid, "title": title, "kind": "brief", "by": "1guy", "created": created, "path": path,
        "summary": f"a brief of {count} card{'' if count == 1 else 's'}" + (f" from {name}" if name else ""),
        "sources": sources, "pile": pile, "written_by": by})
    return {"ok": True, "id": aid, "block_id": "artifact:" + aid, "title": title, "by": by,
            "sources": sources, "pile": pile, "path": path}
