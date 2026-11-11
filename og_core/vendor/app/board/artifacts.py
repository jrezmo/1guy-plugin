"""artifacts: things the crew made for the user — the "made for you" lane.

Registry: ~/.hermes/1guy/artifacts.jsonl, one JSON object per line:
  {"id": "reel-v1", "title": "1guy reel, first cut",
   "summary": "165s, voiceover + score", "url": "/media/reel/1guy-reel.mp4",
   "path": "~/projects/1guy/media/reel/1guy-reel.mp4",
   "kind": "media|doc|page|report|data", "by": "session-id-or-cron", "created": "ISO-8601",
   "origin": "the reel job · 20261101_113813_0db8b4"}

A brief (board/brief.py, frontier 6) is a line with kind "brief" plus `sources` (the block ids it
was written from) and `pile` (the pile it was made on). Its card links to its sources (the board
makes links two-way, so each source shows the brief), sits in that pile, and carries the brief's
words in detail.body.

Origin follows the tasklist plugin's proven convention: "label · session-id"
(resolved against ~/.hermes/state.db; label alone resolves to that source's
newest session). A local session makes the card's "↤ label" origin a deep link
into the session that made the thing (hermes://open/<id>).

Anything an agent creates that the user might want to visit gets appended here (see
docs/artifacts-convention.md) — via POST /api/artifacts or by appending a line
to the file directly. GET /api/artifacts reads it; this board source turns it
into blocks (kind "artifact") so the newest things surface on the home board.

Read-only here: the file is the source of truth, newest first in the lane.
"""
import json
import os
import re
import sqlite3

from . import util
from .block import make

TTL = 15.0
REGISTRY = util.oneguy("artifacts.jsonl")
STATE_DB = os.environ.get("ONEGUY_STATE_DB", os.path.expanduser("~/.hermes/state.db"))
MAX_BLOCKS = 60          # keep the board sane; the route can return everything
KIND_STATUS = "idle"     # idle + recency keeps artifacts visible without shouting
_ORIGIN_SPLIT = re.compile(r"\s*·\s*")


def read_registry(limit=None):
    """All valid lines, oldest first (file order). Bad lines are skipped."""
    out = []
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if isinstance(a, dict) and a.get("id") and (a.get("url") or a.get("path")):
                    out.append(a)
    except OSError:
        return []
    return out[-limit:] if limit else out


def append(entry):
    """Add one artifact (dict). Caller owns validation; id is required."""
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    with open(REGISTRY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_origin(raw):
    """'label · session-id' (or 'label') -> {label, session_id, local}.

    Same shape the tasklist plugin ships: a session_id that exists in this
    gateway's state.db is 'local' and deep-links (hermes://open/<id>); a bare
    label falls back to that source's newest session. No DB -> inert label.
    """
    if isinstance(raw, dict):
        return {"label": str(raw.get("label") or ""), "session_id": raw.get("session_id"),
                "local": False}
    label, sid = str(raw or "").strip(), None
    if not label:
        return None
    parts = _ORIGIN_SPLIT.split(label, 1)
    if len(parts) == 2:
        label, sid = parts[0].strip(), parts[1].strip()
    local = False
    if sid:
        try:
            con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=2)
            row = con.execute("select 1 from sessions where id = ?", (sid,)).fetchone()
            con.close()
            local = bool(row)
        except sqlite3.Error:
            local = False
    return {"label": label, "session_id": sid or None, "local": local}


def fetch():
    arts = read_registry(limit=MAX_BLOCKS)
    blocks = []
    for a in arts:
        url = a.get("url") or ""
        sources = [s for s in a.get("sources") or [] if isinstance(s, str)] if isinstance(a.get("sources"), list) else []
        pile = a.get("pile") if isinstance(a.get("pile"), str) else None
        extra = {}
        if a.get("kind") == "brief":
            from . import brief          # late: brief imports this module
            extra = dict(sources=sources, pile=pile or "", body=brief.read(a.get("path")),
                         written_by=a.get("written_by") or "")
        blocks.append(make(
            "artifact", str(a["id"]), util.redact(a.get("title") or str(a["id"])),
            summary=util.redact(a.get("summary") or ""),
            status=KIND_STATUS,
            tags=[t for t in [a.get("kind"), a.get("by") and "made-for-you"] if t],
            updated=util.iso(util.parse_ts(a.get("created"))),
            source_type="artifact", url=url, links=sources,
            project=pile if pile and pile.startswith("project:") else None,
            detail={**extra, "path": a.get("path") or "", "kind": a.get("kind") or "thing",
                    "by": a.get("by") or "", "created": a.get("created") or "",
                    "origin": _resolve_origin(a.get("origin"))},
        ))
    return {"ok": True, "blocks": blocks, "registry": REGISTRY}
