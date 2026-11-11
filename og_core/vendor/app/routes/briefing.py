"""Phase 3.5 (A6): cycling the three. POST /api/briefing/seen {id} records that
the user focused/opened a pick, so it drops down the pool (board/selector.py)."""
import board as _board            # app/board/ (app/ is on sys.path under serve.py)


def post_seen(query, body):
    key = (body or {}).get("id")
    if not key or not isinstance(key, str):
        raise ValueError("id required")
    _board.selector.record_opened(key)
    return {"ok": True, "id": key}


def post_look(query, body):
    """Frontier 3 (change outlines): POST /api/seen {board?: true, id?: "<block id>"}. The user looked at
    the board (visible a few seconds) and/or focused an item, which takes its "what moved" mark
    off. The logic is board/briefing.py look(); this is the thin HTTP face."""
    body = body or {}
    key = body.get("id")
    if key is not None and (not isinstance(key, str) or not key or len(key) > 200):
        raise ValueError("id must be a block id")
    if not key and not body.get("board"):
        raise ValueError("board or id required")
    return dict(_board.briefing.look(board=bool(body.get("board")), item=key), ok=True)


ROUTES = {
    ("POST", "/api/seen"): post_look,
    ("POST", "/api/briefing/seen"): post_seen,
}
