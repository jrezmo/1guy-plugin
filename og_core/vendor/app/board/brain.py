"""brain: the second brain's projects.md and goals.md (~/.hermes/1guy/brain/).

Format (WS-C): optional preamble, then one '## <Title>' section per entry with
'key: value' lines (slug, status, horizon, next, tags, keywords, color, goal,
why) followed by body text. Projects -> board.projects[] and project blocks;
goals -> goal blocks. Missing files -> empty (the assembler then derives
projects from tasklist tags).
"""
import os
import re

from . import util
from .block import make, SURFACE

TTL = 15.0
KEY_RE = re.compile(r"^([a-z][a-z_ -]{1,20}):\s*(.*)$")
KNOWN_KEYS = {"slug", "id", "status", "horizon", "next", "tags", "keywords", "color", "goal",
              "why", "summary", "projects", "evidence"}
STATUS_MAP = {"active": "active", "idle": "idle", "parked": "parked", "done": "done",
              "paused": "parked", "dormant": "parked", "someday": "parked", "waiting": "waiting",
              "blocked": "waiting", "needs-you": "needs-you"}


def _split_list(v):
    return [t.strip().lstrip("#").lower() for t in re.split(r"[,\s]+", v or "") if t.strip(" #")]


def parse(text):
    """-> [{title, slug, keys:{}, body}] for each '## ' section."""
    out, cur = [], None
    for line in (text or "").splitlines():
        if line.startswith("## "):
            cur = {"title": line[3:].strip(), "keys": {}, "body": []}
            out.append(cur)
            continue
        if cur is None or line.startswith("# "):
            continue
        m = KEY_RE.match(line.strip())
        if m and m.group(1).strip().lower() in KNOWN_KEYS and not cur["body"]:
            cur["keys"][m.group(1).strip().lower()] = m.group(2).strip()
        elif line.strip() or cur["body"]:
            cur["body"].append(line.rstrip())
    for e in out:
        e["body"] = "\n".join(e["body"]).strip()
        e["slug"] = util.slugify(e["keys"].get("slug") or e["keys"].get("id") or e["title"])
        e["tags"] = _split_list(e["keys"].get("tags"))
        e["keywords"] = [k.strip().lower() for k in (e["keys"].get("keywords") or "").split(",") if k.strip()]
    return out


def first_sentence(text, n=200):
    t = util.clip(text, 2000)
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return util.clip(m.group(1) if m else t, n)


def _status(v, default="idle"):
    return STATUS_MAP.get((v or "").strip().lower().split(" ")[0], default)


def _read(name):
    path = os.path.join(util.oneguy("brain"), name)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(), os.path.getmtime(path)
    except OSError:
        return None, None


def project_entries():
    text, mtime = _read("projects.md")
    return (parse(text) if text else []), mtime


def build(projects, goals, p_mtime=None, g_mtime=None):
    """-> (projects_meta, blocks). projects_meta carries match info for the
    assembler: {id, title, summary, color, slug, tags, keywords, status}."""
    metas, blocks = [], []
    goal_for = {}
    for g in goals:          # a goal whose FIRST tag is the project wins
        if g["tags"]:
            goal_for.setdefault(g["tags"][0], g)
    for g in goals:
        for t in g["tags"]:
            goal_for.setdefault(t, g)
    for p in projects:
        pid = f"project:{p['slug']}"
        status = _status(p["keys"].get("status"))
        summary = util.redact(p["keys"].get("summary") or first_sentence(p["body"]))
        g = goal_for.get(p["slug"])
        metas.append({"id": pid, "title": p["title"], "summary": summary,
                      "color": util.color_for(p["slug"], p["keys"].get("color")),
                      "slug": p["slug"], "tags": p["tags"], "keywords": p["keywords"],
                      "status": status})
        blocks.append(make(
            "project", p["slug"], p["title"], summary=summary, status=status, project=pid,
            tags=p["tags"], source_type="brain", updated=None,
            links=[f"goal:{g['slug']}"] if g else [],
            actions=[dict(SURFACE)],
            detail={"goal": p["keys"].get("goal") or (g["title"] if g else first_sentence(p["body"], 160)),
                    "horizon": p["keys"].get("horizon"), "next": p["keys"].get("next"),
                    "children": [], "body": util.clip(util.redact(p["body"]), 800)},
            brain_ref=f"projects.md#{p['slug']}",
        ))
        blocks[-1]["source"]["ref"] = f"projects.md#{p['slug']}"
    slugs = {m["slug"] for m in metas}
    for g in goals:
        links = [f"project:{t}" for t in g["tags"] if t in slugs]
        links += [f"project:{util.slugify(s)}" for s in _split_list(g["keys"].get("projects"))]
        status = _status(g["keys"].get("status"))
        why = g["keys"].get("why") or g["body"]
        ev = [e.strip() for e in (g["keys"].get("evidence") or "").split(";") if e.strip()]
        if g["keys"].get("next"):
            ev.append("next: " + g["keys"]["next"])
        b = make(
            "goal", g["slug"], g["title"], summary=util.redact(g["keys"].get("summary") or first_sentence(g["body"])),
            status=status, tags=g["tags"], source_type="brain", updated=util.iso(g_mtime),
            links=links, actions=[dict(SURFACE)],
            detail={"why": util.clip(util.redact(why), 600), "horizon": g["keys"].get("horizon"),
                    "evidence": ev},
        )
        b["source"]["ref"] = f"goals.md#{g['slug']}"
        blocks.append(b)
    return metas, blocks


def fetch():
    ptext, pm = _read("projects.md")
    gtext, gm = _read("goals.md")
    projects = parse(ptext) if ptext else []
    goals = parse(gtext) if gtext else []
    metas, blocks = build(projects, goals, pm, gm)
    return {"blocks": blocks, "projects": metas, "count": len(blocks),
            "files": {"projects.md": ptext is not None, "goals.md": gtext is not None}}
