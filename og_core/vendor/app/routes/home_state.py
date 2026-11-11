"""WS-A1: the home surface's spatial memory. GET/POST /api/home/state.

Where the user put things (placements), what he parked, and the zoom level, kept per
namespace so fixture runs (?ns=fixture) and test runs never overwrite the user's real
layout (ns "live"). File: ~/.hermes/1guy/home-state.json (override with
ONEGUY_HOME_STATE), shape:

    {"version": 1, "namespaces": {"live": {"placements": {...}, "view": {...},
                                            "updated": 1730650000000}, ...}}

Writes are validated, capped at 2 MB for the whole file, and atomic
(tmp + fsync + rename, chmod 600). Stdlib only.
"""
import json
import math
import os
import re
import tempfile
import threading

MAX_BYTES = 2 * 1024 * 1024
MAX_PLACEMENTS = 5000
NS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:@/#+-]{1,200}$")
VIEW_KEYS = {"level": str, "zoom": (int, float), "x": (int, float), "y": (int, float)}
_LOCK = threading.Lock()


def _path():
    return os.environ.get("ONEGUY_HOME_STATE") or os.path.expanduser("~/.hermes/1guy/home-state.json")


def _ns(query):
    ns = (query or {}).get("ns") or "live"
    if not NS_RE.match(ns):
        raise ValueError("ns must be 1-32 chars of a-z 0-9 _ -")
    return ns


def _read_all():
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("namespaces"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "namespaces": {}}


def _num(v, lo=-1e7, hi=1e7):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError("placement numbers must be finite")
    if v < lo or v > hi:
        raise ValueError("placement number out of range")
    return round(float(v), 2)


def _clean(body):
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    raw = body.get("placements") or {}
    if not isinstance(raw, dict):
        raise ValueError("placements must be an object")
    if len(raw) > MAX_PLACEMENTS:
        raise ValueError(f"too many placements (max {MAX_PLACEMENTS})")
    placements = {}
    for bid, p in raw.items():
        if not isinstance(bid, str) or not ID_RE.match(bid):
            raise ValueError(f"bad block id: {str(bid)[:60]!r}")
        if not isinstance(p, dict):
            raise ValueError(f"placement for {bid} must be an object")
        out = {}
        for k in ("x", "y"):
            if k in p and p[k] is not None:
                out[k] = _num(p[k])
        for k in ("w", "h"):
            if k in p and p[k] is not None:
                out[k] = _num(p[k], 0, 10000)
        if "parked" in p:
            out["parked"] = bool(p["parked"])
        if "summoned" in p:
            out["summoned"] = bool(p["summoned"])
        if "size" in p and p["size"] in ("hero", "card", "compact", "chip"):
            out["size"] = p["size"]
        if "at" in p and p["at"] is not None:
            out["at"] = _num(p["at"], 0, 1e14)
        placements[bid] = out
    view = {}
    if isinstance(body.get("view"), dict):
        for k, t in VIEW_KEYS.items():
            v = body["view"].get(k)
            if isinstance(v, t) and not isinstance(v, bool):
                view[k] = v if isinstance(v, str) else _num(v)
        if view.get("level") not in (None, "blocks", "projects"):
            view.pop("level")
    expanded = body.get("expanded") or []
    if not isinstance(expanded, list) or not all(isinstance(x, str) and len(x) <= 120 for x in expanded):
        raise ValueError("expanded must be a list of cluster ids")
    updated = body.get("updated")
    updated = _num(updated, 0, 1e14) if updated is not None else 0
    return {"placements": placements, "view": view, "expanded": expanded[:200], "updated": updated}


def get_state(query, body):
    ns = _ns(query)
    data = _read_all()
    st = data["namespaces"].get(ns)
    if not st:
        return {"ns": ns, "placements": {}, "view": {}, "expanded": [], "updated": 0, "empty": True}
    return dict(st, ns=ns)


def post_state(query, body):
    ns = _ns(query)
    clean = _clean(body)
    path = _path()
    with _LOCK:
        data = _read_all()
        if (query or {}).get("delete") == "1":
            data["namespaces"].pop(ns, None)
        else:
            data["namespaces"][ns] = clean
        blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(blob.encode("utf-8")) > MAX_BYTES:
            raise ValueError("home state would exceed 2 MB")
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".home-state.", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return {"ok": True, "ns": ns, "placements": len(clean["placements"]), "bytes": len(blob)}


ROUTES = {
    ("GET", "/api/home/state"): get_state,
    ("POST", "/api/home/state"): post_state,
}
