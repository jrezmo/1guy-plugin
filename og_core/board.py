"""The board: this Hermes install's loose ends as one attention-ranked list of blocks.

    build(fresh=False) -> {generated_at, sources, projects, blocks, briefing}
    ticker()           -> the docked strip's plain-words digest of the same board
    app()              -> the loaded vendored 1guy modules (talk.py dispatches into them)

The ranking, the block shapes and the briefing are the vendored 1guy board
(og_core/vendor/app/board, copied verbatim by scripts/vendor_canvas.py). This module is the
adapter that makes that code portable:

  * every path points into this Hermes home (store.vendored_env + util.CFG)
  * the tasklist is called in-process (tasklist.call), not over HTTP
  * only sources any install has are on: tasks, sessions, jobs, proposals, brain, artifacts,
    talk threads, and the glance tiles (to-do; email and calendar through the user's own
    Hermes, see vendored board/mailcal.py: 1guy reads what a Hermes job of theirs wrote). The private-box sources (GitHub issues, media, plugin
    tiles, Gmail) are stubbed out at vendor time and never loaded.
  * headlines ask the host's LLM (talk.llm) instead of an API-server URL + key
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import threading
import time
import types

from . import store, tasklist

VENDOR_APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "app")
PORTABLE = ("tasks", "sessions", "jobs", "brain", "proposals", "artifacts", "talk", "widgets")
LOCAL = "oneguy-local://tasklist"

_lock = threading.RLock()
_app: types.SimpleNamespace | None = None


def app() -> types.SimpleNamespace:
    """Import the vendored modules once, pointed at this install. -> namespace(board, util, ...)"""
    global _app
    with _lock:
        if _app is None:
            _app = _load()
        return _app


def _load() -> types.SimpleNamespace:
    os.environ.update(store.vendored_env())          # the vendored modules read these at import
    if VENDOR_APP not in sys.path:
        sys.path.insert(0, VENDOR_APP)               # they import each other by bare name
    b = importlib.import_module("board")
    if os.path.dirname(os.path.abspath(b.__file__)) != os.path.join(VENDOR_APP, "board"):
        raise ImportError(f"another module named 'board' is already loaded from {b.__file__}")
    util, home, data = b.util, store.hermes_home(), store.data_dir()
    util.HOME = str(home.parent)
    util.CFG.update({
        "tasklist_api": LOCAL,
        "hermes_home": str(home),            # mailcal: skills/, config.yaml server names, cron output
        "state_db": str(store.state_db()),
        "cron_jobs": str(store.cron_dir() / "jobs.json"),
        "cron_output": str(store.cron_dir() / "output"),
        "oneguy_dir": str(data),
        "logs_dir": str(data / "logs"),
        "media_blocks": str(data / "media-blocks.json"),
        "repo_root": str(data),
        "projects_glob_root": str(data / "none"),
        "email_glance": False,               # no direct mail or calendar reader in the plugin:
        "calendar_feed": False,              # mail and calendar come only through the user's Hermes
    })
    b.artifacts.STATE_DB = str(store.state_db())
    # computed at import, before CFG pointed here: without this the "made for you" registry (and
    # the briefs written beside it) would live under ~/.hermes/1guy instead of the plugin's folder
    b.artifacts.REGISTRY = str(data / "artifacts.jsonl")
    b.headlines.ENV_PATH = b.headlines.CONFIG_PATH = os.devnull    # never a key or config file
    b.headlines.chat = _host_llm                                   # headlines ask the host's model
    for name in ("untangle", "brief"):                             # so do untangle (piles from history)
        try:                                                       # and brief (a pile written up)
            importlib.import_module("board." + name).chat = _host_llm
        except ImportError:
            pass                                                   # an older vendored canvas

    real_http = util.http_json

    def http_json(url, body=None, timeout=4.0):
        if not str(url).startswith(LOCAL):
            return real_http(url, body, timeout)
        res = tasklist.call(str(url)[len(LOCAL):].strip("/"), body)
        if isinstance(res, dict) and res.get("error") and "status" in res:
            raise util.HttpError(res["status"], f"{res['status']}: {res['error']}")
        return res
    util.http_json = http_json

    for name in [n for n in b.SOURCES if n not in PORTABLE]:
        del b.SOURCES[name]
    # a fresh install has no session db or cron file yet: that is "nothing", not an error
    for name, needs in (("sessions", "state_db"), ("jobs", "cron_jobs")):
        b.SOURCES[name] = _Quiet(b.SOURCES[name], util, needs)
    b.SOURCES["sessions"] = _Roomy(b.SOURCES["sessions"], b.sessions)
    real_build = b.build_board      # the canvas's own /board route calls this by name

    def build_board(*args, **kwargs):      # whatever the vendored route passes (force, explain, here, …)
        return first_run(real_build(*args, **kwargs))
    b.build_board = build_board
    return types.SimpleNamespace(board=b, util=util, headlines=b.headlines)


def _host_llm(prompt, timeout=60):
    from . import talk          # late: talk imports this module
    return talk.llm(prompt, timeout)


class _Quiet:
    """A source that reports an honest empty result while the file it reads does not exist."""

    def __init__(self, source, util, cfg_key):
        self.source, self.util, self.cfg_key, self.TTL = source, util, cfg_key, source.TTL

    def fetch(self):
        if not os.path.exists(self.util.CFG[self.cfg_key]):
            return {"blocks": [], "count": 0}
        return self.source.fetch()


class _Roomy:
    """Sessions on a quiet install. The board normally shows only the last week's chats with
    real back-and-forth; when that leaves nothing, look further back and accept short ones, so
    a new user still sees their own work instead of an empty room."""

    def __init__(self, source, module):
        self.source, self.module, self.TTL = source, module, source.TTL

    def fetch(self):
        out = self.source.fetch()
        if out.get("blocks"):
            return out
        m, saved = self.module, (self.module.MIN_MESSAGES, self.module.WINDOW_S)
        m.MIN_MESSAGES, m.WINDOW_S = 2, 90 * 86400
        try:
            return self.source.fetch()
        finally:
            m.MIN_MESSAGES, m.WINDOW_S = saved


# ---------------------------------------------------------------- the first run
def first_run(board: dict) -> dict:
    """The two doors (vendored board/doors.py) own the first run: door A counts a lived-in home
    and names what needs you, door B asks a sparse one what it is in the middle of. One gap is
    left for the plugin: door A on a home where nothing is pressing and no pile has been
    proposed says "…nothing needs you right now." over an empty rail. There he still gets
    two real places to start, the latest chat and a scheduled job, so the first screen is
    never a line with nothing under it. Nothing is invented and his line is left alone."""
    br = board.get("briefing") or {}
    if board.get("first_run") != "a" or br.get("for_you") or br.get("broke"):
        return board
    blocks = board.get("blocks") or []

    def newest(kind):
        return max((b for b in blocks if b["kind"] == kind), key=lambda b: b.get("updated") or "", default=None)
    session, job = newest("session"), None
    jobs = [b for b in blocks if b["kind"] == "job" and b.get("status") != "idle"]
    if jobs:
        job = min(jobs, key=lambda b: (b.get("detail") or {}).get("next_run") or "9")
    picks, whys = [], {}
    if session:
        picks.append(session)
        whys[session["id"]] = "your latest chat"
    if job:
        picks.append(job)
        sched = str((job.get("detail") or {}).get("schedule") or "").strip()
        whys[job["id"]] = ("runs " + sched) if sched and len(sched) < 34 else "runs on its own"
        if job["status"] == "done":
            job["status"] = "waiting"       # a healthy job between runs; "done" cards stay off the rail
    if not picks:
        return board
    for b in picks:
        b["for_you"], b["fy_why"] = True, whys[b["id"]]
    board["briefing"] = dict(br, for_you=[b["id"] for b in picks], whys=dict(br.get("whys") or {}, **whys))
    return board


def build(fresh: bool = False, explain: bool = False) -> dict:
    return app().board.build_board(force=fresh, explain=explain)


def invalidate(name: str | None = None) -> None:
    app().board.invalidate(name)


# ---------------------------------------------------------------- the docked ticker
_TICKER_TTL = 15.0
_ticker_cache: dict = {"at": 0.0, "payload": None}
# plain words on every face: no anchors, tags, origins, paths or ids
_STRIPS = [(re.compile(r"\s*\^[a-z0-9]{4}\b", re.I), " "), (re.compile(r"\s*#[\w-]+"), " "),
           (re.compile(r"\s*\(from:[^)]*\)"), " "), (re.compile(r"(?:~|\./|/)[\w.@/-]{3,}"), " "),
           (re.compile(r"\b(?:api_|deleg_|sess_)[0-9a-zA-Z_]{6,}\b"), " ")]


def _plain(text, cap=78) -> str:
    s = " ".join(str(text or "").split())
    for rx, repl in _STRIPS:
        s = rx.sub(repl, s)
    s = " ".join(s.split())
    if len(s) > cap:
        cut = s[:cap].rsplit(" ", 1)[0]
        s = (cut or s[:cap]).rstrip(" ,;:-—") + "…"
    return s or "…"


def session_of(block: dict) -> str | None:
    """The Hermes session a block opens, if it has one."""
    bid = block.get("id") or ""
    if bid.startswith("session:"):
        return bid.split(":", 1)[1]
    d = block.get("detail") if isinstance(block.get("detail"), dict) else {}
    if d.get("session_id"):
        return str(d["session_id"])
    if isinstance(d.get("origin"), dict) and d["origin"].get("session_id"):
        return str(d["origin"]["session_id"])
    return next((l.split(":", 1)[1] for l in block.get("links") or []
                 if isinstance(l, str) and l.startswith("session:")), None)


def _ticker_item(b: dict, src: str | None = None) -> dict:
    project = (b.get("project") or "").split(":")[-1]
    head = (b.get("headline") or {}).get("headline") if isinstance(b.get("headline"), dict) else None
    raw = head or b.get("title") or b.get("summary")
    return {"id": b.get("id"), "src": src or project or b.get("kind") or "board", "text": _plain(raw),
            "tone": {"needs-you": "you", "failed": "bad"}.get(b.get("status") or "", ""),
            "session_id": session_of(b)}


def ticker() -> dict:
    """{ok, stale, chips (<=3 needs-you), items (calm marquee), waiting_on_you}. Never raises:
    a failing board serves the last good read marked stale."""
    now = time.time()
    if _ticker_cache["payload"] is not None and now - _ticker_cache["at"] < _TICKER_TTL:
        return _ticker_cache["payload"]
    try:
        # piles (projects) are containers, not news: the strip carries the items themselves
        blocks = [b for b in build().get("blocks") or [] if isinstance(b, dict) and b.get("kind") != "project"]
    except Exception as e:
        last = _ticker_cache["payload"]
        return dict(last or {"chips": [], "items": [], "waiting_on_you": 0},
                    ok=bool(last), stale=True, error=str(e)[:200])
    rank = lambda bs: sorted(bs, key=lambda b: b.get("attention") or 0, reverse=True)  # noqa: E731
    needs = rank(b for b in blocks if b.get("status") == "needs-you")
    chips = [_ticker_item(b) for b in needs[:3]]
    seen = {c["id"] for c in chips}
    items = []
    for status, n, src in (("needs-you", 4, None), ("failed", 3, None), ("active", 8, None),
                           ("waiting", 3, None), ("proposed", 5, "proposal")):
        for b in rank(b for b in blocks if b.get("status") == status and b.get("id") not in seen)[:n]:
            it = _ticker_item(b, src)
            if it["text"] != "…":
                items.append(it)
    payload = {"ok": True, "stale": False, "chips": chips, "items": items[:40],
               "waiting_on_you": len(needs)}
    _ticker_cache.update(at=now, payload=payload)
    return payload
