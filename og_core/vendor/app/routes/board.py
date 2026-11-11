"""WS-B: the board. GET /api/board and its write actions.

  GET  /api/board            -> the unified, attention-ranked board (contract shape)
       ?fresh=1               bypass the per-source caches
       ?explain=1             add _why (attention parts) to every block
       ?here=1                he has been at the desk since the last load: what appears now is not
                              marked as having moved while he was away (board/briefing.py moved)
       ?only=tasks,jobs       just these sources (debug)
       ?sample=1              the sample desk: the synthetic demo world, read-only (board/sample.py).
                              Reads one bundled file; touches no real source and writes nothing.
  POST /api/board/task       {id, op: toggle|edit|move|add|delete, ...} -> tasklist API
  POST /api/board/job        {job_id, op: "run"} -> `hermes cron run <id>` (background)

All logic lives in app/board/ (one module per source + attention + actions).
"""
import board as _board            # app/board/ (app/ is on sys.path under serve.py)
from board import actions as _actions
from board import sample as _sample


def _flag(query, key):
    return str(query.get(key, "")).lower() in ("1", "true", "yes")


def get_board(query, body):
    if _flag(query, "sample"):
        return _sample.board()
    only = [s for s in (query.get("only") or "").split(",") if s] or None
    if only:
        res = _board.run_sources(force=_flag(query, "fresh"), only=only)
        return _board.assemble(res, explain=_flag(query, "explain"))
    return _board.build_board(force=_flag(query, "fresh"), explain=_flag(query, "explain"), here=_flag(query, "here"))


def post_task(query, body):
    res, status = _actions.task_action(body)
    _board.invalidate("tasks")
    return res, status


def post_job(query, body):
    res, status = _actions.job_action(body)
    if res.get("ok"):
        _board.invalidate("jobs")
    return res, status


ROUTES = {
    ("GET", "/api/board"): get_board,
    ("POST", "/api/board/task"): post_task,
    ("POST", "/api/board/job"): post_job,
}
