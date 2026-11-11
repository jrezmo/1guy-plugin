"""Stage log (Phase 4, A4 L6): every stage open/hold is logged, urgent unasked opens included.

POST /api/stage/log {op, kind, title, src?, reason?, urgent?, at?} -> appends one JSON line to
$ONEGUY_HOME/logs/stage.jsonl (default ~/.hermes/1guy). GET /api/stage/log?n=20 -> last rows.
"""
import json
import os
import time

FIELDS = {"op": 16, "kind": 16, "title": 160, "src": 300, "reason": 16, "at": 40}


def log_path():
    home = os.path.expanduser(os.environ.get("ONEGUY_HOME", "~/.hermes/1guy"))
    return os.path.join(home, "logs", "stage.jsonl")


def r_log(query, body):
    if not isinstance(body, dict):
        raise ValueError("json object body required")
    op = body.get("op")
    if op not in ("open", "held"):
        raise ValueError("op must be open|held")
    row = {k: str(body.get(k) or "")[:n] for k, n in FIELDS.items() if body.get(k)}
    row["urgent"] = bool(body.get("urgent"))
    row["logged"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p = log_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")
    return {"ok": True}


def r_tail(query, body):
    try:
        n = max(1, min(200, int((query or {}).get("n", 20))))
    except ValueError:
        raise ValueError("n must be a number")
    p = log_path()
    if not os.path.isfile(p):
        return {"rows": []}
    with open(p) as f:
        lines = f.readlines()[-n:]
    rows = []
    for ln in lines:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    return {"rows": rows}


ROUTES = {("POST", "/api/stage/log"): r_log, ("GET", "/api/stage/log"): r_tail}
