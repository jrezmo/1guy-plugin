"""sample: the sample desk (docs/product.md, door B). A read-only board made of the synthetic demo
world, for someone looking around on an empty install.

    board(now=None) -> the sample board: every block wears sample=True, board["sample"] is True

It is a snapshot (sample_desk.json beside this file, written by demo/world/sample_desk.py from the
fully fake demo world: no real people, nothing of the host's). Every time in it is moved by the same
amount so the desk always reads as 'just now'. This module reads that one file and nothing else: it
never opens state.db, the tasklist, threads or the ledger, and it writes nothing, so a sample card
can never land on the real desk and a real card can never show up here. The client keeps its half
of the promise in app/home/core.js (on the sample desk the network door refuses every write).
Stdlib only.
"""
import datetime as _dt
import json
import os
import re

from . import util

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_desk.json")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EPOCH_KEYS = {"since", "at_ts", "last", "started", "ts"}
LINE = "this is a sample desk. none of it is yours, and nothing here is saved."
_snap = {"mtime": None, "doc": None}


def _load():
    try:
        m = os.path.getmtime(PATH)
        if _snap["mtime"] != m:
            with open(PATH, encoding="utf-8") as f:
                _snap.update(mtime=m, doc=json.load(f))
        return _snap["doc"]
    except (OSError, ValueError):
        return None


def _shift(v, delta, days, key=None):
    if isinstance(v, dict):
        return {k: _shift(x, delta, days, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_shift(x, delta, days, key) for x in v]
    if isinstance(v, str):
        if _STAMP.match(v):
            ts = util.parse_ts(v)
            return util.iso(ts + delta) if ts else v
        if _DAY.match(v):
            try:
                return (_dt.date.fromisoformat(v) + _dt.timedelta(days=days)).isoformat()
            except ValueError:
                return v
        return v
    if key in _EPOCH_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e9:
        return round(v + delta)
    return v


def board(now=None):
    """The sample desk as a board payload. Missing or unreadable snapshot -> an honest empty desk."""
    now = now or util.now()
    doc = _load()
    if not isinstance(doc, dict) or not isinstance(doc.get("blocks"), list):
        return {"generated_at": util.iso(now), "sample": True, "first_run": None, "sources": {}, "projects": [],
                "blocks": [], "briefing": {"line": "the sample desk isn't here.", "for_you": [], "broke": [], "by": "sample"}}
    made = util.parse_ts(doc.get("generated_at")) or now
    delta = now - made
    days = (_dt.date.fromtimestamp(now) - _dt.date.fromtimestamp(made)).days
    out = _shift(doc, delta, days)
    for b in out["blocks"]:
        b["sample"] = True
    out.update(generated_at=util.iso(now), sample=True, first_run=None, took_ms=0)
    out.pop("door", None)
    br = out.get("briefing") if isinstance(out.get("briefing"), dict) else {}
    br.update(line=LINE, by="sample")          # he says where you are; he never claims this is your Hermes
    br.pop("door", None)
    out["briefing"] = br
    return out
