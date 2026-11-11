"""claim: where on the desk a turn is about to work, and the -ing word for it (frontier 4).

The moment the user sends a line that targets cards, the page frames them with one plain word taken
from his ask ("group the dentist stuff" -> `grouping…`) before any reply. A card someone is
already working on gets the same frame with its job's word.

    verb(text)                              -> "grouping" | … | "working"
    targets(text, blocks, focus, view)      -> [block id]   focused card, else his selection,
                                                            else the cards his words name
    build(text, blocks, focus, view)        -> {"verb", "label", "ids"}
    apply(blocks, live)                     -> sets b["claim"] on cards that are running

THE verb map lives here and nowhere else. Stdlib only, no I/O, no model call.
"""
import re

# ask word -> the word on the frame. Lowercase, to match the surface.
VERBS = {
    "group": "grouping", "cluster": "grouping",
    "tidy": "tidying",
    "summarize": "summarizing", "summarise": "summarizing",
    "schedule": "scheduling", "reschedule": "scheduling",
    "find": "finding",
    "draft": "drafting", "write": "drafting",
    "compare": "comparing",
    "fix": "fixing",
}
DEFAULT = "working"
MAX_IDS = 12


def _forms(w):
    out = {w, w + "s", w + "ing", w + "ed"}
    if w.endswith("e"):
        out |= {w[:-1] + "ing", w + "d"}
    if w.endswith("y"):
        out |= {w[:-1] + "ies", w[:-1] + "ied"}
    return out


_FORM = {f: ing for w, ing in VERBS.items() for f in _forms(w)}
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOP = set("""the a an and or of to in on for with my me it is are be this that these those at by from as into
all any some stuff things thing cards card ones one everything please can you could would will up out about
them its our your what which who how why when where do does did let's lets just now then also""".split())


def _words(s):
    return [re.sub(r"['’]s$", "", w) for w in _WORD.findall(str(s or "").lower().replace("’", "'"))]


def verb(text):
    """The -ing form of the ask's main verb: the first word of his that the map knows."""
    for w in _words(text):
        if w in _FORM:
            return _FORM[w]
    return DEFAULT


def label(v):
    return (v or DEFAULT) + "…"


def _stem(w):
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _tokens(s):
    return {_stem(w) for w in _words(s) if len(w) >= 3 and w not in _STOP and w not in _FORM}


def _face_tokens(b):
    h = b.get("headline") if isinstance(b.get("headline"), dict) else {}
    parts = [b.get("title"), h.get("title"), " ".join(str(t) for t in (b.get("tags") or []))]
    proj = str(b.get("project") or "")
    parts.append(re.sub(r"[-_.:]+", " ", proj.split(":", 1)[-1]))
    return _tokens(" ".join(str(p or "") for p in parts))


def targets(text, blocks=None, focus=None, view=None):
    """Which cards this line is about. The focused card; else what he selected; else the cards
    whose face shares the most words with the ask (the best score only, so one stray word
    doesn't frame half the desk). Nothing named -> [] and no frame."""
    known = {b.get("id") for b in blocks or []}
    if focus and (not known or focus in known):
        return [focus]
    sel = [i for i in ((view or {}).get("selected") or []) if isinstance(i, str) and (not known or i in known)]
    if sel:
        return sel[:MAX_IDS]
    want = _tokens(text)
    if not want:
        return []
    scored = []
    for b in blocks or []:
        if b.get("kind") == "project" or b.get("status") == "done":
            continue
        n = len(want & _face_tokens(b))
        if n:
            scored.append((n, b["id"]))
    if not scored:
        return []
    top = max(n for n, _ in scored)
    return [i for n, i in scored if n == top][:MAX_IDS]


def build(text, blocks=None, focus=None, view=None):
    v = verb(text)
    return {"verb": v, "label": label(v), "ids": targets(text, blocks, focus, view)}


def apply(blocks, live=None):
    """A card that is running carries its job's word: b["claim"] = {"verb", "label"}. The word
    comes from what the work was asked to do (the live event's goal), else the card's own title.
    Never raises; a card that isn't running loses a stale claim."""
    live = live or {}
    for b in blocks or []:
        try:
            ev = live.get(b.get("id")) or {}
            running = (b.get("world") or {}).get("kind") == "running" or b.get("status") == "running"
            if not running:
                b.pop("claim", None)
                continue
            v = verb(ev.get("goal") or ev.get("what") or "")
            if v == DEFAULT:
                v = verb(b.get("title"))
            b["claim"] = {"verb": v, "label": label(v)}
        except Exception:   # noqa: BLE001 - a malformed block never sinks the board
            continue
