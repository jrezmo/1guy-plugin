"""POST /api/talk — the adaptive talk bar's endpoint (Phase 2, unified audit §5.2).

  POST /api/talk        {text, focus?, guess?, view?}    -> shape-hinted reply (see talk_resolve)
                        view = what the user is looking at (core.js viewContext()): the router uses it
                        to resolve "this" / "these", the thread gets one plain line of it, and
                        the layout problems the page measured go to view-issues.jsonl
                        view.selected with two or more cards (a lasso) and no focus: a conversation
                        line opens a NEW thread about those cards (board/selection.py) ->
                        route {kind: "selection", key, run_id, title, ids, names}. The line is
                        already sent: the page follows run_id and must not send it again
  POST /api/talk/claim  {text, focus?, view?}            -> {verb, label, ids}: the cards this line is about and
                        the -ing word for the frame (board/claim.py). Instant: no model, no thread.
  POST /api/talk/do     {token}                          -> execute a confirmed risky op (landed + undo)
  POST /api/talk/undo   {token}                          -> roll a landed change back
  GET  /api/talk/history?limit=N                         -> the last landed changes (files, not chat)

Command shape: the action layer runs the change (board task/job actions) — no Hermes session,
no agent turn (audit: "It's instant and cheap"). Conversation shape: the thread is resolved from
threads.json (resolve-or-start happens client-side through /api/surface/*, the same runner the
bloom uses). Every landed change is appended to ~/.hermes/1guy/talk-history.json and carries an
undo token (memory-only, 120s window — real undo is the tasklist's own history).
"""
import os
import re
import threading
import time
import uuid

import talk_resolve as R            # app/talk_resolve.py (app/ is on sys.path under serve.py)

_UNDOS = {}                          # token -> {ops:[{route,payload,label}], at, summary}
_UNDOS_LOCK = threading.Lock()
_PENDING = {}                        # confirm token -> {op, args, target}
_UNDO_TTL = 120


def _board():
    import board as _board           # app/board/
    return _board


def _faces(board=None):
    """The board's item faces for target matching (cached sources; cheap)."""
    b = board or _board().build_board()
    out = []
    for blk in b.get("blocks") or []:
        if blk.get("kind") == "project":
            continue
        out.append({"id": blk.get("id"), "title": blk.get("title"),
                    "kind": blk.get("kind"), "status": blk.get("status"),
                    "headline": blk.get("headline") or {}})
    return out


def _jobs():
    try:
        from board import jobs as _jobs
        res = _jobs.load_jobs() if hasattr(_jobs, "load_jobs") else (_jobs.fetch() or {}).get("jobs", [])
        return res or []
    except Exception:
        return []


def _task(body, post=None):
    from board import actions as _actions
    res, status = _actions.task_action(body, post=post)
    _board().invalidate("tasks")
    return res, status


def _store_undo(ops, summary):
    token = "u_" + uuid.uuid4().hex[:10]
    with _UNDOS_LOCK:
        now = time.time()
        for k in [k for k, v in _UNDOS.items() if now - v["at"] > _UNDO_TTL]:
            _UNDOS.pop(k, None)
        _UNDOS[token] = {"ops": ops, "at": now, "summary": summary}
    return token


def _tasklist_id(ref):
    """A stable anchor ('^ab12') is not a tasklist id: find the item's hash id.
    Returns None when the tasklist can't be reached or the anchor isn't there."""
    ref = str(ref or "")
    if not ref.startswith("^"):
        return ref
    try:
        from board import tasks as _tasks
        for sec in ((_tasks.fetch() or {}).get("sections") or []):
            for it in sec.get("items") or []:
                if it.get("anchor") == ref:
                    return it["id"]
    except Exception:
        pass
    return None


def _execute(args, post=None):
    """Run a task op through the action layer. -> (result, undo_ops|None, summary)"""
    op = args.get("op")
    args = dict(args)
    if args.get("id"):
        args["id"] = _tasklist_id(args["id"])
        if not args["id"]:
            return {"ok": False, "error": "couldn't find that item on the tasklist"}, None, ""
    if op == "add":
        res, status = _task({"op": "add", "text": args.get("text"), "origin": "1guy talk bar"}, post=post)
        undo = [{"route": "/api/board/task", "payload": {"op": "delete", "id": res.get("id")}}] if res.get("ok") and res.get("id") else None
        return res, undo, f"added “{(args.get('text') or '')[:48]}” to my list"
    if op == "toggle":
        res, status = _task({"op": "toggle", "id": args.get("id")}, post=post)
        item = res.get("item") or {}
        undo = [{"route": "/api/board/task", "payload": {"op": "toggle", "id": args.get("id")}}] if res.get("ok") else None
        return res, undo, ("ticked " if item.get("done") else "re-opened ") + "“" + str(item.get("id") or args.get("id"))[:40] + "”"
    if op == "delete":
        res, status = _task({"op": "delete", "id": args.get("id")}, post=post)
        return res, None, "deleted it"
    if op == "schedule" or (op == "edit" and args.get("due") and not args.get("section")):
        # time is a place (lane D): the same op as a drop on the Time lens — anchor, due, judgment, undo
        bid = args.get("id") if str(args.get("id") or "").startswith(("task:", "^")) else "task:" + str(args.get("id"))
        res, status = _task({"op": "schedule", "id": bid, "due": args.get("due")}, post=post)
        if not res.get("ok"):
            return res, None, ""
        _judge("schedule", res.get("block_id"), args.get("due"))
        undo = [{"route": "/api/board/task", "payload": {"op": "schedule", "id": res.get("block_id"), "due": res.get("was")}}]
        return res, undo, "on for " + _slot_words(args.get("due"))
    if op == "edit":
        payload = {"op": "edit", "id": args.get("id")}
        prev = {}
        undo = None
        if args.get("due"):
            payload["due"] = args["due"]
        if args.get("section"):
            payload["section"] = args["section"]
            prev = {"op": "move", "id": args.get("id")}
        elif args.get("due"):
            prev = {"op": "edit", "id": args.get("id")}
        res, status = _task(payload, post=post)
        if res.get("ok") and prev:
            item = res.get("item") or {}
            undo = [{"route": "/api/board/task", "payload": dict(prev)}]
            if args.get("section"):
                undo[0]["payload"]["section"] = item.get("section") or "To-do"
        return res, undo, ("moved it to " + str(args.get("section") or args.get("due")))
    raise ValueError("unknown op " + str(op))


def _judge(kind, block, target):
    """The talk bar's changes are judgments too (the same ledger as drags)."""
    try:
        import importlib
        importlib.import_module("routes.judgments").post_judgment({}, {"kind": kind, "block": block, "target": target, "note": "talk bar"})
    except Exception:   # noqa: BLE001 - the ledger never blocks the change
        pass


def _slot_words(at):
    """'2026-11-12T14:00' -> 'thu 12 · 2 pm' (the drag's own words)."""
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return str(at or "")
    today = dt.date.today()
    day = "today" if d.date() == today else "tomorrow" if d.date() == today + dt.timedelta(days=1) else d.strftime("%a ").lower() + str(d.day)
    if len(at) == 10:
        return day + " · any time"
    return day + " · " + d.strftime("%I:%M %p").lstrip("0").lower().replace(":00", "")


def _threads_routes():
    """The live /api/threads/* module (surface runner). Routes load alphabetically,
    so at request time it is either already in sys.modules or loaded standalone."""
    import importlib.util
    import sys
    mod = sys.modules.get("oneguy_routes_threads")
    if mod is not None:
        return mod
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "routes", "threads.py")
    spec = importlib.util.spec_from_file_location("oneguy_routes_threads", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oneguy_routes_threads"] = mod
    spec.loader.exec_module(mod)
    return mod


def _tasklist_base():
    return os.environ.get("ONEGUY_TASKLIST_API", "http://oneguy.invalid").rstrip("/")


def _tasklist_post(path, payload, timeout=8):
    """POST to the tasklist API -> parsed dict, or None on any failure."""
    try:
        import json as _json
        import urllib.request as _ur
        req = _ur.Request(_tasklist_base() + path, data=_json.dumps(payload).encode(),
                          method="POST", headers={"Content-Type": "application/json",
                                                  "User-Agent": "1guy-router/1"})
        with _ur.urlopen(req, timeout=timeout) as r:
            return _json.load(r)
    except Exception:
        return None


def _tasklist_titles():
    """Current tasklist titles (plain faces: provenance stripped) for router dedupe."""
    try:
        import json as _json
        import urllib.request as _ur
        from board import tasks as _tasks
        secs = (_tasks.fetch() or {}).get("sections") or []
        out = []
        for sec in secs:
            for it in sec.get("items") or []:
                if it.get("done"):
                    continue
                t = re.sub(r"\s*\(from: [^)]*\)", "", str(it.get("text") or "")).strip()
                if t:
                    out.append(t[:160])
        if out:
            return out
    except Exception:
        pass
    try:
        import json as _json
        import urllib.request as _ur
        with _ur.urlopen(_tasklist_base() + "/t/tasks", timeout=5) as r:
            data = _json.load(r)
        out = []
        for sec in (data or {}).get("sections") or []:
            for it in sec.get("items") or []:
                if it.get("done"):
                    continue
                t = re.sub(r"\s*\(from: [^)]*\)", "", str(it.get("text") or "")).strip()
                if t:
                    out.append(t[:160])
        return out
    except Exception:
        return []


_FROM_TAG = re.compile(r"\s*\(from: [^)]*\)")

_CLASSIFY = None   # test seam: overrides the router's LLM classify call when set


def _conversation_thread(key):
    """Resolve an existing key from threads.json (never writes)."""
    try:
        import threads as _threads
        k, rec = _threads.resolve(key)
        return {"key": k, "session_id": ((rec or {}).get("primary") or {}).get("session_id")}
    except Exception:
        return {"key": key, "session_id": None}


VIEW_ISSUES_KEEP = 200


def view_issues_path(home=None):
    return os.path.join(home or os.path.expanduser(os.environ.get("ONEGUY_HOME", "~/.hermes/1guy")),
                        "view-issues.jsonl")


def log_view_issues(view, home=None, now=None):
    """Layout problems the page measured (a clipped headline, two cards overlapping) -> a small
    ring file, newest last, at most VIEW_ISSUES_KEEP lines. One line per (screen, lens, card, kind):
    seeing it again moves it to the end and counts it, so one stubborn card can't flush the rest.
    Best-effort: a talk turn never fails on this. -> how many it logged."""
    issues = (view or {}).get("issues") or []
    if not issues:
        return 0
    import json as _json
    ident = lambda r: (r.get("screen"), r.get("lens"), r.get("id"), r.get("kind"), r.get("with"))
    try:
        p = view_issues_path(home)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rows = []
        try:
            with open(p, encoding="utf-8") as f:
                for ln in f:
                    try:
                        rows.append(_json.loads(ln))
                    except ValueError:
                        continue
        except OSError:
            pass
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now or time.time()))
        for it in issues:
            row = dict(at=at, screen=view.get("screen") or "", lens=view.get("lens"),
                       level=view.get("level"), focused=view.get("focused"),
                       id=it["id"], kind=it["kind"], n=1)
            if it.get("with"):
                row["with"] = it["with"]
            old = [r for r in rows if ident(r) == ident(row)]
            if old:
                row["n"] = int(old[-1].get("n") or 1) + 1
                row["first"] = old[-1].get("first") or old[-1].get("at")
                rows = [r for r in rows if ident(r) != ident(row)]
            rows.append(row)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows[-VIEW_ISSUES_KEEP:]:
                f.write(_json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
        return len(issues)
    except OSError:
        return 0


def _route_talk(res, text, focus, view=None):
    """Route a conversation utterance (docs/request-router.md). Fills res['route'] and
    res['thread']. Never raises; a router failure degrades to the old behavior."""
    import router as _router
    import threads as _threads
    faces = _faces()
    # a lasso: he picked several cards and asked one thing about all of them. That is a NEW thread
    # seeded with those cards (board/selection.py), never one card's thread, and never a to-do.
    from board import selection as _selection
    sel = None if focus else _selection.build((view or {}).get("selected"), faces)
    if sel:
        th = _open_thread("talk:" + uuid.uuid4().hex[:12], title=sel["title"], text=text, view=view,
                          block=sel["block"], linked=sel["linked"])
        res["route"] = {"kind": "selection", "key": th["key"], "session_id": th.get("session_id"),
                        "created": th.get("created"), "run_id": th.get("run_id"),
                        "title": sel["title"], "ids": sel["ids"], "names": sel["names"],
                        "refers": {"what": "selected", "ids": sel["ids"]}}
        res["thread"] = {"key": th["key"], "session_id": th.get("session_id")}
        return res
    decision = _router.route(text, focus=focus, faces=faces, task_titles=_tasklist_titles(),
                             classify=_CLASSIFY, view=view)
    kind, key = decision["route"], decision.get("key")
    out = {"kind": kind, "key": key}
    if decision.get("refers"):
        out["refers"] = decision["refers"]

    if kind == "block":
        th = _conversation_thread(key)
    elif kind == "todo":
        # new tasklist item, then anchor it; the thread lives on the stable task:^xxxx key
        talk_key = "talk:" + uuid.uuid4().hex[:12]
        added = _tasklist_post("/t/add", {"section": "To-do", "text": decision["todo_text"],
                                          "origin": f"1guy talk bar · {talk_key}"})
        item_id = (added or {}).get("id")
        anchored = _tasklist_post("/t/anchor", {"id": item_id}) if item_id else None
        anchor = str((anchored or {}).get("anchor") or "").strip()
        key = f"task:{anchor}" if anchor.startswith("^") else talk_key
        th = _open_thread(key, aliases=[talk_key] if key != talk_key else [],
                          title=decision["title"], text=text, view=view)
        _board().invalidate("tasks")
        out["todo_id"] = item_id
    elif kind == "new":
        key = "talk:" + uuid.uuid4().hex[:12]
        th = _open_thread(key, title=decision["title"], text=text, view=view)
    else:                                   # home: only genuine small talk lands here
        th = _conversation_thread("home")

    out.update({"key": th["key"], "session_id": th.get("session_id")})
    if kind in ("todo", "new"):
        try:
            rec = _threads.resolve(th["key"])[1]
            out["created"] = th.get("created")
            out["title"] = (rec or {}).get("title") or decision["title"]
        except Exception:
            pass
    res["route"] = out
    res["thread"] = {"key": th["key"], "session_id": th.get("session_id")}
    return res


def _open_thread(key, title=None, text="", aliases=(), view=None, block=None, linked=None):
    """resolve-or-start through the surface runner (/api/threads/open path). `view` rides along
    so the seed turn tells the thread what the user was looking at when he said it; `block` / `linked`
    are a selection's subject and its cards (board/selection.py)."""
    try:
        opened = _threads_routes().r_open({}, {
            "key": key, "aliases": aliases, "title": title, "seed": text,
            "opened_from": "talk-router",
            "block": block or {"kind": "talk", "title": title or ""},
            "view": view, "linked": linked,
        })
        return {"key": opened.get("key") or key, "session_id": opened.get("session_id"),
                "created": opened.get("created"), "run_id": opened.get("run_id")}
    except Exception:
        return _conversation_thread(key)


def r_talk(q, body):
    body = body or {}
    text = str(body.get("text") or "").strip()
    if not text or len(text) > 2000:
        return {"shape": "conversation", "error": "need 1-2000 chars of text"}, 400
    focus = body.get("focus") or None
    guess = body.get("guess") or None
    import router as _router
    view = _router.clean_view(body.get("view"))      # None = a client that doesn't send it (yet)
    log_view_issues(view)
    res = R.resolve(text, focus=focus, items=_faces(), jobs=_jobs())
    # the user's tap-to-flip guess wins when it contradicts a conversation resolve (a human override
    # of the glyph), but never promotes a confirm/landed the resolver refused.
    if res.get("shape") == "conversation" and guess == "command":
        res["override"] = "guess-command"
        res["shape"] = "conversation"     # still conversation: the resolver found no target (D10)
    if res.get("shape") == "show" and body.get("show") is False:
        res = {"shape": "conversation", "reason": "show-declined"}
    shape = res.get("shape")

    if shape == "landed-pending":
        if res["args"]["op"] in R.RISKY_OPS:
            token = "c_" + uuid.uuid4().hex[:10]
            with _UNDOS_LOCK:
                _PENDING[token] = {"res": res, "at": time.time()}
            res["shape"] = "confirm"
            res["token"] = token
            res["question"] = res["args"]["op"].replace("delete", "delete") + " — tap the target to confirm"
            return res
        try:
            result, undo_ops, summary = _execute(res["args"])
        except ValueError as e:
            res.update({"shape": "failed", "error": str(e)})
            return res
        if not result.get("ok", True):
            res.update({"shape": "failed", "error": result.get("error") or "the change didn't land"})
            return res
        out = {"shape": "landed", "target": res.get("target"), "op": res.get("op"), "args": res.get("args"),
               "result": result, "summary": summary}
        if undo_ops:
            out["undo"] = {"token": _store_undo(undo_ops, summary), "label": "undo"}
        R.record_history({"at": time.time(), "by": "the talk bar", "text": text, "op": res.get("op"),
                          "args": res.get("args"), "summary": summary})
        return out

    if shape == "confirm":
        res.setdefault("question", "tap the target to confirm")
        return res

    if shape == "conversation":
        # the request router decides where this line lives (docs/request-router.md);
        # thread key comes from the route, never a bare focus-or-home fallback
        try:
            _route_talk(res, text, focus, view)
        except Exception:
            key = focus or "home"
            th = _conversation_thread(key)
            res["thread"] = th
            res["route"] = {"kind": "new" if not focus else "block", "key": th["key"],
                            "session_id": th.get("session_id")}
        return res

    return res


def r_claim(q, body):
    """Thin shim: the logic is app/board/claim.py (vendored into the plugin)."""
    from board import claim as _claim
    import router as _router
    body = body or {}
    text = str(body.get("text") or "").strip()[:2000]
    try:
        blocks = _board().build_board().get("blocks") or []
    except Exception:   # noqa: BLE001 - no board, no frame; the turn itself is unaffected
        blocks = []
    return _claim.build(text, blocks, focus=body.get("focus") or None, view=_router.clean_view(body.get("view")))


def r_do(q, body):
    """Execute a confirmed risky op (the user tapped the lit target)."""
    token = str((body or {}).get("token") or "")
    with _UNDOS_LOCK:
        pend = _PENDING.pop(token, None)
        for k in [k for k, v in list(_PENDING.items()) if time.time() - v["at"] > _UNDO_TTL]:
            _PENDING.pop(k, None)
    if not pend:
        return {"ok": False, "error": "confirm expired; say it again"}, 404
    res = pend["res"]
    try:
        result, undo_ops, summary = _execute(res["args"])
    except ValueError as e:
        return {"shape": "failed", "error": str(e)}, 200
    if not result.get("ok", True):
        return {"shape": "failed", "error": result.get("error") or "the change didn't land"}, 200
    out = {"shape": "landed", "target": res.get("target"), "op": res.get("op"), "args": res.get("args"),
           "result": result, "summary": summary}
    if undo_ops:
        out["undo"] = {"token": _store_undo(undo_ops, summary), "label": "undo"}
    R.record_history({"at": time.time(), "by": "the talk bar (confirmed)", "op": res.get("op"),
                      "args": res.get("args"), "summary": summary})
    return out


def r_undo(q, body):
    token = str((body or {}).get("token") or "")
    with _UNDOS_LOCK:
        rec = _UNDOS.pop(token, None)
    if not rec:
        return {"ok": False, "error": "undo window closed"}, 404
    from board import actions as _actions
    results = []
    for op in rec["ops"]:
        res, _ = _actions.task_action(op["payload"])
        results.append(res)
    _board().invalidate("tasks")
    return {"ok": all(r.get("ok", True) for r in results), "undone": rec["summary"]}


def r_history(q, body):
    limit = min(max(int((q or {}).get("limit") or 20), 1), _UNDO_TTL)
    try:
        data = json_load_history()
        return {"history": list(reversed(data))[:limit]}
    except Exception:
        return {"history": []}


def json_load_history():
    import json as _json
    try:
        return _json.load(open(R.history_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return []


ROUTES = {
    ("POST", "/api/talk"): r_talk,
    ("POST", "/api/talk/claim"): r_claim,
    ("POST", "/api/talk/do"): r_do,
    ("POST", "/api/talk/undo"): r_undo,
    ("GET", "/api/talk/history"): r_history,
}
