"""The board: one attention-ranked list of Blocks from the user's real sources.

    build_board() -> {generated_at, sources, projects, blocks}

Each source (tasks, sessions, crew, jobs, brain, proposals, media) is a module
with TTL and fetch(). Sources run in parallel through a stale-while-revalidate
cache; a failing or slow source shows up as {ok: false, error} in
board.sources and never breaks the board. See docs/home-ws-B.md.
"""
import concurrent.futures as _cf
import re
import time

from . import attention, artifacts, brain, crew, jobs, media, proposals, sessions, tasks, util, widgets
from . import briefing, headlines   # WS-H: the one guy's plain-language layer
from . import talk as talk_src      # request-router: the talk bar's own interactions
from . import selector   # phase 3.5 (A6): the three = a real selector
from . import plugins    # desktop plugin surfaces as canvas tiles (exp/plugin-tiles)
from . import untangle   # door A: the three that need you + piles proposed from history
from . import doors      # the two-door first run: which door this home opens on
from . import claim      # frontier 4: the frame's -ing word, and which cards a line is about
from . import world      # lane D: one plain line of what's moving on each item, and who
from .block import make, SURFACE, lens_fields

SOURCES = {
    "tasks": tasks, "sessions": sessions, "crew": crew, "jobs": jobs,
    "brain": brain, "proposals": proposals, "media": media, "artifacts": artifacts,
    "talk": talk_src, "widgets": widgets, "plugins": plugins,
}
BUDGET_S = 3.0          # a cold source slower than this reports "loading" and fills the cache later
PROJECT_LINKS = 8       # tendrils from a project to its loudest children
MIN_DERIVED = 2         # a tasklist tag needs this many open items to become a project
TIME_TAGS = {"calendar", "cal", "event", "events", "schedule", "appointment", "appointments"}   # time is a place, not a project (card-grammar §6)
_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=len(SOURCES), thread_name_prefix="board-src")


def run_sources(force=False, only=None):
    """-> {name: cache entry}. Never raises."""
    names = [n for n in SOURCES if not only or n in only]
    futs = {n: _EXECUTOR.submit(util.CACHE.get, n, SOURCES[n].TTL, SOURCES[n].fetch, force)
            for n in names}
    deadline = time.time() + BUDGET_S
    out = {}
    for n, f in futs.items():
        try:
            out[n] = f.result(timeout=max(0.05, deadline - time.time()))
        except _cf.TimeoutError:
            out[n] = {"ok": False, "error": f"still loading (> {BUDGET_S:.0f}s); try again shortly"}
        except Exception as e:  # pragma: no cover - CACHE.get catches source errors
            out[n] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


# ---------- projects ----------
def _phrases(meta):
    title = re.sub(r"\(.*?\)", "", meta["title"]).strip().lower()
    ph = {meta["slug"], meta["slug"].replace("-", " "), title} | set(meta.get("keywords") or [])
    return [p for p in ph if len(p) >= 4]


def _has_phrase(text, phrase):
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def match_project(hint, metas):
    """hint {"tags", "text"} -> project id or None.
    Scores: tag == slug 3 · tag == project's first tag 2 · title/slug/keyword
    phrase in the text 1. Highest wins; ties go to the earlier project."""
    if not hint:
        return None
    tags = {t.lower() for t in hint.get("tags") or []}
    text = (hint.get("text") or "").lower()
    best, best_s = None, 0
    for m in metas:
        s = 0
        if m["slug"] in tags:
            s = 3
        elif m.get("tags") and m["tags"][0] in tags:
            s = 2
        elif text and any(_has_phrase(text, p) for p in _phrases(m)):
            s = 1
        if s > best_s:
            best, best_s = m["id"], s
    return best


def derive_projects(task_blocks):
    """No brain file: every tasklist tag on >= MIN_DERIVED open items is a project."""
    counts, order = {}, []
    for b in task_blocks:
        if b["status"] == "done":
            continue
        for t in b["tags"]:
            if t not in counts:
                order.append(t)
            counts[t] = counts.get(t, 0) + 1
    metas, blocks = [], []
    for t in order:
        if counts[t] < MIN_DERIVED or t.startswith("oneguy-test") or t.lower() in TIME_TAGS:
            continue
        pid = f"project:{t}"
        summary = f"{counts[t]} open tasks tagged #{t}"
        metas.append({"id": pid, "title": t, "summary": summary, "color": util.color_for(t),
                      "slug": t, "tags": [t], "keywords": [], "status": None})
        blocks.append(make("project", t, t, summary=summary, status="idle", project=pid, tags=[t],
                           source_type="derived", actions=[dict(SURFACE)],
                           detail={"goal": None, "horizon": None, "next": None, "children": []}))
    return metas, blocks


# ---------- assembly ----------
def _strip_private(b):
    return {k: v for k, v in b.items() if not k.startswith("_")}


def assemble(results, now=None, explain=False, live=None, keep_history=False):
    now = now or util.now()
    srcs, blocks = {}, []
    for name, e in results.items():
        if e.get("ok"):
            v = e["value"]
            st = {"ok": True, "count": v.get("count", len(v.get("blocks", [])))}
            if e.get("stale_error"):
                st["stale_error"] = e["stale_error"]
            for k in ("open", "failed", "candidates", "writers"):
                if k in v:
                    st[k] = v[k]
            blocks.extend(dict(b) for b in v.get("blocks", []))
        else:
            st = {"ok": False, "error": e.get("error") or "unknown error"}
        if "ms" in e:
            st["ms"] = e["ms"]
        if e.get("cached"):
            st["age_s"] = e.get("age")
        srcs[name] = st

    # projects: the brain's, else derived from tasklist tags
    bv = results.get("brain", {})
    metas = list(bv.get("value", {}).get("projects", [])) if bv.get("ok") else []
    if not metas:
        dm, dblocks = derive_projects([b for b in blocks if b["kind"] == "task"])
        metas, blocks = dm, blocks + dblocks
        srcs.setdefault("brain", {})["derived_projects"] = len(dm)
    # extra project metas from sources that bring their own (plugins -> project:hermes)
    for _n, _e in results.items():
        if _n != "brain" and _e.get("ok"):
            for _m in _e["value"].get("projects") or []:
                if _m["id"] not in {x["id"] for x in metas}:
                    metas.append(_m)
    pids = {m["id"] for m in metas}
    # a project id must have exactly one block (derived vs source-provided clash)
    _seenp, _keep = set(), []
    for _b in blocks:
        if _b["kind"] == "project":
            if _b["id"] in _seenp:
                continue
            _seenp.add(_b["id"])
        _keep.append(_b)
    blocks = _keep
    pstatus = {}
    for b in blocks:
        if b["kind"] == "project":
            pstatus[b["id"]] = b["status"]

    # project assignment
    for b in blocks:
        if b["kind"] == "project":
            b["project"] = b["id"] if b["id"] in pids else None
            continue
        if b.get("project") not in pids:
            b["project"] = None
        if b["project"] is None:
            b["project"] = match_project(b.get("_match"), metas)

    # media mentions in crew issues
    media_ids = [b["id"][6:] for b in blocks if b["kind"] == "media"]
    for b in blocks:
        if b["kind"] == "crew":
            b["links"] += crew.media_mentions(b["title"] + "\n" + (b["detail"].get("body") or ""),
                                              media_ids)

    # links: project <-> children, then drop dangling, then make them symmetric
    ids = {b["id"] for b in blocks}
    children = {}
    for b in blocks:
        if b["project"] and b["kind"] != "project":
            children.setdefault(b["project"], []).append(b)
            if b["project"] not in b["links"]:
                b["links"].append(b["project"])
    graph = {b["id"]: [l for l in dict.fromkeys(b["links"]) if l in ids and l != b["id"]]
             for b in blocks}
    for a, ls in list(graph.items()):
        for l in ls:
            if a not in graph[l]:
                graph[l].append(a)

    # attention: everything but projects, then projects (they carry their children)
    byid = {b["id"]: b for b in blocks}
    def feats(b):
        return {"status": b["status"], "kind": b["kind"], "decision": b.get("_decision"),
                "priority": b.get("priority"), "due_ts": util.parse_ts(b.get("due")),
                "updated_ts": util.parse_ts(b.get("updated")),
                "project_active": pstatus.get(b.get("project")) in ("active", "needs-you")
                and b["kind"] != "project"}
    for b in blocks:
        if b["kind"] != "project":
            f = feats(b)
            b["attention"] = attention.score(f, now)
            if explain:
                b["_why"] = attention.explain(f, now)
    for b in blocks:
        if b["kind"] == "project":
            kids = children.get(b["id"], [])
            ups = [util.parse_ts(k.get("updated")) for k in kids]
            ups = [u for u in ups if u]
            if ups and not b.get("updated"):
                b["updated"] = util.iso(max(ups))
            if b["source"]["type"] == "derived" and any(k["status"] in ("active", "needs-you") for k in kids):
                b["status"] = "active"
            f = feats(b)
            b["attention"] = attention.project_score(f, now, [k["attention"] for k in kids])
            if explain:
                carried = attention.CHILD_CARRY * max([k["attention"] for k in kids] or [0.0])
                b["_why"] = attention.explain(f, now) + [("child-carry (floor)", carried)]
            kids_sorted = sorted(kids, key=lambda k: -k["attention"])
            b["detail"]["children"] = [k["id"] for k in kids_sorted]
            loud = {k["id"] for k in kids_sorted[:PROJECT_LINKS]}
            # keep only the loudest children as tendrils (all stay in detail.children)
            graph[b["id"]] = [l for l in graph[b["id"]]
                              if l in loud or byid[l].get("project") != b["id"]]
    for b in blocks:
        b["links"] = graph[b["id"]]
        if b["kind"] != "project" and b["project"] and b["project"] not in b["links"]:
            b["links"].append(b["project"])

    blocks.sort(key=lambda b: (-b["attention"], -(util.parse_ts(b.get("updated")) or 0)))
    out_blocks = []
    for b in blocks:
        lens_fields(b)   # optional `when` / `inbox` for the Time and Inbox lenses
        why = b.get("_why")
        b = _strip_private(b)
        if explain and why is not None:
            b["_why"] = [[n, round(v, 3)] for n, v in why]
        out_blocks.append(b)
    world.apply(out_blocks, now, live)   # b["world"]: what's moving out there, who's moving it (+ its lift)
    claim.apply(out_blocks, live)        # b["claim"]: a running card's -ing word (frontier 4)
    out_blocks.sort(key=lambda b: (-b["attention"], -(util.parse_ts(b.get("updated")) or 0)))
    projects = [{"id": m["id"], "title": m["title"], "summary": m["summary"], "color": m["color"]}
                for m in metas]
    board = {"generated_at": util.iso(now), "sources": srcs, "projects": projects,
             "blocks": out_blocks}
    sv = results.get("sessions", {})
    board["_history"] = list(sv["value"].get("history") or []) if sv.get("ok") else []   # server-side only
    plain_layer(board, now)
    if not keep_history:
        board.pop("_history", None)
    return board


def plain_layer(board, now=None):
    """WS-H: cached headlines onto blocks + the briefing (for_you, broke, line).
    Cache reads only, never the LLM, and never breaks the board."""
    try:
        data = headlines.load()
        headlines.attach(board["blocks"], data)
        picks, whys = selector.pick(board.get("blocks") or [], now=now)
        # door A: the three that need you, counted; on a first run they and the piles lead
        need = untangle.needs_you(board, now)
        board["needs_you"] = need
        board["history"] = untangle.facts(board)
        which = doors.pick(board)     # "a" lived-in · "b" sparse · None once the user has judged something
        door = None
        if which:                     # the door: nothing judged yet. The three and the piles lead
            picks, whys = untangle.lead(board, picks, whys, need)
            if which == "a":          # door B has nothing to count: he asks instead (doors.apply)
                door = dict(board["history"], need=need)
        elif untangle.open_piles(board):
            # a judged board with piles still waiting for an answer: the piles keep their cards and
            # their line on the rail, but the first-visit opener and its three belong to the door only
            picks, whys = untangle.lead(board, picks, whys, [])
        briefing.apply(board, data, now, picks=picks, whys=whys, door=door)
        doors.apply(board, which)     # board.first_run + board.door (his words for that door)
        world.speak(board, now)   # lane D: today's conflict, said out loud in his opener
    except Exception as e:  # pragma: no cover - defensive
        board["briefing"] = {"line": "", "for_you": [], "broke": [], "by": "error",
                             "error": f"{type(e).__name__}: {e}"[:200]}
        board.setdefault("first_run", None)


def build_board(force=False, explain=False, here=False):
    t0 = time.time()
    board = assemble(run_sources(force=force), explain=explain, live=world.live_events(), keep_history=True)
    headlines.kick(board)   # WS-H: background refresh of missing/stale headlines (never waits)
    untangle.kick(board)    # door A: piles proposed from history, once per real change (never waits)
    board.pop("_history", None)
    briefing.moved(board, here=here)   # frontier 3: block.changed + briefing.moved (what moved since the user last looked)
    board["took_ms"] = round((time.time() - t0) * 1000)
    return board


def invalidate(name=None):
    util.CACHE.clear(name)
