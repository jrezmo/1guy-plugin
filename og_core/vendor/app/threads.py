"""threads: the ONE item -> thread map, shared by every door (unified audit §4.2, Phase 1).

~/.hermes/1guy/threads.json is THE answer to "which conversation is this item's":

    {"version": 1,
     "threads": {
       "task:^k7f2": {
         "primary":  {"session_id": "api_…", "engine": "api_server", "opened_from": "canvas", "at": 1793…},
         "children": [{"session_id": "…", "why": "branched: …", "opened_from": "phone", "at": …}],
         "workspace": "~/.hermes/workspaces/…" | null,
         "handoff":   "~/.hermes/tasklist-handoffs/….md" | null,
         "title": "…", "kind": "task",
         "surface": {"turns": 3, "carried_tokens": 812, "board_at": …, "last_activity": …}
       }},
     "aliases": {"task:69fdb6ab18": "task:^k7f2"}}

Rules
- An entry exists only when a session exists: start() refuses a falsy session_id, and
  there is no way to write a record without one (seam 8: no hollow threads).
- Keys are stable item keys: "task:^<anchor>" for tasklist items (D1), "<kind>:<ref>" for
  everything else, "home" for the home thread. A volatile key (a tasklist content hash)
  can be an ALIAS of a stable key; resolve() follows aliases.
- Writers take an exclusive flock on threads.json.lock and replace the file atomically
  (tmp + rename, 600), the tasklist.md.lock pattern, so the canvas server, the migration
  and any CLI can write concurrently.
Stdlib only.
"""
import contextlib
import fcntl
import json
import os
import re
import threading
import time

ENGINE = "api_server"
_local = threading.RLock()


def home_dir():
    return os.path.expanduser(os.environ.get("ONEGUY_HOME", "~/.hermes/1guy"))


def path():
    return os.environ.get("ONEGUY_THREADS_FILE") or os.path.join(home_dir(), "threads.json")


_KEY_RE = re.compile(r"[A-Za-z0-9:_\-.#/@^]{1,160}")


def check_key(key):
    k = str(key or "").strip()
    if not _KEY_RE.fullmatch(k):
        raise ValueError(f"bad thread key: {key!r}")
    return k


def empty():
    return {"version": 1, "threads": {}, "aliases": {}}


def load(p=None):
    p = p or path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("threads"), dict):
            data.setdefault("aliases", {})
            data.setdefault("version", 1)
            return data
    except (OSError, ValueError):
        pass
    return empty()


def save(data, p=None):
    p = p or path()
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


@contextlib.contextmanager
def _locked(p=None):
    """In-process RLock + cross-process flock. Yields the loaded data; caller mutates it
    and sets data['_dirty']=True to have it written back."""
    p = p or path()
    with _local:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        fd = os.open(p + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            data = load(p)
            yield data
            if data.pop("_dirty", False):
                save(data, p)
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def canonical(key, data=None):
    """Follow aliases (bounded) to the stable key."""
    data = data if data is not None else load()
    k, seen = key, set()
    while k in (data.get("aliases") or {}) and k not in seen:
        seen.add(k)
        k = data["aliases"][k]
    return k


def resolve(key, p=None):
    """-> (stable_key, thread dict) or (stable_key, None). Never writes."""
    key = check_key(key)
    data = load(p)
    k = canonical(key, data)
    rec = data["threads"].get(k)
    return k, (json.loads(json.dumps(rec)) if rec else None)


def session_of(key, p=None):
    _, rec = resolve(key, p)
    return ((rec or {}).get("primary") or {}).get("session_id")


def start(key, session_id, opened_from, *, engine=ENGINE, title=None, kind=None,
          aliases=(), handoff=None, workspace=None, p=None, at=None):
    """Record `session_id` as `key`'s primary thread. Requires a real session id.
    If the key (or one of `aliases`) already has a thread, that thread wins and is
    returned unchanged except for new aliases/handoff/workspace (resolve-or-start: the
    caller should have resolved first; a lost race keeps the first session).
    Returns (stable_key, record, created: bool)."""
    key = check_key(key)
    if not session_id:
        raise ValueError("no thread without a session: session_id is required")
    with _locked(p) as data:
        k = canonical(key, data)
        rec = data["threads"].get(k)
        # an alias might already own a thread (e.g. migrated under the old hash key): adopt it
        for a in aliases or ():
            a = check_key(a)
            ak = canonical(a, data)
            if rec is None and ak != k and ak in data["threads"]:
                rec = data["threads"].pop(ak)
                data["aliases"][ak] = k
            if a != k:
                data["aliases"][a] = k
        created = rec is None
        if created:
            rec = {"primary": {"session_id": session_id, "engine": engine,
                               "opened_from": opened_from, "at": at or time.time()},
                   "children": [], "workspace": None, "handoff": None,
                   "title": title, "kind": kind or (k.split(":", 1)[0] if ":" in k else k),
                   "surface": {"turns": 0}}
        if handoff:
            rec["handoff"] = handoff
        if workspace:
            rec["workspace"] = workspace
        if title and not rec.get("title"):
            rec["title"] = title
        data["aliases"].pop(k, None)
        data["threads"][k] = rec
        data["_dirty"] = True
        return k, json.loads(json.dumps(rec)), created


def branch(key, session_id, why, opened_from, *, engine=ENGINE, p=None, at=None):
    """Add a child thread (real new work) under an existing thread."""
    key = check_key(key)
    if not session_id:
        raise ValueError("no thread without a session: session_id is required")
    with _locked(p) as data:
        k = canonical(key, data)
        rec = data["threads"].get(k)
        if rec is None:
            raise KeyError(f"no thread for {key}")
        if all(c.get("session_id") != session_id for c in rec["children"]):
            rec["children"].append({"session_id": session_id, "engine": engine, "why": why,
                                    "opened_from": opened_from, "at": at or time.time()})
            data["_dirty"] = True
        return k, json.loads(json.dumps(rec))


def alias(old, key, p=None):
    old, key = check_key(old), check_key(key)
    if old == key:
        return key
    with _locked(p) as data:
        k = canonical(key, data)
        if old in data["threads"] and k not in data["threads"]:
            data["threads"][k] = data["threads"].pop(old)
        data["aliases"][old] = k
        data["_dirty"] = True
        return k


def update(key, p=None, **surface_fields):
    """Merge runner state (turns, carried_tokens, board_at, last_activity, title…) into an
    EXISTING thread. Returns the record, or None when the key has no thread (never creates)."""
    key = check_key(key)
    with _locked(p) as data:
        k = canonical(key, data)
        rec = data["threads"].get(k)
        if rec is None:
            return None
        s = rec.setdefault("surface", {})
        for f, v in surface_fields.items():
            if v is None:
                continue
            if f in ("title", "kind", "workspace", "handoff"):
                rec[f] = v
            else:
                s[f] = v
        data["_dirty"] = True
        return json.loads(json.dumps(rec))


def forget(key, p=None):
    """Drop a thread whose session is gone (deleted in Desktop / by a cleanup)."""
    key = check_key(key)
    with _locked(p) as data:
        k = canonical(key, data)
        rec = data["threads"].pop(k, None)
        if rec is not None:
            for a in [a for a, t in data["aliases"].items() if t == k]:
                del data["aliases"][a]
            data["_dirty"] = True
        return rec


def session_map(p=None):
    """session id -> set(thread keys + their aliases). For the board's sessions source."""
    data = load(p)
    back = {}
    for a, t in (data.get("aliases") or {}).items():
        back.setdefault(t, set()).add(a)
    out = {}
    for k, rec in data["threads"].items():
        sids = [(rec.get("primary") or {}).get("session_id")]
        sids += [c.get("session_id") for c in rec.get("children") or []]
        for s in filter(None, sids):
            out.setdefault(s, set()).add(k)
    return out


def hollow(p=None):
    """Keys whose record has no primary session id (must always be empty: acceptance (d))."""
    data = load(p)
    return [k for k, r in data["threads"].items() if not ((r or {}).get("primary") or {}).get("session_id")]
