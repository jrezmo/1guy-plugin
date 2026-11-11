"""selection: what a lasso means to the server (frontier 1, "lasso to ask").

  build(ids, blocks)   -> None, or what a turn about those cards opens with:
                          {ids, names, title, block, linked}

the user drew a box round some cards and asked one thing about all of them. The ask does not belong in
any one card's thread, so it gets a NEW thread whose first turn names the cards: `block` is that
thread's subject and `linked` are the cards as the thread sees them (id, the words on the card,
status, why), handed to the existing threads path (/api/threads/open `linked`). Fewer than two
cards the board knows is not a selection: one card is just that card's own thread.

The judgment (`select`: block = the first id, target = {"ids": [...]}, note = his words) is the
page's to write, through /api/judgment like every other gesture. Pure, stdlib only, no model call.
"""
import re

MAX_CARDS = 10          # what one thread opening can carry by name; the rest are counted
NAME_CHARS = 28         # a name inside the thread's title
_NUM = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _plain(b):
    h = b.get("headline") if isinstance(b.get("headline"), dict) else {}
    return re.sub(r"\s+", " ", str(h.get("title") or b.get("title") or "")).strip()


def _clip(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1].rsplit(" ", 1)[0].rstrip(",;:-–— ") + "…"


def count(n):
    """'three cards' (his voice counts small numbers in words)."""
    return f"{_NUM[n] if 0 <= n < len(_NUM) else n} card{'' if n == 1 else 's'}"


def build(ids, blocks):
    by_id = {b.get("id"): b for b in blocks or [] if isinstance(b, dict) and b.get("kind") != "project"}
    picked = []
    for i in ids if isinstance(ids, (list, tuple)) else []:
        if isinstance(i, str) and i in by_id and i not in picked:
            picked.append(i)
    if len(picked) < 2:
        return None
    names = [_plain(by_id[i]) or "a card" for i in picked]
    more = len(picked) - 3
    title = _clip(f"{len(picked)} cards: " + " · ".join(_clip(n, NAME_CHARS) for n in names[:3])
                  + (f" · and {more} more" if more > 0 else ""), 110)
    linked = []
    for i, name in list(zip(picked, names))[:MAX_CARDS]:
        b = by_id[i]
        h = b.get("headline") if isinstance(b.get("headline"), dict) else {}
        why = re.sub(r"\s+", " ", str(h.get("why") or b.get("summary") or "")).strip()
        linked.append({"id": i, "title": name, "status": b.get("status") or None,
                       "summary": "" if why.lower() == name.lower() else why})
    left = len(picked) - len(linked)
    summary = (f"The user drew a box round {count(len(picked))} on his desk and is asking about them together. "
               "They are the linked blocks below" + (f" (and {left} more he also picked)" if left > 0 else "")
               + ". Answer about these cards as a set, by the words on them.")
    return {"ids": picked, "names": names, "title": title, "linked": linked,
            "block": {"kind": "talk", "title": title, "summary": summary}}
