"""proposals: ONEGUY_HOME/proposals/<writer>.json (contract format). Any writer: the watcher
and the retro (crew), or a source inside app/board (put() below; no crew needed).
status 'proposed' -> a proposal block. Actions emit POST /api/brain/proposal {id, writer, op}.

PREVIEW BEFORE YES. A proposal that changes the board carries its edit set:

    "edits": [{"op": "move", "id": "<block id>", "to_pile": "project:x" | "pile:<slug>" | "cluster:loose"},
              {"op": "park", "id": "<block id>"},
              {"op": "schedule", "id": "task:…", "when": "YYYY-MM-DD[THH:MM]"},
              {"op": "done", "id": "task:…"}]

The block shows them in detail.edits, so the page can ghost the result before the user says yes.
verdict(writer, id, "keep") applies them (each one a line in the judgment ledger) and hands back
an undo token; undo(token) takes every one of them back. A proposal WITHOUT edits is not ours to
judge: verdict() returns None and the caller does what it always did.

Where the state lives: done / schedule go through the task action layer (the same calls the
done verb and a drop on the Time lens make). move / park are the user's placements, which the page owns
(per-device view state), so verdict() returns them as `placements` for the page to put in its
store; the ledger line is written here either way.
"""
import glob
import os
import re
import threading
import time
import uuid

from . import actions, ledger, util
from .block import make, SURFACE

TTL = 10.0
EDIT_OPS = ("move", "park", "schedule", "done")
MAX_EDITS = 60
UNDO_TTL = 120
_WRITER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_BID_RE = re.compile(r"^[A-Za-z0-9_.:@/#+^-]{1,200}$")
_PILE_RE = re.compile(r"^(project|pile|cluster):[A-Za-z0-9_.-]{1,80}$")
_KIND = {"move": "cluster", "park": "park", "schedule": "schedule", "done": "done"}   # edit op -> ledger kind
_UNDOS = {}                     # token -> {writer, id, inverse, applied, at}  (memory only, like talk's)
_LOCK = threading.Lock()


def _post(pid, writer, op):
    return {"id": pid, "writer": writer, "op": op}


def clean_edits(raw):
    """The edits we can both show and carry out. Anything malformed is left out."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or e.get("op") not in EDIT_OPS:
            continue
        bid = str(e.get("id") or "")
        if not _BID_RE.match(bid):
            continue
        x = {"op": e["op"], "id": bid}
        if x["op"] == "move":
            if not _PILE_RE.match(str(e.get("to_pile") or "")):
                continue
            x["to_pile"] = str(e["to_pile"])
        elif x["op"] in ("schedule", "done"):
            if not bid.startswith("task:"):
                continue                               # only to-dos can be ticked or put on the calendar
            if x["op"] == "schedule":
                if not actions._DUE_RE.match(str(e.get("when") or "")):
                    continue
                x["when"] = str(e["when"])
        out.append(x)
    return out[:MAX_EDITS]


def verb(edits):
    """The words on the card's one button."""
    ops = {e["op"] for e in edits}
    one = len(edits) == 1
    if ops == {"move"}:
        return "move it" if one else "move them"
    if ops == {"park"}:
        return "put it away" if one else "put them away"
    if ops == {"schedule"}:
        return "schedule it" if one else "schedule them"
    if ops == {"done"}:
        return "mark it done" if one else "mark them done"
    return "do it"


def words(edits):
    """What a yes did, for the undo pill: 'moved 3', 'moved 2, ticked 1'."""
    parts = []
    for op, w in (("move", "moved"), ("park", "put away"), ("schedule", "scheduled"), ("done", "ticked")):
        n = sum(1 for e in edits if e["op"] == op)
        if n:
            parts.append(f"{w} {n}")
    return ", ".join(parts) or "nothing to change"


def to_blocks(doc, writer_hint):
    out = []
    writer = (doc or {}).get("writer") or writer_hint
    for p in (doc or {}).get("proposals") or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        if (p.get("status") or "proposed") != "proposed":
            continue
        pid = str(p["id"])
        kind = p.get("kind") or "task"
        edits = clean_edits(p.get("edits"))
        if edits:
            actions_ = [{"id": "keep", "label": verb(edits), "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "keep")},
                        {"id": "dismiss", "label": "not now", "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "dismiss")}]
        elif kind == "infra":
            actions_ = [dict(SURFACE, id="build", label="build it"),
                        {"id": "keep", "label": "keep", "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "keep")},
                        {"id": "dismiss", "label": "not now", "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "dismiss")}]
        else:
            actions_ = [{"id": "keep", "label": "keep", "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "keep")},
                        {"id": "dismiss", "label": "dismiss", "post": "/api/brain/proposal",
                         "body": _post(pid, writer, "dismiss")},
                        dict(SURFACE)]
        ev = []
        for e in p.get("evidence") or []:
            if isinstance(e, dict):
                ev.append({"type": e.get("type"), "ref": e.get("ref"),
                           "quote": util.safe_excerpt(e.get("quote"), 300)})
        links = [x for x in p.get("links") or [] if isinstance(x, str)]
        project = next((x for x in links if x.startswith("project:")), None)
        out.append(make(
            "proposal", pid, util.redact(p.get("title") or pid),
            summary=util.redact(p.get("summary") or p.get("why") or ""),
            status="proposed", tags=[t for t in [writer, kind] if t], project=project,
            updated=util.iso(util.parse_ts(p.get("updated") or p.get("created"))),
            source_type="proposal", links=links, actions=actions_,
            detail={"why": util.safe_excerpt(p.get("why"), 600), "evidence": ev,
                    "draft": util.redact(p.get("draft") or "")[:4000] or None, "writer": writer,
                    "created": p.get("created"), "kind": kind},
            match={"tags": [], "text": (p.get("title") or "") + " " + (p.get("summary") or "")},
        ))
        out[-1]["source"]["ref"] = f"{writer}/{pid}"
        if edits:
            out[-1]["detail"]["edits"] = edits
    return out


def fetch():
    blocks, writers, bad = [], [], []
    for path in sorted(glob.glob(os.path.join(util.oneguy("proposals"), "*.json"))):
        writer = os.path.basename(path)[:-5]
        doc = util.read_json(path, None)
        if not isinstance(doc, dict):
            bad.append(writer)
            continue
        writers.append(writer)
        blocks.extend(to_blocks(doc, writer))
    res = {"blocks": blocks, "count": len(blocks), "writers": writers}
    if bad:
        res["unreadable"] = bad
    return res


# ---------------------------------------------------------------- writing, from inside app/board
def _file(writer):
    if not _WRITER_RE.match(str(writer or "")):
        raise ValueError("bad writer")
    return util.oneguy("proposals", writer + ".json")


def _invalidate():
    try:
        from . import invalidate
        invalidate("proposals")
        invalidate("tasks")
    except Exception:
        pass


def put(writer, items, replace_open=False):
    """A board-side source (untangle, …) proposes: merge `items` by id into its own file under
    ONEGUY_HOME. An id the user already kept or dismissed is never brought back. replace_open: `items` is
    the writer's whole current say, so its unanswered proposals that aren't in it are withdrawn."""
    path = _file(writer)
    doc = util.read_json(path, None)
    doc = doc if isinstance(doc, dict) else {}
    have = {str(p.get("id")): p for p in doc.get("proposals") or [] if isinstance(p, dict) and p.get("id")}
    if replace_open:
        said = {str(it.get("id")) for it in items or [] if isinstance(it, dict)}
        have = {k: p for k, p in have.items() if k in said or (p.get("status") or "proposed") != "proposed"}
    for it in items or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        old = have.get(str(it["id"]))
        if old and (old.get("status") or "proposed") != "proposed":
            continue
        have[str(it["id"])] = dict(it, status="proposed",
                                   created=(old or it).get("created") or util.iso(util.now()))
    doc.update(writer=writer, updated=util.iso(util.now()), proposals=list(have.values()))
    util.write_json_atomic(path, doc)
    _invalidate()
    return doc


def _find(writer, pid):
    path = _file(writer)
    doc = util.read_json(path, None)
    if isinstance(doc, dict):
        for p in doc.get("proposals") or []:
            if isinstance(p, dict) and str(p.get("id")) == str(pid):
                return path, doc, p
    return path, doc, None


def _set_status(writer, pid, status):
    path, doc, p = _find(writer, pid)
    if p is None:
        return False
    p["status"] = status
    p["decided"] = util.iso(util.now())
    util.write_json_atomic(path, doc)
    return True


# ---------------------------------------------------------------- carrying out an edit set
def _io(post, get):
    api = util.CFG["tasklist_api"].rstrip("/")
    return (post or (lambda route, payload: util.http_json(api + "/" + route, payload, timeout=8)),
            get or (lambda: util.http_json(api + "/tasks", timeout=4)))


def _task(get, bid):
    ref = bid[5:]
    by = (lambda s, it: it.get("anchor") == ref) if ref.startswith("^") else (lambda s, it: it.get("id") == ref)
    return actions._find((get() or {}).get("sections"), by)


def _run(body, post, get):
    """One task op through the action layer -> its result. RuntimeError when it didn't take."""
    try:
        if body["op"] == "schedule":
            res, _st = actions.schedule(body, post, get)
        else:
            res, _st = actions.task_action(body, post)
    except (util.HttpError, OSError, ValueError) as e:
        raise RuntimeError(str(e)[:200])
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "the list said no")
    return res


def apply_edits(edits, post=None, get=None):
    """Carry out an edit set -> (applied, placements, inverse). All or nothing: when one edit
    fails, the ones before it are taken back and RuntimeError says why."""
    post, get = _io(post, get)
    applied, placements, inverse = [], {}, []
    try:
        for e in edits:
            a = dict(e)
            if e["op"] == "move":
                placements[e["id"]] = {"cluster": e["to_pile"], "parked": False}
            elif e["op"] == "park":
                placements[e["id"]] = {"parked": True}
            elif e["op"] == "done":
                try:
                    it = _task(get, e["id"])
                except (util.HttpError, OSError) as err:
                    raise RuntimeError(str(err)[:200])
                if it is None:
                    raise RuntimeError("one of these isn't on the list any more")
                if it.get("done"):
                    a["noop"] = True                   # already ticked: nothing to do, nothing to undo
                else:
                    _run({"op": "toggle", "id": it["id"]}, post, get)
                    inverse.append({"op": "toggle", "id": it["id"]})
            elif e["op"] == "schedule":
                res = _run({"op": "schedule", "id": e["id"], "due": e["when"]}, post, get)
                a["block_id"] = res.get("block_id") or e["id"]
                inverse.append({"op": "schedule", "id": a["block_id"], "due": res.get("was")})
            applied.append(a)
    except RuntimeError:
        revert(inverse, post, get)
        raise
    return applied, placements, inverse


def revert(inverse, post=None, get=None):
    """Run the inverse task ops, newest first -> how many didn't take."""
    post, get = _io(post, get)
    bad = 0
    for op in reversed(inverse):
        try:
            _run(dict(op), post, get)
        except RuntimeError:
            bad += 1
    return bad


def verdict(writer, pid, op, post=None, get=None):
    """The user's answer to an edit-carrying proposal. None = this proposal has no edits (not ours).
    keep|apply -> the edits carried out, one ledger line each, status 'kept', an undo token.
    dismiss    -> status 'dismissed'."""
    _path, _doc, p = _find(writer, pid)
    if p is None:
        return None
    edits = clean_edits(p.get("edits"))
    if not edits:
        return None
    if (p.get("status") or "proposed") != "proposed":
        return {"ok": False, "error": "that one was already answered"}, 409
    if op == "dismiss":
        _set_status(writer, pid, "dismissed")
        _invalidate()
        return {"ok": True, "id": pid, "writer": writer, "status": "dismissed"}
    if op not in ("keep", "apply"):
        raise ValueError("op must be keep, apply, dismiss or undo")
    try:
        applied, placements, inverse = apply_edits(edits, post, get)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}, 502      # nothing was changed
    note = f"proposal:{writer}/{pid}"
    for a in applied:
        if not a.get("noop"):
            ledger.append(_KIND[a["op"]], a.get("block_id") or a["id"], a.get("to_pile") or a.get("when"), note)
    _set_status(writer, pid, "kept")
    token = "p_" + uuid.uuid4().hex[:10]
    with _LOCK:
        now = time.time()
        for k in [k for k, v in _UNDOS.items() if now - v["at"] > UNDO_TTL]:
            _UNDOS.pop(k, None)
        _UNDOS[token] = {"writer": writer, "id": pid, "inverse": inverse, "applied": applied, "at": now}
    _invalidate()
    return {"ok": True, "id": pid, "writer": writer, "status": "kept", "applied": applied,
            "placements": placements, "summary": words(edits),
            "undo": {"token": token, "label": "undo", "post": "/api/brain/proposal",
                     "body": {"op": "undo", "token": token}}}


def undo(token, post=None, get=None):
    """Take back everything one yes did: the task edits, the proposal's status (it is proposed
    again), and a ledger line per edit. The page puts its own placements back."""
    with _LOCK:
        rec = _UNDOS.pop(str(token or ""), None)
    if not rec or time.time() - rec["at"] > UNDO_TTL:
        return {"ok": False, "error": "undo window closed"}, 404
    bad = revert(rec["inverse"], post, get)
    note = f"proposal:{rec['writer']}/{rec['id']}"
    for a in rec["applied"]:
        if not a.get("noop"):
            ledger.append("undo", a.get("block_id") or a["id"], _KIND[a["op"]], note)
    _set_status(rec["writer"], rec["id"], "proposed")
    _invalidate()
    out = {"ok": bad == 0, "id": rec["id"], "writer": rec["writer"], "status": "proposed",
           "undone": words(rec["applied"]), "ids": [a["id"] for a in rec["applied"]]}
    if bad:
        out["error"] = "some of it couldn't be put back on the list"
    return out
