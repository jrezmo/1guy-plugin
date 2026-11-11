"""threads routes: every door's resolve-or-start into ONE item -> thread map (Phase 1).

  GET  /api/threads                       -> {threads:{key: rec}, aliases:{…}, hollow:[…]}
  GET  /api/threads/resolve?key=…         -> {key, thread|null, session_id|null}
  POST /api/threads/open   {key, aliases?, block?, title?, seed, resume?, opened_from,
                            handoff?, workspace?, view?, linked?}
        resolve-or-start: no thread -> create an API-server session, record it, send `seed`;
        a thread -> send `resume` (or `seed`) into the SAME session. Runs on the surface
        runner (stream, queue, steer, carried context), so the canvas sees the same turns.
        -> {key, session_id, created, block_id, run_id|null, queued|null, deep_link}
  POST /api/threads/branch {key, why, title?, opened_from}
        -> a child thread (new session) linked under key (Phase 6 drives it from the canvas)

The tasklist backend calls /open for ⤷ and ◫ over loopback and polls /api/surface/poll with
the returned block_id + run_id. If this server is down it falls back to its own CLI path.
"""
import importlib.util
import json
import re
import time
import os
import sys

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
import threads as T  # noqa: E402


class _Live:
    """Attribute view over the loaded surface module's globals (live, not a snapshot)."""
    def __init__(self, g):
        self.__dict__["_g"] = g

    def __getattr__(self, k):
        try:
            return self._g[k]
        except KeyError:
            raise AttributeError(k) from None


def _surface():
    """The LIVE surface runner: surface.py registers its globals on app/threads.py when it
    loads (serve.py loads routes in name order, surface before threads), so runs started here
    share the canvas runner's memory. Load it only when run standalone."""
    g = getattr(T, "RUNNER_GLOBALS", None)
    if g is not None:
        return _Live(g)
    mod = sys.modules.get("oneguy_routes_surface")
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            "oneguy_routes_surface", os.path.join(os.path.dirname(os.path.abspath(__file__)), "surface.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["oneguy_routes_surface"] = mod
        spec.loader.exec_module(mod)
    return mod


def _str(v, n=4000):
    s = str(v or "").strip()
    return s[:n]


def r_list(q, body):
    S = _surface()
    data = T.load(S.THREADS_FILE)
    return {"threads": data["threads"], "aliases": data.get("aliases") or {},
            "hollow": T.hollow(S.THREADS_FILE)}


def r_recent(q, body):
    """Thin shim: the logic is app/board/recent.py (vendored into the plugin)."""
    S = _surface()
    from board import recent as _recent
    try:
        limit = int((q or {}).get("limit") or 20)
    except ValueError:
        limit = 20
    return {"recent": _recent.build(threads_file=S.THREADS_FILE, limit=limit)}


def r_resolve(q, body):
    S = _surface()
    key, rec = T.resolve(q.get("key"), S.THREADS_FILE)
    return {"key": key, "thread": rec, "session_id": ((rec or {}).get("primary") or {}).get("session_id")}


def r_open(q, body):
    S = _surface()
    body = body or {}
    key = T.check_key(body.get("key"))
    aliases = [T.check_key(a) for a in (body.get("aliases") or []) if a]
    opened_from = _str(body.get("opened_from"), 40) or "phone"
    seed = _str(body.get("seed"))
    if not seed:
        raise ValueError("need seed")
    resume = _str(body.get("resume")) or seed
    block = body.get("block") if isinstance(body.get("block"), dict) else {}
    block = dict(block, id=key)
    block.setdefault("kind", key.split(":", 1)[0] if ":" in key else key)
    if body.get("title"):
        block.setdefault("title", _str(body.get("title"), 160))

    # single-flight with the surface runner's own resolve-or-start (surface.start_lock): one session per item
    with S.start_lock(key):
        k, rec = T.resolve(key, S.THREADS_FILE)
        if rec is None:
            for a in aliases:
                ak, arec = T.resolve(a, S.THREADS_FILE)
                if arec is not None:
                    k, rec = ak, arec
                    break
        sid = ((rec or {}).get("primary") or {}).get("session_id")
        if sid and S.session_exists(sid) is False:
            T.forget(k, S.THREADS_FILE)
            rec, sid = None, None
        created = False
        if not sid:
            sid, title = S.create_session(S.surface_title(block))
            k, rec, created = T.start(key, sid, opened_from, title=title, kind=block.get("kind"),
                                      aliases=aliases, handoff=body.get("handoff"),
                                      workspace=body.get("workspace"), p=S.THREADS_FILE)
            sid = rec["primary"]["session_id"]       # a lost race keeps the first session
        else:
            T.update(k, S.THREADS_FILE, handoff=body.get("handoff"), workspace=body.get("workspace"))
            for a in aliases + ([key] if key != k else []):
                T.alias(a, k, S.THREADS_FILE)
    # linked = the cards this thread is about (a lasso's selection): named in the first turn's context
    linked = [l for l in (body.get("linked") or []) if isinstance(l, dict)][:10] or None
    S.LIVE.ctx[k] = {"block": block, "linked": linked, "board_top": None, "opened_from": opened_from}
    said = S.r_say({}, {"block_id": k, "block": block, "linked": linked, "text": seed if created else resume, "mode": "queue",
                        "view": body.get("view")})     # what the user was looking at (talk router passes it)
    return {"ok": True, "key": k, "block_id": k, "session_id": sid, "created": created,
            "run_id": said.get("run_id"),
            "queued": said.get("queued"), "deep_link": f"hermes://open/{sid}"}


def r_branch(q, body):
    S = _surface()
    body = body or {}
    key = T.check_key(body.get("key"))
    k, rec = T.resolve(key, S.THREADS_FILE)
    if rec is None:
        raise ValueError(f"no thread for {key}")
    why = _str(body.get("why"), 200) or "branched"
    title = _str(body.get("title"), 160) or (rec.get("title") or k) + " · " + why
    sid, title = S.create_session(title)
    T.branch(k, sid, why, _str(body.get("opened_from"), 40) or "canvas", p=S.THREADS_FILE)
    return {"ok": True, "key": k, "session_id": sid, "title": title, "deep_link": f"hermes://open/{sid}"}


ROUTES = {
    ("GET", "/api/threads"): r_list,
    ("GET", "/api/threads/resolve"): r_resolve,
    ("GET", "/api/recent"): r_recent,
    ("POST", "/api/threads/open"): r_open,
    ("POST", "/api/threads/branch"): r_branch,
}
