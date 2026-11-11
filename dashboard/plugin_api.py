"""1guy backend: the thin router. Mounted by Hermes at /api/plugins/1guy/.

  GET  /app/page          the canvas as one self-contained document, plus the small loader
                          page the frame navigates to (JSON {boot, html, version, bytes})
  *    /{canvas route}    /board /talk /judgment /thread/open /surface/* /home/state ...
                          -> og_core.talk.dispatch (the vendored 1guy route table)
  GET  /relay/next        the desktop half claims agent-turn jobs      (see og_core.talk)
  POST /relay/push        ... and pushes what streamed
  POST /local             the canvas's small remembered choices (it has no localStorage)
  GET  /diag             remote eyes: what the app last reported about itself + backend state
  POST /diag             the desktop half / canvas report errors, policy refusals, timings
  GET  /ticker            the docked strip's digest of the board
  GET  /tasks, POST /toggle /add /edit /move /delete /clear-done /anchor   the shared tasklist

All logic lives in og_core (store, tasklist, board, talk).
"""
from __future__ import annotations

import json
import os
import sys

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from og_core import board, store, talk, tasklist  # noqa: E402

router = APIRouter()
_STATIC = os.path.join(_ROOT, "og_core", "static", "canvas.html")
_BOOT = os.path.join(_ROOT, "og_core", "static", "boot.html")
_VENDOR = os.path.join(_ROOT, "og_core", "vendor", "VENDOR.json")


def _canvas_version() -> str:
    try:
        with open(_VENDOR, encoding="utf-8") as f:
            return json.load(f).get("sha", "")[:10]
    except (OSError, ValueError):
        return ""


def _plugin_version() -> str:
    try:
        with open(os.path.join(_ROOT, "plugin.yaml"), encoding="utf-8") as f:
            return next((ln.split(":", 1)[1].strip().strip('"') for ln in f if ln.startswith("version:")), "")
    except OSError:
        return ""


@router.get("/app/page")
def app_page() -> dict:
    with open(_STATIC, encoding="utf-8") as f:
        html = f.read()
    version = _canvas_version()
    with open(_BOOT, encoding="utf-8") as f:
        boot = f.read()
    return {"boot": boot, "html": html, "version": version, "bytes": len(html.encode("utf-8")),
            "local": store.local_prefs()}


@router.post("/local")
def local(body: dict) -> dict:
    return {"ok": True, "count": len(store.local_prefs(body.get("data") if isinstance(body.get("data"), dict) else {}))}


@router.post("/diag")
def diag_report(body: dict) -> dict:
    return {"ok": True, "kept": store.diag_append(body.get("entries") or [], body.get("meta") or {})}


@router.get("/diag")
def diag() -> dict:
    """Remote eyes: what the real app last reported, plus enough backend state to read it by."""
    import time
    entries = store.diag_tail(50)
    last_load = {}
    for e in entries:                       # the newest value of each timing
        if e.get("kind", "").startswith("t:"):
            last_load[e["kind"][2:]] = {"ms": e.get("ms"), "at": e.get("at")}
    t0 = time.time()
    try:
        b = board.build()
        backend = {"board_ms": round((time.time() - t0) * 1000), "blocks": len(b.get("blocks") or []),
                   "sources": {k: ("ok" if v.get("ok") else v.get("error", "error")) for k, v in b["sources"].items()}}
    except Exception as e:
        backend = {"error": f"{type(e).__name__}: {e}"[:200]}
    problems = [e for e in entries if not e.get("kind", "").startswith("t:")]
    return {"plugin_version": _plugin_version(), "canvas": _canvas_version(), "now": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "desktop_attached": talk.RELAY.attached(), "llm_door": talk._llm_door is not None,
            "last_load": last_load, "problem_count": len(problems),
            "by_kind": {k: sum(1 for e in problems if e.get("kind") == k) for k in sorted({e.get("kind") for e in problems})},
            "backend": backend, "entries": entries}


@router.get("/ticker")
def ticker() -> dict:
    return board.ticker()


@router.get("/relay/next")
def relay_next(wait: float = 20.0) -> dict:
    return {"jobs": talk.RELAY.next(wait)}


@router.post("/relay/push")
def relay_push(body: dict) -> dict:
    return {"ok": talk.RELAY.push(str(body.get("id") or ""), body.get("events") or [])}


# ---- the shared tasklist (plain functions with pydantic bodies; FastAPI validates) ----
router.get("/tasks")(tasklist.tasks)
for _route, (_fn, _model) in tasklist.ROUTES.items():
    router.post("/" + _route)(_fn)


# ---- every canvas route, last so the explicit routes above win ----
@router.api_route("/{path:path}", methods=["GET", "POST"])
def canvas(path: str, request: Request, body: dict | None = None) -> JSONResponse:
    obj, status = talk.dispatch(request.method, path, dict(request.query_params), body)
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})
