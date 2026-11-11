"""Talk: the talk bar, one thread per item, and agent turns. Everything the canvas calls.

    dispatch(method, path, query, body) -> (json, status)   every canvas route, by path
    llm(prompt, timeout)                -> text              one host-owned model call
    RELAY                                                    agent turns through the desktop app

Routes. The canvas client is the vendored 1guy client and still calls the 1guy route table
(/board, /talk, /judgment, /surface/*, /threads/*, /home/state, ...). The few routes the
standalone server answers from outside app/ are answered here (OWN_ROUTES: proposal verdicts). `dispatch` loads the
vendored route modules (og_core/vendor/app/routes, each exporting ROUTES) and runs the
handler: one deep door instead of thirty FastAPI functions.

LLM calls (headlines, the talk bar's router) go through the host: `ctx.llm` handed over by
the plugin's register(ctx), which uses whatever model and login the user already has. No
API-server URL, no key.

Agent turns ("one item = one Hermes session"). The 1guy runner used to drive the Hermes API
server over HTTP, which a stock install does not run. Here the runner's three upstream calls
(create a session, stream a turn, stop a turn) become jobs on RELAY. The desktop half claims
them (GET /relay/next), runs them on the app's own gateway connection (session.create,
prompt.submit, session.interrupt) and pushes what streams back (POST /relay/push). So turns
run as real, titled Hermes sessions under the user's normal agent, and the canvas streams
them through the unchanged long-poll. If no desktop window is attached, a turn fails with a
plain sentence instead of hanging.
"""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import threading
import time
import uuid

from . import board as _board

ROUTES_DIR = os.path.join(_board.VENDOR_APP, "routes")
ALIASES = {"/thread/open": "/threads/open"}
CLAIM_TIMEOUT = 20.0      # a job nobody claims this fast means no desktop window is attached
STREAM_IDLE = 600.0

STANDING_RULES = (
    "You are the one guy: the user's single companion on their 1guy board in Hermes. This is "
    "ONE thread about one item on the board (other items have their own threads). Keep state "
    "in files and the tasklist, not in chat. Propose before changing anything outside this "
    "item. Talk like a person: short, concrete, a few sentences unless asked for more. "
    "The user may cut you off: a line starting 'The user steered' means drop what you were saying "
    "and pivot; 'The user queued' is an add-on to handle after the current point."
)

_lock = threading.RLock()
_routes: dict | None = None
_route_errors: dict = {}
_llm_door = None          # ctx.llm from register(ctx)


# ---------------------------------------------------------------- the host LLM
def set_llm(door) -> None:
    global _llm_door
    _llm_door = door


def llm(prompt: str, timeout: float = 60.0) -> str:
    """One bounded model call on the user's active model. Raises when the host has none."""
    door = _llm_door
    if door is None:     # register(ctx) has not run in this process: cards keep their raw titles
        raise RuntimeError("no model is available to the plugin")
    res = door.complete(messages=[{"role": "user", "content": prompt}],
                        timeout=timeout, purpose="1guy.plain-words")
    return res.text or ""


# ---------------------------------------------------------------- the relay
class Relay:
    """Jobs for the desktop half, answered as a stream of events. Thread-safe, in memory."""

    def __init__(self):
        self.cond = threading.Condition()
        self.pending: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.seen_at = 0.0            # last time a desktop asked for work

    def submit(self, kind: str, **payload) -> dict:
        job = {"id": "j_" + uuid.uuid4().hex[:10], "kind": kind, "payload": payload,
               "events": queue.Queue(), "claimed": False, "at": time.time()}
        with self.cond:
            self.jobs[job["id"]] = job
            self.pending.append(job)
            for old in [j for j in self.jobs.values() if time.time() - j["at"] > 3600]:
                self.jobs.pop(old["id"], None)
            self.cond.notify_all()
        _nudge()
        return job

    def next(self, wait: float = 20.0) -> list[dict]:
        """The desktop's long-poll: claim every pending job (or wait for one)."""
        deadline = time.time() + max(0.0, min(wait, 25.0))
        with self.cond:
            self.seen_at = time.time()
            while not self.pending and time.time() < deadline:
                self.cond.wait(min(1.0, deadline - time.time()))
            out, self.pending = self.pending, []
            for j in out:
                j["claimed"] = True
            self.seen_at = time.time()
        return [{"id": j["id"], "kind": j["kind"], **j["payload"]} for j in out]

    def push(self, job_id: str, events: list) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        for e in events or []:
            if isinstance(e, dict):
                job["events"].put(e)
        return True

    def take(self, job: dict, timeout: float) -> dict:
        """Next event of a job. Raises with a plain sentence when the desktop is not there."""
        deadline = time.time() + timeout
        while True:
            try:
                return job["events"].get(timeout=0.5)
            except queue.Empty:
                pass
            if not job["claimed"] and time.time() - job["at"] > CLAIM_TIMEOUT:
                with self.cond:
                    if job in self.pending:
                        self.pending.remove(job)
                raise RuntimeError("Hermes Desktop isn't attached. Open the 1guy page in the "
                                   "desktop app and say that again.")
            if time.time() > deadline:
                raise RuntimeError("Hermes went quiet on this turn.")

    def attached(self) -> bool:
        return time.time() - self.seen_at < 45


RELAY = Relay()


def _nudge() -> None:
    """Wake the desktop half now (it also long-polls, so this is only an accelerator)."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event("1guy", "relay.pending", {})
    except Exception:
        pass


class _Stream:
    """What the vendored runner iterates: server-sent-event lines, fed by relay events."""

    def __init__(self, job):
        self.job, self.closed = job, False

    def __iter__(self):
        while not self.closed:
            ev = RELAY.take(self.job, STREAM_IDLE)
            name = str(ev.get("event") or "")
            yield f"event: {name}\n".encode()
            yield ("data: " + json.dumps(ev.get("data") or {}) + "\n").encode()
            if name == "done":
                return

    def close(self):
        self.closed = True


def _create_session(title):
    job = RELAY.submit("create", title=title)
    ev = RELAY.take(job, 60)
    data = ev.get("data") or {}
    if ev.get("event") != "created" or not data.get("session_id"):
        raise RuntimeError(str(data.get("message") or "Hermes could not start a session"))
    return data["session_id"], data.get("title") or title


def _upstream(method, path, body=None, timeout=30):
    """Stand-in for the runner's HTTP call to the API server: only the turn stream is used."""
    parts = path.strip("/").split("/")
    if method == "POST" and len(parts) == 5 and parts[:2] == ["api", "sessions"] and parts[3:] == ["chat", "stream"]:
        return _Stream(RELAY.submit("turn", session_id=parts[2], message=(body or {}).get("message") or ""))
    raise RuntimeError(f"unsupported upstream call {method} {path}")


def _stop_run(session_id):
    RELAY.submit("stop", session_id=session_id)
    return {"ok": True}


# ---------------------------------------------------------------- the route table
def _load_routes() -> dict:
    app = _board.app()                      # configures env + sys.path before any route imports
    table: dict = {}
    for name in sorted(os.listdir(ROUTES_DIR)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        modname = "oneguy_routes_" + name[:-3]
        try:
            spec = importlib.util.spec_from_file_location(modname, os.path.join(ROUTES_DIR, name))
            mod = importlib.util.module_from_spec(spec)
            import sys
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            _adapt(name[:-3], mod, app)
            for (method, path), fn in getattr(mod, "ROUTES", {}).items():
                table[(method, path[4:] if path.startswith("/api/") else path)] = fn
        except Exception as e:              # one broken module never takes the rest down
            _route_errors[name] = repr(e)
    return table


def _adapt(name, mod, app) -> None:
    """Point a vendored route module's outward seams at this install."""
    from . import tasklist
    if name == "surface":
        mod.STANDING_RULES = STANDING_RULES
        mod.create_session = _create_session
        mod._hreq = _upstream
        mod.tasklist_anchor = lambda task_hash, timeout=4: tasklist.call("anchor", {"id": task_hash}).get("anchor")
        real_hard_stop = mod._hard_stop

        def hard_stop(run, why):            # the runner stops by run id; the relay stops by session
            real_hard_stop(run, why)
            rec = mod.get_surface(run.block_id) or {}
            if rec.get("session_id"):
                _stop_run(rec["session_id"])
            if run.resp is not None:
                RELAY.push(run.resp.job["id"], [{"event": "done", "data": {}}])
        mod._hard_stop = hard_stop
    elif name == "talk":
        mod._tasklist_post = lambda path, payload, timeout=8: _ok(tasklist.call(path.rsplit("/", 1)[-1], payload))
    if name == "talk":                       # the talk bar's router asks the host's model too
        try:
            import router as _router
            real = _router._llm_classify
            _router._llm_classify = lambda text, faces, titles, timeout=12.0, post=None: real(
                text, faces, titles, timeout,
                post=lambda prompt: {"choices": [{"message": {"content": llm(prompt, timeout)}}]})
        except Exception as e:
            _route_errors["router"] = repr(e)


def proposal_verdict(q, body):
    """POST /brain/proposal {id, writer, op}: the user's answer to a proposal card.

    A proposal that carries edits (preview before yes) is carried out by the vendored
    board.proposals: every edit applied through the tasklist, one ledger line each, an undo token;
    `op: undo {token}` takes it all back. A plain proposal is kept or dismissed; keeping one that
    is a to-do in waiting (task / stalled / followup) puts its line on the list first, and if the
    list refuses, nothing changes."""
    from . import tasklist
    P = _board.app().board.proposals
    body = body if isinstance(body, dict) else {}
    pid, writer, op = body.get("id"), body.get("writer") or "watcher", body.get("op")
    if op == "undo":
        return P.undo(body.get("token"))
    res = P.verdict(writer, pid, op)
    if res is not None:
        return res
    _path, _doc, prop = P._find(writer, pid)
    if prop is None:
        return {"ok": False, "error": "that proposal is not there any more"}, 404
    if op not in ("keep", "dismiss"):
        raise ValueError("op must be keep, apply, dismiss or undo")
    if (prop.get("status") or "proposed") != "proposed":
        return {"ok": False, "error": "that one was already answered"}, 409
    out = {"ok": True, "id": pid, "writer": writer, "status": "kept" if op == "keep" else "dismissed"}
    if op == "keep" and prop.get("kind") in ("task", "stalled", "followup"):
        text = " ".join(str(prop.get("draft") or prop.get("title") or "").split())[:300]
        added = tasklist.call("add", {"section": "To-do", "text": text}) if text else {"error": "nothing to add"}
        if added.get("error"):
            return {"ok": False, "error": "the list did not take it: " + str(added["error"])}, 502
        out["task"] = text
    P._set_status(writer, pid, out["status"])
    P._invalidate()
    return out


def write_brief(q, body):
    """POST /brief {ids, pile?, name?}: turn a pile of cards into a brief. The vendored
    board/brief.py gathers the cards, asks the host model once (or writes a plain one when no
    model is there) and files it as a card in the same pile. The standalone server answers this
    from its artifacts route module, which the plugin does not carry (it also takes writes to
    the "made for you" registry from outside)."""
    b = _board.app().board
    body = body if isinstance(body, dict) else {}
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError("ids must be a list of block ids")
    import importlib
    brief = importlib.import_module("board.brief")
    res = brief.write(ids, b.build_board(), pile=body.get("pile"), name=body.get("name"))
    if res.get("ok"):
        res.pop("path", None)
        b.invalidate("artifacts")
    return res


# Routes the canvas calls that the standalone server answers from modules the plugin does not
# carry. The plugin answers them itself, from the vendored board package.
OWN_ROUTES = {("POST", "/brain/proposal"): proposal_verdict, ("POST", "/brief"): write_brief}


def _ok(res):
    return None if not isinstance(res, dict) or res.get("error") else res


def routes() -> dict:
    global _routes
    with _lock:
        if _routes is None:
            _routes = {**_load_routes(), **OWN_ROUTES}
        return _routes


def dispatch(method: str, path: str, query: dict | None = None, body=None):
    """Run one canvas route. -> (json, status). Never raises."""
    path = "/" + path.strip("/")
    path = ALIASES.get(path, path)
    if path == "/routes":
        return {"routes": sorted(f"{m} {p}" for m, p in routes()), "errors": _route_errors,
                "desktop_attached": RELAY.attached()}, 200
    fn = routes().get((method.upper(), path))
    if fn is None:
        return {"error": f"no route {method.upper()} {path}"}, 404
    try:
        res = fn(dict(query or {}), body)
        return res if isinstance(res, tuple) else (res, 200)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:400]}, 500
