"""feed: the ONE activity feed, "who is working on what, live" (unified audit §4.3, Phase 3).

Derived on read: no new event log. Four producers, each normalized to one event shape:

  subagent  delegate_task runs: ~/.hermes/cache/delegation/live/<deleg>/{manifest.json,task-N.log}
            + their `Subagent:` sessions in state.db (model, cost, last activity)
  surface   the surface engine's in-flight replies (routes/surface.py LIVE, in this process)
  run       tasklist ⤷ runs: ~/.hermes/tasklist-runs/<run>.{meta.json,jsonl}
  cron      1guy-* cron jobs: ~/.hermes/cron/jobs.json + executions.db + output/<id>/ (read-only)

Event (every producer):
  {id, actor, key|None, who: {engine, model}, what, now, where: {branch, worktree},
   started, last_move, status, live, session_id, deep_link, reason, elapsed_s, eta}
  status: running | quiet | completed | failed | interrupted | ended
  live:   True while the work is in flight (running or quiet)

Each event is keyed to the item key from threads.json where possible: an explicit
`item: <key>` token in a delegate goal, else the run's own session or its parent session
mapped back through threads.session_map(). runs.py (the work-in-flight page) reads this
feed: one source (D8).

Escalation (audit 9c): escalate(events) turns a FAILED run, or one quiet past 15 min, into
ONE tasklist item (section 'Open questions / issues', origin '1guy feed · <session>'),
deduped through ~/.hermes/1guy/feed-escalations.json. Live progress never goes in the list.
Stdlib only.
"""
import glob
import json
import os
import re
import sqlite3
import statistics
import sys
import threading
import time
import urllib.request

import threads as T

HOME = os.path.expanduser("~")
DELEG_LIVE = os.environ.get("ONEGUY_DELEG_LIVE", os.path.join(HOME, ".hermes/cache/delegation/live"))
STATE_DB = os.environ.get("ONEGUY_STATE_DB", os.path.join(HOME, ".hermes/state.db"))
TASKLIST_RUNS = os.environ.get("ONEGUY_TASKLIST_RUNS", os.path.join(HOME, ".hermes/tasklist-runs"))
HANDOFFS = os.environ.get("ONEGUY_HANDOFFS", os.path.join(HOME, ".hermes/tasklist-handoffs"))
CRON_DIR = os.environ.get("ONEGUY_CRON_DIR", os.path.join(HOME, ".hermes/cron"))
ESCALATE_URL = os.environ.get("ONEGUY_ESCALATE_URL", "http://oneguy.invalid/t/add")

QUIET_S = 300            # no move this long -> "quiet" (maybe stuck)
STALL_S = 900            # quiet this long -> escalate (audit 9c: 15 min)
CHILD_TIMEOUT_S = 3600   # delegation.child_timeout_seconds
RECENT_S = 24 * 3600
HISTORY_DAYS = 30
SURFACE_KEEP_S = 600     # finished surface replies stay in the feed this long
ESCALATE_WINDOW_S = 6 * 3600   # never escalate old news (a failure from last week)
LIVE_STATES = ("running", "quiet")
OK_FINAL = ("completed", "complete", "success", "succeeded", "ok", "done", "ended")


def esc_state_path():
    return os.environ.get("ONEGUY_FEED_ESCALATIONS") or os.path.join(T.home_dir(), "feed-escalations.json")


# ------------------------------------------------------------------ small helpers
def _db(path=None):
    return sqlite3.connect(f"file:{path or STATE_DB}?mode=ro", uri=True, timeout=2)


def _local_ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def _iso_ts(s):
    """'2026-11-03T13:00:56.744722-05:00' / '2026-10-29T13:01:46' -> epoch."""
    if not s:
        return None
    try:
        from datetime import datetime
        d = datetime.fromisoformat(str(s))
        return d.timestamp()
    except ValueError:
        return None


def _tail(path, n=12000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _clip(s, n):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def headline(goal):
    """First sentence of a goal, with the plumbing (branch, paths) stripped out."""
    g = re.sub(r"\s+", " ", goal or "").strip()
    m = re.match(r"(.+?[.:;])\s", g)
    first = m.group(1) if m and len(m.group(1)) > 20 else g
    first = first.rstrip(":;")
    first = re.sub(r",?\s+(on|in) (branch|worktree)\s+\S+", "", first)
    first = re.sub(r",?\s+(in|at) ~/\S+", "", first)
    first = re.sub(r",?\s+per docs/\S+(\s+§\S+)?", "", first)
    first = re.sub(r",?\s+(verified|with|using|so that|then)\b.*$", "", first)
    first = re.sub(r"\s*\bitem[:=]\s*\S+", "", first)
    return _clip(first.strip(" ,"), 80)


def where(goal):
    g = goal or ""
    b = re.search(r"branch[`\s]+([\w./-]+)", g)
    w = re.search(r"(~/projects/[\w.-]+)", g)
    return {"branch": b.group(1).rstrip(".,") if b else None, "worktree": w.group(1).rstrip(".,") if w else None}


_VERB = {"terminal": "running a command", "read_file": "reading", "write_file": "writing", "patch": "editing",
         "search_files": "searching", "vision_analyze": "looking at a screenshot", "web_extract": "reading the web",
         "web_search": "searching the web", "browser_exec": "driving a browser", "skill_view": "loading a skill",
         "image_generate": "generating an image", "delegate_task": "briefing a helper", "execute_code": "running code",
         "mcp__browser_exec": "driving a browser", "mcp__terminal": "running a command"}


def tool_verb(tool):
    t = re.sub(r"^mcp__", "", tool or "")
    return _VERB.get(tool) or _VERB.get(t) or t.replace("_", " ")


def humanize(now, kind):
    """'write_file(/x/y/fly-off.html)' -> 'writing fly-off.html'."""
    if not now or kind != "tool":
        return now
    m = re.match(r"(\w+)\((.*)", now)
    if not m:
        return now
    tool, arg = m.group(1), m.group(2)
    verb = tool_verb(tool)
    p = re.search(r"([\w.-]+\.\w{1,5})\b", arg)
    if tool in ("read_file", "write_file", "patch") and p:
        return f"{verb} {p.group(1)}"
    if tool == "terminal":
        c = re.sub(r"^(env \S+=\S+\s+)+", "", arg)
        c = re.sub(r"^\S*/", "", c.split()[0]) if c.split() else ""
        c = re.sub(r"[^\w.-]", "", c)
        return f"{verb}" + (f" ({c})" if c and c not in ("cd", "sleep") else "")
    return verb


def family(model):
    m = (model or "").lower()
    for fam in ("opus", "sonnet", "haiku", "glm", "gemini", "gpt"):
        if fam in m:
            return fam
    return m or "?"


def _link(sid):
    return f"hermes://open/{sid}" if sid else None


def _event(**kw):
    e = {"id": None, "actor": None, "key": None, "who": {"engine": None, "model": None}, "what": None,
         "now": None, "where": {"branch": None, "worktree": None}, "started": None, "last_move": None,
         "status": "running", "live": False, "session_id": None, "deep_link": None, "reason": None,
         "elapsed_s": None, "eta": None}
    e.update(kw)
    e["live"] = e["status"] in LIVE_STATES
    e["deep_link"] = e.get("deep_link") or _link(e.get("session_id"))
    return e


# ------------------------------------------------------------------ keys: run -> item
_KEY_TOKEN = re.compile(r"\bitem[:=]\s*`?((?:task|crew|job|session|proposal|project|goal|media|note):[\w^.\-/@#]+)")


class Keys:
    """session id -> item key, from threads.json (+ aliases followed to the stable key)."""

    def __init__(self, threads_path=None):
        self.data = T.load(threads_path)
        self.by_session = {}
        for k, rec in self.data["threads"].items():
            sids = [(rec.get("primary") or {}).get("session_id")]
            sids += [c.get("session_id") for c in rec.get("children") or []]
            for s in filter(None, sids):
                self.by_session.setdefault(s, k)

    def of_session(self, *sids):
        for s in sids:
            if s and s in self.by_session:
                return self.by_session[s]
        return None

    def canonical(self, key):
        return T.canonical(key, self.data) if key else None

    def session_of(self, key):
        rec = self.data["threads"].get(key or "") or {}
        return (rec.get("primary") or {}).get("session_id")

    def title(self, key):
        rec = self.data["threads"].get(key or "") or {}
        return rec.get("title")

    def from_goal(self, goal):
        m = _KEY_TOKEN.search(goal or "")
        return self.canonical(m.group(1).rstrip(".,`")) if m else None


# ------------------------------------------------------------------ producer: delegate_task
FINAL_RE = re.compile(r"^\d\d:\d\d:\d\d final\s+\| status=(\w+) duration=([\d.]+)s summary: (.*)")
END_RE = re.compile(r"^\d\d:\d\d:\d\d final\s+\| end status=(\w+)")
LINE_RE = re.compile(r"^(\d\d:\d\d:\d\d) (\w+)\s*\| (.*)")


def read_log(path):
    """status/duration/summary from the final line; 'now' = latest assistant/tool line."""
    out = {"final": None, "duration_s": 0.0, "summary": None, "error": None, "now": None,
           "now_kind": None, "now_at": None, "mtime": None}
    try:
        out["mtime"] = os.path.getmtime(path)
    except OSError:
        return out
    for line in _tail(path):
        m = FINAL_RE.match(line)
        if m:
            out["final"], out["duration_s"], out["summary"] = m.group(1), float(m.group(2)), _clip(m.group(3), 400)
            continue
        e2 = END_RE.match(line)
        if e2:
            out["end"] = e2.group(1)
            e = re.search(r"error: (.*)", line)
            if e:
                out["error"] = _clip(e.group(1), 300)
            continue
        m = LINE_RE.match(line)
        if m and m.group(2) in ("assistant", "tool"):
            text = m.group(3)
            if m.group(2) == "tool":
                text = re.sub(r"^->\s*", "", text)
            out["now"], out["now_kind"], out["now_at"] = _clip(text, 180), m.group(2), m.group(1)
    return out


def subagent_sessions(db=None):
    q = """select s.id, s.parent_session_id, s.model, s.started_at, s.ended_at, s.end_reason,
                  s.message_count, s.tool_call_count, s.estimated_cost_usd, s.last_activity_at,
                  s.last_activity_description, s.title, p.title
           from sessions s left join sessions p on p.id = s.parent_session_id
           where s.title like 'Subagent:%' and s.started_at > ?"""
    try:
        con = _db(db)
        rows = con.execute(q, (time.time() - HISTORY_DAYS * 86400,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    keys = ("id", "parent", "model", "started", "ended", "end_reason", "messages", "tool_calls",
            "cost", "last_activity", "activity", "title", "parent_title")
    return [dict(zip(keys, r)) for r in rows]


def _title_prefix(title):
    t = re.sub(r"^Subagent:\s*", "", title or "")
    t = re.sub(r"\s*#\d+$", "", t).rstrip("…").strip()
    return t


def match_session(sessions, goal, started):
    goal_n = re.sub(r"\s+", " ", goal or "").strip()
    best = None
    for s in sessions:
        pre = _title_prefix(s["title"])
        if len(pre) < 8 or not goal_n.startswith(pre):
            continue
        d = abs((s["started"] or 0) - (started or 0))
        if d < 600 and (best is None or d < best[0]):
            best = (d, s)
    return best[1] if best else None


def typical(sessions):
    """median/p75 seconds of completed runs: overall and per model family."""
    durs = {}
    for s in sessions:
        if s["ended"] and s["started"] and s["ended"] - s["started"] >= 30:
            durs.setdefault(family(s["model"]), []).append(s["ended"] - s["started"])

    def stat(v):
        v = sorted(v)
        return {"n": len(v), "median_s": round(statistics.median(v)),
                "p75_s": round(v[min(len(v) - 1, int(len(v) * 0.75))])}
    allv = [d for v in durs.values() for d in v]
    return {"all": stat(allv) if allv else None,
            "by_family": {k: stat(v) for k, v in durs.items() if len(v) >= 5}}


def eta(model, elapsed, typ):
    fam = family(model)
    base = typ["by_family"].get(fam) or typ["all"]
    if not base:
        return None
    med, p75 = base["median_s"], base["p75_s"]
    e = {"typical_s": med, "p75_s": p75, "n": base["n"],
         "basis": f"median of {base['n']} past {fam if fam in typ['by_family'] else 'subagent'} runs",
         "progress": round(min(0.97, elapsed / max(p75, 1)), 3)}
    if elapsed < med:
        e["left_s"], e["label"] = med - elapsed, "on pace"
    elif elapsed < p75:
        e["left_s"], e["label"] = p75 - elapsed, "past typical"
    else:
        e["left_s"], e["label"] = None, "running long"
    e["timeout_in_s"] = max(0, CHILD_TIMEOUT_S - elapsed)
    return e


def delegate_events(now, keys, sessions=None, typ=None):
    """-> (events, batches). Each event also carries the runs page's detail fields."""
    sessions = subagent_sessions() if sessions is None else sessions
    typ = typical(sessions) if typ is None else typ
    events, batches = [], []
    manifests = []
    for mf in glob.glob(os.path.join(DELEG_LIVE, "*", "manifest.json")):
        try:
            with open(mf, encoding="utf-8") as f:
                manifests.append(json.load(f))
        except (OSError, ValueError):
            continue
    manifests.sort(key=lambda m: m.get("started") or "", reverse=True)
    for man in manifests:
        started = _local_ts(man.get("started"))
        if started is None or now - started > HISTORY_DAYS * 86400:
            continue
        b = {"id": man.get("delegation_id"), "started": started, "total": 0, "done": 0, "running": 0}
        for t in man.get("tasks", []):
            goal = t.get("goal")
            log = read_log(t.get("log", ""))
            s = match_session(sessions, goal, started)
            last = max(filter(None, [log["mtime"], s and s["last_activity"], started]))
            sid = s and s["id"]
            model = (s and s["model"]) or man.get("model")
            key = keys.from_goal(goal) or keys.of_session(sid, s and s["parent"])
            if key == "home":
                key = None    # spawned from the home conversation: that's not a card's work
            r = _event(
                id=f"{man.get('delegation_id')}/{t.get('index')}", actor="subagent", key=key,
                who={"engine": "delegate_task", "model": model},
                what=headline(goal), now=humanize(log["now"], log["now_kind"]), where=where(goal),
                started=started, last_move=last, session_id=sid,
                batch=man.get("delegation_id"), headline=headline(goal), goal=_clip(goal, 600), model=model,
                parent=s and s["parent"], parent_title=_clip((s and s["parent_title"]) or "", 70) or None,
                tool_calls=s and s["tool_calls"], messages=s and s["messages"],
                cost_usd=round(s["cost"], 3) if s and s["cost"] else None,
                now_raw=log["now"], now_kind=log["now_kind"], now_at=log["now_at"],
                last_activity=last, summary=log["summary"], error=log["error"])
            if log["final"]:
                r["status"] = "completed" if log["final"].lower() in OK_FINAL else "failed"
                r["final"] = log["final"]
                r["elapsed_s"] = round(log["duration_s"])
                r["ended"] = started + log["duration_s"]
                if r["status"] == "failed":
                    r["reason"] = log["error"] or f"it ended with status {log['final']}"
            elif s and s["ended"]:
                r["status"] = "ended"
                r["elapsed_s"] = round(s["ended"] - started)
                r["ended"] = s["ended"]
            else:
                r["elapsed_s"] = round(now - started)
                r["status"] = "quiet" if now - last > QUIET_S else "running"
                r["quiet_s"] = round(now - last)
                if r["elapsed_s"] > CHILD_TIMEOUT_S + 600:
                    # past the child timeout with no final line: the parent is gone
                    r["status"], r["ended"] = "failed", last
                    r["reason"] = "it ran past the hour limit and never reported back"
            r["live"] = r["status"] in LIVE_STATES
            b["total"] += 1
            if r.get("ended"):
                b["done"] += 1
            else:
                b["running"] += 1
                r["eta"] = eta(model, r["elapsed_s"], typ)
            events.append(r)
        if b["running"] or now - started < RECENT_S:
            batches.append(b)
    return events, batches


# ------------------------------------------------------------------ producer: surface replies
def surface_live():
    """The surface engine's in-memory LIVE, from the route module loaded in this process."""
    mod = sys.modules.get("oneguy_routes_surface")
    if mod is not None and hasattr(mod, "LIVE"):
        return mod.LIVE
    main = sys.modules.get("__main__")
    routes = getattr(main, "ROUTES", None) or {}
    fn = routes.get(("GET", "/api/surface/poll"))
    if fn is not None:
        return fn.__globals__.get("LIVE")
    return None


def surface_events(now, keys, live=None):
    live = live if live is not None else surface_live()
    if live is None:
        return []
    out = []
    with live.cond:
        pairs = [(bid, live.runs.get(rid)) for bid, rid in live.current.items()]
        ctx = dict(live.ctx)
    for bid, run in pairs:
        if run is None:
            continue
        if run.done and now - (run.t_end or now) > SURFACE_KEEP_S:
            continue
        key = keys.canonical(bid)
        block = (ctx.get(bid) or {}).get("block") or {}
        if run.done:
            status = {"failed": "failed", "interrupted": "interrupted"}.get(run.status, "completed")
            doing = None
        else:
            status = "running"
            doing = tool_verb(run.tools[-1]) if run.tools and not run.text else (
                "writing a reply" if run.text else "thinking")
        sid = keys.session_of(key)
        h = block.get("headline")
        h = h.get("title") if isinstance(h, dict) else h
        what = h if isinstance(h, str) and h else (keys.title(key) or block.get("title") or bid)
        what = re.sub(r"^1guy · \w+:\s*", "", what or "")
        out.append(_event(
            id=f"surface/{run.id}", actor="surface", key=key, who={"engine": "api_server", "model": None},
            what=_clip(what, 80), now=doing, started=run.t0,
            last_move=run.t_end or now, status=status, session_id=sid,
            elapsed_s=round((run.t_end or now) - run.t0), reason=run.error if status == "failed" else None,
            ended=run.t_end, mode=run.mode))
    return out


# ------------------------------------------------------------------ producer: tasklist ⤷ runs
def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _handoff_task(run):
    try:
        with open(os.path.join(HANDOFFS, run + ".md"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("> "):
                    return line[2:].strip()
    except OSError:
        pass
    return None


def read_run_jsonl(path):
    out = {"session_id": None, "now": None, "last_ts": None, "result": None, "text": False}
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            line = line.strip()
            if line.startswith("Session:") or line.startswith("session_id:"):
                out["session_id"] = out["session_id"] or line.split(":", 1)[1].strip()
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("timestamp"):
                out["last_ts"] = o["timestamp"] / 1000.0
            t = o.get("type")
            if o.get("session_id"):
                out["session_id"] = o["session_id"]
            if t == "tool_use":
                out["now"] = tool_verb(o.get("name"))
            elif t == "text":
                out["now"], out["text"] = "writing the reply", True
            elif t == "result":
                out["result"] = o
    return out


def tasklist_events(now, keys):
    out = []
    for mp in glob.glob(os.path.join(TASKLIST_RUNS, "*.meta.json")):
        try:
            with open(mp, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if meta.get("engine") == "oneguy":
            continue    # a 1guy-engine run: the surface producer already reports it
        run = meta.get("run") or os.path.basename(mp)[: -len(".meta.json")]
        jl = os.path.join(TASKLIST_RUNS, run + ".jsonl")
        started = _iso_ts(meta.get("started_at"))
        try:
            mtime = os.path.getmtime(jl)
        except OSError:
            mtime = None
        last = max(filter(None, [mtime, started])) if (mtime or started) else None
        if not last or now - last > RECENT_S:
            continue
        info = read_run_jsonl(jl)
        alive = bool(meta.get("pid")) and _pid_alive(meta["pid"])
        res = info["result"]
        reason = None
        if res is not None:
            ok = res.get("exit_code") in (0, None) and not res.get("is_error")
            status = "completed" if ok else "failed"
            if not ok:
                reason = _clip(res.get("error") or res.get("text") or f"exit code {res.get('exit_code')}", 200)
        elif alive:
            status = "quiet" if now - (last or now) > QUIET_S else "running"
        else:
            status, reason = "failed", "the run exited without a result"
        sid = info["session_id"] or meta.get("session_id")
        key = keys.canonical(meta.get("key")) or keys.of_session(sid)
        what = _handoff_task(run) or keys.title(key) or (meta.get("slug") or run).replace("-", " ")
        out.append(_event(
            id=f"run/{run}", actor="run", key=key, who={"engine": "hermes chat", "model": meta.get("model")},
            what=_clip(what, 80), now=info["now"] if status in LIVE_STATES else None,
            started=started, last_move=last, status=status, session_id=sid, reason=reason,
            elapsed_s=round((last if status not in LIVE_STATES else now) - started) if started else None,
            ended=last if status not in LIVE_STATES else None, run=run))
    return out


# ------------------------------------------------------------------ producer: 1guy-* cron
def _cron_running(db_path, job_ids):
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        q = ("select job_id, started_at, claimed_at, progress_at from executions where status in "
             "('claimed','running') and job_id in (%s)" % ",".join("?" * len(job_ids)))
        rows = con.execute(q, list(job_ids)).fetchall() if job_ids else []
        con.close()
    except sqlite3.Error:
        return {}
    return {r[0]: {"started": _iso_ts(r[1] or r[2]), "progress": _iso_ts(r[3])} for r in rows}


def cron_events(now):
    try:
        with open(os.path.join(CRON_DIR, "jobs.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    jobs = d.get("jobs", []) if isinstance(d, dict) else d
    jobs = [j for j in jobs if isinstance(j, dict) and str(j.get("name", "")).startswith("1guy-")]
    running = _cron_running(os.path.join(CRON_DIR, "executions.db"), [j.get("id") for j in jobs])
    out = []
    for j in jobs:
        jid, name = j.get("id"), str(j.get("name"))
        what = name.replace("1guy-", "the ", 1) + " job"
        outs = sorted(glob.glob(os.path.join(CRON_DIR, "output", str(jid), "*")))
        common = dict(actor="cron", key=f"job:{jid}", who={"engine": "cron", "model": j.get("model")},
                      what=what, job=name, output=outs[-1] if outs else None)
        if jid in running:
            r = running[jid]
            st = r["started"] or now
            last = r["progress"] or st
            out.append(_event(id=f"cron/{jid}/live", started=st, last_move=last,
                              status="quiet" if now - last > STALL_S else "running",
                              now="running", elapsed_s=round(now - st), **common))
            continue
        last_run = _iso_ts(j.get("last_run_at"))
        if not last_run or now - last_run > RECENT_S:
            continue
        failed = (j.get("last_status") or "ok") not in ("ok", "success", "completed")
        out.append(_event(id=f"cron/{jid}/{j.get('last_run_at')}", started=last_run, last_move=last_run,
                          status="failed" if failed else "completed", ended=last_run,
                          reason=_clip(j.get("last_error") or j.get("last_status"), 200) if failed else None,
                          streak=j.get("failure_streak"), **common))
    return out


# ------------------------------------------------------------------ the feed
_cache = {"at": 0.0, "val": None}
_cache_lock = threading.Lock()
CACHE_S = 3.0


def collect(now=None, threads_path=None, live=None, sessions=None):
    """All producers -> {now, events, batches, typical}. A broken producer never sinks the feed."""
    now = now or time.time()
    keys = Keys(threads_path)
    sessions = subagent_sessions() if sessions is None else sessions
    typ = typical(sessions)
    events, batches, errors = [], [], {}
    try:
        ev, batches = delegate_events(now, keys, sessions, typ)
        events += ev
    except Exception as e:      # noqa: BLE001 - one producer can't take the feed down
        errors["subagent"] = repr(e)
    for name, fn in (("surface", lambda: surface_events(now, keys, live)),
                     ("run", lambda: tasklist_events(now, keys)),
                     ("cron", lambda: cron_events(now))):
        try:
            events += fn()
        except Exception as e:  # noqa: BLE001
            errors[name] = repr(e)
    events.sort(key=lambda e: (not e["live"], -(e.get("last_move") or 0)))
    return {"now": now, "events": events, "batches": batches, "typical": typ, "errors": errors,
            "quiet_after_s": QUIET_S, "stall_after_s": STALL_S, "timeout_s": CHILD_TIMEOUT_S}


def cached(max_age=CACHE_S):
    with _cache_lock:
        if _cache["val"] is not None and time.time() - _cache["at"] < max_age:
            return _cache["val"]
    val = collect()
    with _cache_lock:
        _cache.update(at=time.time(), val=val)
    return val


def live_by_key(events):
    """key -> the freshest live event on it (what the board pulses)."""
    out = {}
    for e in events:
        if e["live"] and e["key"] and e["key"] not in out:
            out[e["key"]] = e
    return out


# ------------------------------------------------------------------ escalation (audit 9c)
SECTION = "Open questions / issues"
_ACTOR_WORD = {"subagent": "A helper", "run": "A tasklist run", "cron": "The 1guy cron"}


def escalation_for(e, now):
    """-> (dedupe_key, kind) when this event should become a tasklist item, else None.
    Surface replies are not escalated: the user is in that conversation and sees the error inline."""
    if e["actor"] not in _ACTOR_WORD:
        return None
    ref = e.get("ended") or e.get("last_move") or e.get("started") or 0
    if e["status"] == "failed":
        if now - ref > ESCALATE_WINDOW_S:
            return None
        # a cron job is one recurring thing: one item per failing streak, not per run
        return (f"cron:{e['key']}" if e["actor"] == "cron" else e["id"]), "failed"
    if e["live"] and now - (e.get("last_move") or now) > STALL_S:
        return (f"cron:{e['key']}" if e["actor"] == "cron" else e["id"]), "quiet"
    return None


def escalation_text(e, kind, now):
    who = _ACTOR_WORD[e["actor"]]
    what = _clip(e.get("what") or "something", 70)
    if e["actor"] == "cron":
        lead = f"{what[0].upper()}{what[1:]}"
        verb = "failed" if kind == "failed" else f"has been stuck for {int((now - e['last_move']) // 60)} min"
        sent = f"{lead} {verb}"
    elif kind == "failed":
        sent = f"{who} working on \u201c{what}\u201d failed"
    else:
        sent = f"{who} working on \u201c{what}\u201d has gone quiet for {int((now - e['last_move']) // 60)} min"
    reason = _clip(e.get("reason") or ("no new activity since it went quiet" if kind == "quiet" else ""), 140)
    text = f"{sent}: {reason}. Check it and retry or drop it. #1guy" if reason else f"{sent}. Check it. #1guy"
    return text


def _load_state(p):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("sent"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "sent": {}}


def _save_state(d, p):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def post_tasklist(item, url=None, timeout=5):
    req = urllib.request.Request(url or ESCALATE_URL, data=json.dumps(item).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "1guy-feed"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


_esc_lock = threading.Lock()


def escalate(events, now=None, post=None, state_path=None):
    """Turn failed / quiet-past-15-min runs into ONE tasklist item each. Deduped by a state
    file: a run is escalated at most once, ever; a failing cron job once per failing streak
    (re-armed when the job runs ok again). A failed POST is not recorded, so it retries.
    Returns the items posted."""
    now = now or time.time()
    post = post or post_tasklist
    p = state_path or esc_state_path()
    posted = []
    with _esc_lock:
        first = not os.path.exists(p)
        st = _load_state(p)
        sent = st["sent"]
        dirty = False
        if first:
            # first look ever: whatever already failed is old news. Baseline it, post nothing,
            # and escalate only what goes wrong from here on.
            for e in events:
                hit = escalation_for(e, now)
                if hit:
                    sent[hit[0]] = {"at": now, "kind": "baseline", "event": e["id"]}
            _save_state(st, p)
            return posted
        # re-arm cron jobs that are healthy again
        for e in events:
            if e["actor"] == "cron" and e["status"] == "completed" and f"cron:{e['key']}" in sent:
                sent.pop(f"cron:{e['key']}")
                dirty = True
        for e in events:
            hit = escalation_for(e, now)
            if not hit:
                continue
            dk, kind = hit
            if dk in sent:
                continue
            item = {"section": SECTION, "text": escalation_text(e, kind, now),
                    "origin": f"1guy feed · {e.get('session_id') or e['id']}"}
            try:
                ok = post(item)
            except Exception:   # noqa: BLE001 - tasklist down: try again next time
                ok = False
            if ok:
                sent[dk] = {"at": now, "kind": kind, "event": e["id"], "text": item["text"]}
                posted.append(item)
                dirty = True
        if dirty:
            _save_state(st, p)
    return posted


_esc_last = {"at": 0.0}
ESCALATE_EVERY_S = 60


def maybe_escalate(events):
    """Called by the feed route: at most once a minute, off the request thread."""
    if os.environ.get("ONEGUY_ESCALATE", "1") == "0":
        return False
    now = time.time()
    if now - _esc_last["at"] < ESCALATE_EVERY_S:
        return False
    _esc_last["at"] = now
    threading.Thread(target=escalate, args=(events,), daemon=True).start()
    return True
