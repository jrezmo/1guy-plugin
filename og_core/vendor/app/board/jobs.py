"""jobs: Hermes cron jobs (~/.hermes/cron/jobs.json, read-only) plus the latest
output file per job (~/.hermes/cron/output/<id>/*.md).

Failed / erroring jobs -> status failed (high attention). Healthy jobs ->
status done (compact). Paused/disabled or never-run jobs -> idle.
Action "run now" -> POST /api/board/job {job_id, op: "run"}.
"""
import os
import re

from . import util
from .block import make, SURFACE

TTL = 30.0
EXCERPT = 300
_NOISE = re.compile(r"(Warning|warnings\.warn|^\s*$)")
_HEADER = re.compile(r"^(# Cron Job:|\*\*[A-Za-z ]+:\*\*)")


def latest_output(job_id):
    d = os.path.join(util.CFG["cron_output"], job_id)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".md"))
    except OSError:
        return None, None
    if not names:
        return None, None
    path = os.path.join(d, names[-1])
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(2_000_000), os.path.getmtime(path)
    except OSError:
        return None, None


def output_excerpt(text):
    """The part of a cron output file worth showing: the agent's response, or
    for script jobs the body after the header (warnings stripped)."""
    if not text:
        return ""
    m = re.search(r"^## Response\s*$", text, re.M)
    if m:
        body = text[m.end():]
    else:
        lines = text.splitlines()
        i = 0
        while i < len(lines) and (_HEADER.match(lines[i]) or not lines[i].strip()):
            i += 1
        body = "\n".join(lines[i:])
        st = re.search(r"^\*\*Status:\*\*\s*(.+)$", text, re.M)
        if st and not body.strip():
            body = st.group(1)
    keep = [ln for ln in body.splitlines() if not _NOISE.search(ln)]
    return util.safe_excerpt("\n".join(keep), EXCERPT)


def job_status(j):
    if not j.get("enabled", True) or j.get("state") == "paused" or j.get("paused_at"):
        return "idle"
    if (j.get("last_status") in ("error", "failed", "timeout")
            or (j.get("failure_streak") or 0) > 0 or j.get("last_delivery_error")):
        return "failed"
    if not j.get("last_run_at"):
        return "idle"
    return "done"


def _hhmm(ts):
    s = util.iso(ts) or ""
    return s[11:16] if len(s) >= 16 else "?"


def to_block(j, out_text=None, out_mtime=None):
    jid = j["id"]
    status = job_status(j)
    name = j.get("name") or jid
    last_ts = util.parse_ts(j.get("last_run_at"))
    paused = status == "idle" and (not j.get("enabled", True) or j.get("paused_at")
                                   or j.get("state") == "paused")
    err = j.get("last_error") or j.get("last_delivery_error")
    excerpt = output_excerpt(out_text)
    if status == "failed" and err:
        err_lines = [ln for ln in str(err).splitlines() if not _NOISE.search(ln)]
        excerpt = util.safe_excerpt(" ".join(err_lines[-3:]) or err, EXCERPT)
    if paused:
        lead = "paused" + (f" · {util.clip(j.get('paused_reason'), 60)}" if j.get("paused_reason") else "")
    elif not last_ts:
        lead = "not run yet · next " + (util.clip(j.get("next_run_at"), 16) or "?")
    else:
        lead = f"ran {_hhmm(last_ts)} · {j.get('last_status') or '?'}"
        if j.get("last_delivery_error"):
            lead += " · delivery failed"
    summary = lead + (f" · {excerpt}" if excerpt else "")
    sched = j.get("schedule_display") or (j.get("schedule") or {}).get("display") \
        or (j.get("schedule") or {}).get("expr")
    actions = []
    if not paused:
        actions.append({"id": "run", "label": "run now", "post": "/api/board/job",
                        "body": {"job_id": jid, "op": "run"},
                        "confirm": f"run \u201c{util.clip(name, 40)}\u201d now? it does real work "
                                   f"(may send mail or post)"})
    actions.append(dict(SURFACE))
    return make(
        "job", jid, name, summary=summary, status=status,
        updated=util.iso(last_ts or out_mtime), source_type="cron",
        tags=["paused"] if paused else [],
        actions=actions,
        detail={"job_id": jid, "schedule": sched, "last_run": util.iso(last_ts),
                "last_status": j.get("last_status") or ("paused" if paused else None),
                "last_output_excerpt": excerpt, "next_run": util.iso(util.parse_ts(j.get("next_run_at"))),
                "failure_streak": j.get("failure_streak") or 0,
                "expr": (j.get("schedule") or {}).get("expr") if (j.get("schedule") or {}).get("kind", "cron") == "cron" else None,
                "deliver": j.get("deliver")},
        match={"tags": [], "text": name},
        decision=status == "failed",
    )


def load_jobs():
    data = util.read_json(util.CFG["cron_jobs"], None)
    if data is None:
        raise FileNotFoundError(util.CFG["cron_jobs"])
    return data.get("jobs", []) if isinstance(data, dict) else list(data)


def fetch():
    blocks = []
    for j in load_jobs():
        if not j.get("id"):
            continue
        text, mtime = latest_output(j["id"])
        blocks.append(to_block(j, text, mtime))
    return {"blocks": blocks, "count": len(blocks),
            "failed": sum(1 for b in blocks if b["status"] == "failed")}
