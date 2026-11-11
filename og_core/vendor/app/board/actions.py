"""Write actions behind the board.

POST /api/board/task {id, op, ...} proxies to the tasklist API (never the file):
  toggle {id}                       -> /toggle
  edit   {id, text|note, tags?, due?, priority?}  -> /edit (only fields sent)
  move   {id, section, index?=0}    -> /move
  add    {text|note, section?='To-do', tags?, due?, priority?, origin?} -> /add
  delete {id}                       -> /delete
  schedule {id, due: 'YYYY-MM-DD' | 'YYYY-MM-DDTHH:MM' | null}  -> /anchor (once) + /edit
         a drop on the Time lens (card-grammar §6). The item is anchored first so its card keeps
         its identity (task:^xxxx) across the edit; due null unschedules it.
POST /api/board/job {job_id, op:'run'} -> `hermes cron run <id>` in the
background (it runs synchronously and can take minutes), logged to
~/.hermes/1guy/logs/board-job-<id>-<ts>.log.
"""
import os
import re
import subprocess
import time

from . import util

TASK_OPS = ("toggle", "edit", "move", "add", "delete", "schedule")
_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
_ANCHOR_RE = re.compile(r"^\^[a-z0-9]{3,12}$")
DEFAULT_ORIGIN = "1guy home"
_ID_RE = re.compile(r"^[0-9a-f]{6,40}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _task_id(body):
    tid = str(body.get("id") or "").strip()
    if tid.startswith("task:"):
        tid = tid[5:]
    if not _ID_RE.match(tid):
        raise ValueError("need a tasklist item id (10 hex chars, optionally 'task:' prefixed)")
    return tid


def _clean(text):
    """Item text without inline #tag / @due / !pN tokens (what /tasks returns)."""
    t = re.sub(r"(?<!\S)(#[\w-]+|@\S+|![pP][1-3])(?=\s|$)", " ", text or "")
    return re.sub(r"\s+", " ", t).strip()


def _find(sections, pred):
    for s in sections or []:
        for it in s.get("items") or []:
            if pred(s, it):
                return dict(it, section=s.get("name"))
    return None


def task_action(body, post=None):
    """Returns ({ok, op, ...}, status). `post(route, payload)` is the HTTP call
    (injectable for tests)."""
    post = post or (lambda route, payload: util.http_json(
        util.CFG["tasklist_api"].rstrip("/") + "/" + route, payload, timeout=8))
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    op = body.get("op")
    if op not in TASK_OPS:
        raise ValueError(f"op must be one of {', '.join(TASK_OPS)}")
    text = body.get("text") if body.get("text") is not None else body.get("note")
    if op == "schedule":
        return schedule(body, post)
    if op == "add":
        if not (text or "").strip():
            raise ValueError("add needs text")
        payload = {"section": (body.get("section") or "To-do").strip() or "To-do",
                   "text": str(text).strip(), "origin": body.get("origin") or DEFAULT_ORIGIN}
        for k in ("tags", "due", "priority"):
            if body.get(k) is not None:
                payload[k] = body[k]
        route = "add"
    else:
        tid = _task_id(body)
        payload = {"id": tid}
        route = op
        if op == "edit":
            if not (text or "").strip():
                raise ValueError("edit needs text (or note)")
            payload["text"] = str(text).strip()
            for k in ("tags", "due", "priority"):
                if k in body:
                    payload[k] = body[k]
        elif op == "move":
            if not body.get("section"):
                raise ValueError("move needs section")
            payload["section"] = body["section"]
            payload["index"] = int(body.get("index") or 0)
    try:
        res = post(route, payload) or {}
    except util.HttpError as e:
        return {"ok": False, "op": op, "error": f"tasklist {e}"}, (e.status if 400 <= e.status < 600 else 502)
    out = {"ok": bool(res.get("ok", True)), "op": op}
    secs = res.get("sections")
    if op == "add":
        want = _clean(payload["text"])
        hits = [it for s in secs or [] if (s.get("name") or "").lower() == payload["section"].lower()
                for it in s.get("items") or [] if _clean(it.get("text") or "") == want]
        if hits:
            out["id"] = hits[-1]["id"]
            out["block_id"] = "task:" + hits[-1]["id"]
        elif out["ok"] and isinstance(res.get("id") or (res.get("item") or {}).get("id"), str):
            # a tasklist that answers with the new item instead of the whole list (door B needs the
            # card's id back to open it; without one the client matches the card by its words)
            out["id"] = res.get("id") or res["item"]["id"]
            out["block_id"] = "task:" + out["id"]
    else:
        out["id"] = payload["id"]
        if op != "delete":
            # content-hash ids change on edit/move; toggle keeps the line text but
            # the id is hash(section, raw line) so it survives a toggle
            same = _find(secs, lambda s, it: it.get("id") == payload["id"])
            if same:
                out["item"] = {"id": same["id"], "done": same.get("done"), "section": same["section"]}
            elif op in ("edit", "move"):
                out["note"] = "item id changed (ids are content hashes); refresh the board"
        else:
            out["removed"] = (res.get("removed") or {}).get("text") is not None
    return out, 200


def schedule(body, post, get=None):
    """Time is a place: set (or clear) an item's when. Refetches between mutations because
    tasklist ids are content hashes (the anchor and the new due both change the line)."""
    get = get or (lambda: util.http_json(util.CFG["tasklist_api"].rstrip("/") + "/tasks", timeout=4))
    due = body.get("due") or None
    if due is not None and not _DUE_RE.match(str(due)):
        raise ValueError("due must be YYYY-MM-DD or YYYY-MM-DDTHH:MM (or null to unschedule)")
    ref = str(body.get("id") or "").strip()
    ref = ref[5:] if ref.startswith("task:") else ref
    if not (_ANCHOR_RE.match(ref) or _ID_RE.match(ref)):
        raise ValueError("need a task id (task:^anchor or task:<hash>)")
    by = (lambda s, it: it.get("anchor") == ref) if ref.startswith("^") else (lambda s, it: it.get("id") == ref)
    try:
        it = _find((get() or {}).get("sections"), by)
        if it is None:
            return {"ok": False, "op": "schedule", "error": "that item isn't on the list any more"}, 404
        anchor = it.get("anchor")
        if not anchor:
            a = post("anchor", {"id": it["id"]}) or {}
            anchor = a.get("anchor")
            it = _find((get() or {}).get("sections"), lambda s, x: x.get("anchor") == anchor) or it
        res = post("edit", {"id": it["id"], "text": it.get("text") or "", "due": due or ""}) or {}
    except util.HttpError as e:
        return {"ok": False, "op": "schedule", "error": f"tasklist {e}"}, (e.status if 400 <= e.status < 600 else 502)
    if res.get("ok") is False:
        return {"ok": False, "op": "schedule", "error": res.get("error") or "the list said no"}, 502
    key = "task:" + anchor if anchor else "task:" + it["id"]
    return {"ok": True, "op": "schedule", "block_id": key, "from_id": "task:" + ref, "due": due,
            "was": (it.get("due") or "") + ("T" + it["due_time"] if it.get("due") and it.get("due_time") else "") or None}, 200


def job_action(body, jobs_loader=None, spawn=None):
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    op = body.get("op")
    if op != "run":
        raise ValueError("op must be 'run'")
    jid = str(body.get("job_id") or "").strip()
    if jid.startswith("job:"):
        jid = jid[4:]
    if not _JOB_RE.match(jid):
        raise ValueError("need job_id")
    from . import jobs as _jobs
    jl = (jobs_loader or _jobs.load_jobs)()
    job = next((j for j in jl if j.get("id") == jid), None)
    if job is None:
        return {"ok": False, "error": f"no cron job {jid}"}, 404
    if not job.get("enabled", True) or job.get("paused_at") or job.get("state") == "paused":
        return {"ok": False, "error": f"job {job.get('name') or jid} is paused; resume it first"}, 409
    os.makedirs(util.CFG["logs_dir"], exist_ok=True)
    log = os.path.join(util.CFG["logs_dir"], f"board-job-{jid}-{time.strftime('%Y%m%d-%H%M%S')}.log")
    cmd = ["hermes", "cron", "run", jid]
    if spawn:
        pid = spawn(cmd, log)
    else:
        with open(log, "ab") as fh:
            env = dict(os.environ, HERMES_HOME=os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes"))
            p = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 start_new_session=True, env=env, cwd=os.path.expanduser("~"))
            pid = p.pid
    return {"ok": True, "op": "run", "job_id": jid, "name": job.get("name"), "started": True,
            "pid": pid, "log": log.replace(os.path.expanduser("~"), "~")}, 200
