"""talk: the talk bar's own interactions, from threads.json.

Every routed "new" interaction mints a `talk:<ulid>` thread (threads.start via
/api/threads/open, opened_from='talk-router'). Those threads are real work with a
real session — they get their own board cards here, so an interaction isn't
invisible once the column closes. Tapping the card resumes that key's own session
(the existing column path). Stdlib only.
"""
import time

from . import util
from .block import make, SURFACE

TTL = 10.0
ACTIVE_S = 30 * 60          # a talk card touched in the last 30 min reads as live


def to_blocks(data, now=None):
    now = now if now is not None else time.time()
    out = []
    for key, rec in ((data or {}).get("threads") or {}).items():
        if not isinstance(rec, dict) or not key.startswith("talk:"):
            continue
        sid = ((rec.get("primary") or {}).get("session_id")) or ""
        at = (rec.get("primary") or {}).get("at") or rec.get("surface", {}).get("last_activity") or 0
        fresh = bool(at and now - at < ACTIVE_S) or bool(rec.get("surface", {}).get("last_activity")
                                                        and now - rec["surface"]["last_activity"] < ACTIVE_S)
        out.append(make(
            "talk", key.split(":", 1)[1], rec.get("title") or "a conversation with the one guy",
            summary="", status="active" if fresh else "idle",
            updated=util.iso(at or None),
            source_type="talk", links=["session:" + sid] if sid else [],
            actions=[SURFACE],
            j_initiated=True,   # talk-bar interactions are the user's own asks
            detail={"session_id": sid, "opened_from": (rec.get("primary") or {}).get("opened_from"),
                    "started_at": at, "turns": (rec.get("surface") or {}).get("turns", 0)},
        ))
    out.sort(key=lambda b: -(b["detail"].get("started_at") or 0))
    return out


def fetch():
    data = util.read_json(util.oneguy("threads.json"), None)
    blocks = to_blocks(data)
    return {"blocks": blocks, "count": len(blocks)}
