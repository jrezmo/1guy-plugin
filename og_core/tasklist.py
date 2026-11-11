"""The tasklist: one markdown file, `<HERMES_HOME>/tasklist.md`, that every Hermes session can edit.

The file is the source of truth. This module only reads it and flips / adds / edits / moves /
removes single lines under a file lock, leaving everything else byte-for-byte alone. Lines are
`- [ ] text` under `## Section` headings, with optional inline tokens: `#tag`, `@YYYY-MM-DD`,
`!p1`-`!p3`, a stable anchor `^k7f2`, and an origin suffix `(from: label · session-id)`.

Public API (plain functions taking the pydantic bodies below; they raise HTTPException):
  tasks() toggle() add() delete() edit_item() move_item() clear_done() anchor_item()
The board reaches the same functions in-process through `call(route, payload)`.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import os
import re
import sqlite3
import time

from fastapi import HTTPException
from pydantic import BaseModel

from . import store

_ORIGIN_TTL = 30.0
_WS_RE = re.compile(r"\s*\((?:ws|workspace):\s*([^)]+?)\)\s*")
SEED = "# Tasklist\n\n## To-do\n\n## Open questions / issues\n"


def _file() -> str:
    """The tasklist path; created with two empty sections the first time anything asks."""
    p = str(store.tasklist_path())
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with contextlib.suppress(FileExistsError), open(p, "x", encoding="utf-8") as f:
            f.write(SEED)
    return p

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^(\s*)- \[([ xX])\]\s+(.*?)\s*$")
_ORIGIN_RE = re.compile(r"\s*\(from:\s*([^)·]+?)\s*(?:·\s*([0-9A-Za-z_]+))?\)\s*$")
# Metadata tokens (agent-first: plain lines keep working; tokens are optional)
#   #tag            -> tags (case-insensitive, deduped)
#   @2026-10-20     -> due date (optional "THH:MM" or " HH:MM" time)
#   !p1 / !p2 / !p3 -> priority (Todoist-style, 1 highest)
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z0-9][A-Za-z0-9_-]*)")
_DUE_RE = re.compile(r"(?:^|\s)@(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}:\d{2}))?")
_PRIO_RE = re.compile(r"(?:^|\s)!p([123])(?=\s|$)")
# D1 stable anchor: "^k7f2", written once when an item gets a thread (open-session /
# open-workspace / anchor). Survives /edit, /move and hand edits; never shown as text.
_ANCHOR_RE = re.compile(r"(?:^|\s)\^([a-z0-9]{4,8})(?=\s|$)")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# (epoch, frozenset(ids), {source: newest session id}) snapshot of this gateway's sessions
_origin_cache: tuple[float, frozenset, dict] = (0.0, frozenset(), {})


def _local_sessions() -> tuple[frozenset, dict]:
    """Session ids + newest-session-per-source on THIS gateway, cached 30s."""
    global _origin_cache
    now = time.time()
    cached_at, ids, by_source = _origin_cache
    if now - cached_at < _ORIGIN_TTL and ids:
        return ids, by_source
    ids, by_source = frozenset(), {}
    try:
        conn = sqlite3.connect(f"file:{store.state_db()}?mode=ro", uri=True, timeout=2)
        try:
            for sid, source, started in conn.execute(
                "SELECT id, COALESCE(source,''), COALESCE(started_at,0) FROM sessions"
            ):
                ids = ids | {sid}
                if source:
                    prev = by_source.get(source)
                    if prev is None or started > prev[1]:
                        by_source[source] = (sid, started)
        finally:
            conn.close()
        by_source = {k: v[0] for k, v in by_source.items()}
    except Exception:
        pass  # no DB access -> origins stay inert text
    _origin_cache = (now, ids, by_source)
    return ids, by_source


def _resolve_origin(label: str, session_id: str | None) -> dict:
    ids, by_source = _local_sessions()
    if session_id:
        return {"label": label.strip(), "session_id": session_id,
                "local": session_id in ids}
    sid = by_source.get(label.strip().lower())
    if sid:
        return {"label": label.strip(), "session_id": sid, "local": True}
    return {"label": label.strip(), "session_id": None, "local": False}


def _split_origin(raw_text: str) -> tuple[str, dict | None]:
    m = _ORIGIN_RE.search(raw_text)
    if not m:
        return raw_text, None
    label = (m.group(1) or "").strip()
    if not label:
        return raw_text, None
    return raw_text[: m.start()], _resolve_origin(label, m.group(2))


def _split_meta(raw_text: str) -> tuple[str, dict]:
    """Extract #tag / @due / !pN tokens -> (clean display text, meta dict)."""
    meta: dict = {"tags": [], "due": None, "due_time": None, "priority": None, "anchor": None}
    tags: list[str] = []
    for m in _TAG_RE.finditer(raw_text):
        t = m.group(1).lower()
        if t not in tags:
            tags.append(t)
    meta["tags"] = tags
    m = _DUE_RE.search(raw_text)
    if m:
        meta["due"] = m.group(1)
        meta["due_time"] = m.group(2) or None
    m = _PRIO_RE.search(raw_text)
    if m:
        meta["priority"] = int(m.group(1))
    m = _ANCHOR_RE.search(raw_text)
    if m:
        meta["anchor"] = "^" + m.group(1)
    clean = _ANCHOR_RE.sub(" ", raw_text)
    clean = _TAG_RE.sub(" ", clean)
    clean = _DUE_RE.sub(" ", clean)
    clean = _PRIO_RE.sub(" ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean, meta


def _meta_suffix(meta: dict) -> str:
    """Serialize meta back to canonical trailing tokens for the file."""
    parts = []
    if meta.get("tags"):
        parts.append(" " + " ".join("#" + t for t in meta["tags"]))
    if meta.get("due"):
        parts.append(" @" + meta["due"] + ("T" + meta["due_time"] if meta.get("due_time") else ""))
    if meta.get("priority"):
        parts.append(" !p" + str(meta["priority"]))
    if meta.get("anchor"):
        parts.append(" " + meta["anchor"])
    return "".join(parts)


def _normalize_due(val: str) -> tuple[str | None, str | None]:
    """ISO date, ISO datetime, or agent-friendly NL (today/tomorrow/weekday)."""
    v = (val or "").strip()
    if not v:
        return None, None
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{1,2}:\d{2}))?", v)
    if m:
        return m.group(1), m.group(2) or None
    today = datetime.date.today()
    lv = v.lower()
    if lv in ("today", "tod"):
        return today.isoformat(), None
    if lv in ("tomorrow", "tmr", "tmrw"):
        return (today + datetime.timedelta(days=1)).isoformat(), None
    if lv in ("next week", "nextweek"):
        return (today + datetime.timedelta(days=7)).isoformat(), None
    for i, wd in enumerate(_WEEKDAYS):
        if lv == wd or lv == wd[:3]:
            delta = (i - today.weekday()) % 7 or 7
            return (today + datetime.timedelta(days=delta)).isoformat(), None
    raise HTTPException(status_code=400, detail=f"unparseable due date: {val}")


def _clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        tl = str(t).strip().lstrip("#").lower()
        if tl and tl not in out:
            out.append(tl)
    return out


def _check_priority(p: int | None) -> int | None:
    if p is None or p == 0:
        return None
    if p not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="priority must be 1, 2 or 3")
    return p


def _provided_fields(body) -> set:
    """Pydantic v2/v1 compat: names of fields the request actually provided."""
    return getattr(body, "model_fields_set", None) or getattr(body, "__fields_set__", set())


def _item_id(section: str, text: str) -> str:
    return hashlib.sha1(f"{section}\x00{text}".encode()).hexdigest()[:10]


@contextlib.contextmanager
def _locked():
    lock_path = _file() + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read_sections_unlocked() -> list[dict]:
    try:
        with open(_file(), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    sections: list[dict] = []
    cur: dict | None = None
    for raw in lines:
        m = _SECTION_RE.match(raw)
        if m:
            cur = {"name": m.group(1), "items": []}
            sections.append(cur)
            continue
        m = _ITEM_RE.match(raw)
        if m and cur is not None:
            raw_text = m.group(3)
            text, origin = _split_origin(raw_text)
            text, meta = _split_meta(text)
            cur["items"].append(
                {
                    "id": _item_id(cur["name"], raw_text),
                    "text": text.strip(),
                    "done": m.group(2) != " ",
                    "origin": origin,
                    "tags": meta["tags"],
                    "due": meta["due"],
                    "due_time": meta["due_time"],
                    "priority": meta["priority"],
                    "anchor": meta["anchor"],
                }
            )
    return sections


def _write_file_unlocked(text: str) -> None:
    with open(_file(), "w", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


def _updated_at_unlocked() -> float:
    try:
        return os.path.getmtime(_file())
    except OSError:
        return 0.0


def _section_bounds(lines: list[str], section: str) -> tuple:
    """(header_idx, first_item_idx|None, end_of_items_idx|None) for a section
    (case-insensitive name match). header None -> section doesn't exist."""
    sec = section.strip().lower()
    cur = None
    header = None
    lo = None
    hi = -1
    for i, raw in enumerate(lines):
        m = _SECTION_RE.match(raw)
        if m:
            if cur == sec:
                break  # next section begins; target section's items are done
            cur = m.group(1).strip().lower()
            if cur == sec and header is None:
                header = i
            continue
        if cur == sec and _ITEM_RE.match(raw):
            if lo is None:
                lo = i
            hi = i + 1
    if header is None:
        return None, None, None
    return header, lo, (hi if lo is not None else None)


def _insertion_point(
    lines: list[str], header: int, lo: int | None, hi: int | None,
    index: int | None, done: bool,
) -> int:
    """Line index at which a new item line should be inserted. For open items,
    `index` counts OPEN items of the section (None = after the last open one)."""
    if lo is None:
        # section has no items yet: before the next section header (or EOF)
        for i in range(header + 1, len(lines)):
            if _SECTION_RE.match(lines[i]):
                return i
        return len(lines)
    if done:
        return hi
    opens = [
        i for i in range(lo, hi)
        if (m := _ITEM_RE.match(lines[i])) and m.group(2) == " "
    ]
    if not opens:
        return lo
    if index is not None:
        if index <= 0:
            return opens[0]
        if index < len(opens):
            return opens[index]
    return opens[-1] + 1




class ToggleBody(BaseModel):
    id: str


class AddBody(BaseModel):
    section: str
    text: str
    origin: str | None = None
    index: int | None = None  # position among the section's open items (undo restore)
    done: bool = False
    tags: list[str] | None = None
    due: str | None = None  # ISO date/datetime or NL: today/tomorrow/weekday/next week
    priority: int | None = None  # 1..3


class DeleteBody(BaseModel):
    id: str


class EditBody(BaseModel):
    id: str
    text: str
    tags: list[str] | None = None  # only applied if the field is present in the request
    due: str | None = None
    priority: int | None = None


class MoveBody(BaseModel):
    id: str
    section: str
    index: int  # position among the target section's open items


class ClearDoneBody(BaseModel):
    section: str


def tasks() -> dict:
    with _locked():
        return {
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
            "file": _file(),
        }


def toggle(body: ToggleBody) -> dict:
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        cur_name = ""
        flipped = 0
        for i, raw in enumerate(lines):
            m = _SECTION_RE.match(raw)
            if m:
                cur_name = m.group(1)
                continue
            m = _ITEM_RE.match(raw)
            if m and _item_id(cur_name, m.group(3)) == body.id:
                new_mark = "x" if m.group(2) == " " else " "
                lines[i] = f"{m.group(1)}- [{new_mark}] {m.group(3)}"
                flipped += 1
                break
        if not flipped:
            raise HTTPException(status_code=404, detail=f"no item with id {body.id}")
        _write_file_unlocked("\n".join(lines))
        return {
            "ok": True,
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
        }


def add(body: AddBody) -> dict:
    # one markdown line per item — collapse any newlines (pasted multiline,
    # scripted clients) instead of letting them split the file format
    raw = re.sub(r"\s*[\r\n]+\s*", " ", body.text).strip()
    if not raw:
        raise HTTPException(status_code=400, detail="empty text")
    section = body.section.strip() or "To-do"
    # split off a typed origin suffix, then parse/merge metadata tokens
    mo = _ORIGIN_RE.search(raw)
    origin_raw = raw[mo.start():].strip() if mo else ""
    base = (raw[:mo.start()] if mo else raw).strip()
    clean, meta = _split_meta(base)
    if body.tags is not None:
        meta["tags"] = _clean_tags(body.tags)
    if body.due is not None:
        meta["due"], meta["due_time"] = _normalize_due(body.due)
    if body.priority is not None:
        meta["priority"] = _check_priority(body.priority)
    text = (clean + _meta_suffix(meta)).strip()
    if origin_raw:
        text = f"{text} {origin_raw}"
    else:
        origin = (body.origin or "").strip()
        if origin:
            text = f"{text} (from: {origin})"
    mark = "x" if body.done else " "
    line = f"- [{mark}] {text}"
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            lines = ["# Shared Task List", ""]
        header, lo, hi = _section_bounds(lines, section)
        if header is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"## {section}")
            lines.append("")
            lines.append(line)
        else:
            at = _insertion_point(lines, header, lo, hi, body.index, body.done)
            lines.insert(at, line)
        _write_file_unlocked("\n".join(lines))
        return {
            "ok": True,
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
        }


def delete(body: DeleteBody) -> dict:
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        cur_name = ""
        found = None
        open_before = 0
        for i, raw in enumerate(lines):
            m = _SECTION_RE.match(raw)
            if m:
                cur_name = m.group(1)
                open_before = 0  # index counts open items within the section
                continue
            m = _ITEM_RE.match(raw)
            if not m:
                continue
            done = m.group(2) != " "
            if _item_id(cur_name, m.group(3)) == body.id:
                found = (i, cur_name, m.group(3), done, open_before if not done else None)
                break
            if not done:
                open_before += 1
        if not found:
            raise HTTPException(status_code=404, detail=f"no item with id {body.id}")
        idx, sec, raw_text, done, open_at = found
        del lines[idx]
        _write_file_unlocked("\n".join(lines))
        return {
            "ok": True,
            "removed": {
                "section": sec,
                "text": raw_text,  # raw text incl. origin suffix, for exact undo
                "done": done,
                "index": open_at,  # position among open items at delete time
            },
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
        }


def edit_item(body: EditBody) -> dict:
    # same one-line-per-item guard as /add — newlines would split the format
    new_text = re.sub(r"\s*[\r\n]+\s*", " ", body.text).strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="empty text")
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        cur_name = ""
        for i, raw in enumerate(lines):
            m = _SECTION_RE.match(raw)
            if m:
                cur_name = m.group(1)
                continue
            m = _ITEM_RE.match(raw)
            if m and _item_id(cur_name, m.group(3)) == body.id:
                raw_text = m.group(3)
                mo = _ORIGIN_RE.search(raw_text)
                origin_raw = raw_text[mo.start():].strip() if mo else ""
                base = (raw_text[:mo.start()] if mo else raw_text).strip()
                # ws token: preserved across edits unless the editor retypes it
                mws = _WS_RE.search(base)
                ws_raw = base[mws.start():].strip() if mws else ""
                if mws:
                    base = (base[: mws.start()] + " " + base[mws.end():]).strip()
                _, meta = _split_meta(base)  # preserved unless overridden below
                provided = _provided_fields(body)
                if "tags" in provided:
                    meta["tags"] = _clean_tags(body.tags)
                if "due" in provided:
                    if body.due:
                        meta["due"], meta["due_time"] = _normalize_due(body.due)
                    else:
                        meta["due"], meta["due_time"] = None, None
                if "priority" in provided:
                    meta["priority"] = _check_priority(body.priority)
                new_clean, _ = _split_meta(new_text)  # drop tokens typed into the editor
                mws2 = _WS_RE.search(new_clean)  # editor typed the ws token back
                if mws2:
                    new_clean = (new_clean[: mws2.start()] + " " + new_clean[mws2.end():]).strip()
                line_text = (new_clean + _meta_suffix(meta)).strip()
                if ws_raw and not mws2:
                    line_text = f"{line_text} {ws_raw}"
                if origin_raw:
                    line_text = f"{line_text} {origin_raw}"
                lines[i] = f"{m.group(1)}- [{m.group(2)}] {line_text}"
                _write_file_unlocked("\n".join(lines))
                return {
                    "ok": True,
                    "sections": _read_sections_unlocked(),
                    "updated_at": _updated_at_unlocked(),
                }
        raise HTTPException(status_code=404, detail=f"no item with id {body.id}")


def move_item(body: MoveBody) -> dict:
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        cur_name = ""
        src = None
        for i, raw in enumerate(lines):
            m = _SECTION_RE.match(raw)
            if m:
                cur_name = m.group(1)
                continue
            m = _ITEM_RE.match(raw)
            if m and _item_id(cur_name, m.group(3)) == body.id:
                src = (i, m.group(1), m.group(2), m.group(3))
                break
        if src is None:
            raise HTTPException(status_code=404, detail=f"no item with id {body.id}")
        idx, indent, mark, raw_text = src
        del lines[idx]
        header, lo, hi = _section_bounds(lines, body.section)
        if header is None:
            raise HTTPException(status_code=404, detail=f"no section {body.section}")
        at = _insertion_point(lines, header, lo, hi, max(0, body.index), mark != " ")
        lines.insert(at, f"{indent}- [{mark}] {raw_text}")
        _write_file_unlocked("\n".join(lines))
        return {
            "ok": True,
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
        }


def clear_done(body: ClearDoneBody) -> dict:
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        sec = body.section.strip().lower()
        out = []
        cur = None
        removed = 0
        for raw in lines:
            m = _SECTION_RE.match(raw)
            if m:
                cur = m.group(1).strip().lower()
                out.append(raw)
                continue
            m2 = _ITEM_RE.match(raw)
            if cur == sec and m2 and m2.group(2) != " ":
                removed += 1
                continue
            out.append(raw)
        if not removed:
            raise HTTPException(status_code=404, detail="no done items in section")
        _write_file_unlocked("\n".join(out))
        return {
            "ok": True,
            "removed": removed,
            "sections": _read_sections_unlocked(),
            "updated_at": _updated_at_unlocked(),
        }


# --- Open item as a new workstream session ---------------------------------
#
# Turns a tasklist item into a fresh, context-loaded session:
#   1. gather a catch-up excerpt from the item's origin session (state.db)
#   2. write a handoff doc (~/.hermes/tasklist-handoffs/<slug>-<ts>.md)
#   3. spawn `tmux new-session -d -s <slug> hermes` and seed it via send-keys
#   4. poll state.db until the new session registers, return its id
# The pane then deep-links the returned session id (host.openSession).



def _new_anchor(existing: set) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    while True:
        a = "^" + "".join(alphabet[b % len(alphabet)] for b in os.urandom(4))
        if a not in existing:
            return a


def _ensure_anchor(item_id: str) -> dict:
    """Give item `item_id` its D1 anchor if it has none. -> {anchor, id (new hash), section,
    created}. The token goes before any (ws:) token / origin suffix; nothing else in the
    line moves. Holds the tasklist lock."""
    with _locked():
        try:
            with open(_file(), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tasklist.md not found") from None
        existing = {"^" + m.group(1) for ln in lines for m in _ANCHOR_RE.finditer(ln)}
        cur_name = ""
        for i, raw in enumerate(lines):
            m = _SECTION_RE.match(raw)
            if m:
                cur_name = m.group(1)
                continue
            m = _ITEM_RE.match(raw)
            if not (m and _item_id(cur_name, m.group(3)) == item_id):
                continue
            raw_text = m.group(3)
            ma = _ANCHOR_RE.search(raw_text)
            if ma:
                return {"anchor": "^" + ma.group(1), "id": item_id, "section": cur_name,
                        "created": False}
            anchor = _new_anchor(existing)
            mo = _ORIGIN_RE.search(raw_text)
            cut = mo.start() if mo else len(raw_text)
            mws = _WS_RE.search(raw_text[:cut])
            if mws:
                cut = mws.start()
            new_text = (raw_text[:cut].rstrip() + " " + anchor + " " + raw_text[cut:].lstrip()).rstrip()
            lines[i] = f"{m.group(1)}- [{m.group(2)}] {new_text}"
            _write_file_unlocked("\n".join(lines))
            return {"anchor": anchor, "id": _item_id(cur_name, new_text), "section": cur_name,
                    "created": True}
    raise HTTPException(status_code=404, detail=f"no item with id {item_id}")


class AnchorBody(BaseModel):
    id: str


def anchor_item(body: AnchorBody) -> dict:
    """D1: give an item its stable anchor (idempotent). 1guy calls this before it records a
    thread for a task the canvas opened, so the thread key is `task:^xxxx`."""
    a = _ensure_anchor(body.id)
    return {"ok": True, "anchor": a["anchor"], "key": "task:" + a["anchor"], "id": a["id"],
            "created": a["created"]}



ROUTES = {"toggle": (toggle, ToggleBody), "add": (add, AddBody), "delete": (delete, DeleteBody),
          "edit": (edit_item, EditBody), "move": (move_item, MoveBody),
          "clear-done": (clear_done, ClearDoneBody), "anchor": (anchor_item, AnchorBody)}


def call(route: str, payload: dict | None = None) -> dict:
    """In-process twin of the HTTP routes, for the board: 'tasks' -> the list, else a write.
    Errors come back as {"error": ...} like the HTTP API's JSON, never as an exception."""
    if route == "tasks":
        return tasks()
    if route not in ROUTES:
        return {"error": f"unknown tasklist route {route}"}
    fn, model = ROUTES[route]
    try:
        return fn(model(**(payload or {})))
    except HTTPException as e:
        return {"error": str(e.detail), "status": e.status_code}
    except (TypeError, ValueError) as e:
        return {"error": str(e)[:300], "status": 400}
