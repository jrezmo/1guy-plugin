#!/usr/bin/env python3
"""Scratch server for the harness: the plugin's real router at /api/plugins/1guy (exactly
where Hermes mounts it) plus the harness page and desktop/plugin.js as static files.

    HERMES_HOME=<scratch> <hermes venv python> tests/harness/serve.py <port>

Proxy mode (tests/stranger_install.sh): with ONEGUY_UPSTREAM=http://127.0.0.1:<port> and
ONEGUY_UPSTREAM_TOKEN set, the router is NOT loaded here; /api/plugins/1guy/* is forwarded to a
real `hermes dashboard` that has the plugin installed, with its session token attached (the job
the desktop app's ctx.rest does).

Binds 127.0.0.1 only. Refuses to start without an explicit HERMES_HOME so it can never
touch a real Hermes home by accident.
"""
import importlib.util
import os
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def proxy_app(upstream, token):
    import urllib.error
    import urllib.request
    from fastapi import Request
    from fastapi.responses import Response
    app = FastAPI()

    @app.api_route("/api/plugins/1guy/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        url = f"{upstream}/api/plugins/1guy/{path}"
        if request.url.query:
            url += "?" + request.url.query
        body = await request.body()
        req = urllib.request.Request(url, data=body or None, method=request.method, headers={
            "X-Hermes-Session-Token": token, "Content-Type": "application/json"})

        def go():
            try:
                with urllib.request.urlopen(req, timeout=70) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        import anyio
        status, data = await anyio.to_thread.run_sync(go)
        return Response(data, status_code=status, media_type="application/json")
    _static(app)
    return app


def reset_world():
    """GET /harness/reset: put the synthetic world back the way it was built, so a walk that
    used the rail up can run again (QA gates two widths back to back). Only works when the
    launcher gave us the scratch home and a snapshot of it (tests/preview.sh does); it restores
    the snapshot's files and empties the plugin's own data folder. Never touches anything else."""
    import shutil
    home, snap = os.environ.get("ONEGUY_RESET_HOME"), os.environ.get("ONEGUY_RESET_SNAPSHOT")
    if not (home and snap and os.path.isdir(snap) and os.path.isdir(home)):
        return {"ok": False, "error": "this harness was started without a resettable world"}
    data = os.path.join(home, "plugin-data", "1guy")
    if os.path.isdir(data):
        for name in os.listdir(data):
            path = os.path.join(data, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    restored = []
    for name in ("state.db", "tasklist.md", "cron"):
        src, dst = os.path.join(snap, name), os.path.join(home, name)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        elif os.path.exists(dst):
            os.remove(dst)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            restored.append(name)
        elif os.path.exists(src):
            shutil.copy2(src, dst)
            restored.append(name)
    return {"ok": True, "restored": restored, "note": "reload the page; the board re-reads within a few seconds"}


def _static(app):
    from fastapi.responses import RedirectResponse
    app.get("/harness/reset")(reset_world)
    app.get("/")(lambda: RedirectResponse("/harness/index.html"))
    app.mount("/desktop", StaticFiles(directory=os.path.join(ROOT, "desktop")))
    app.mount("/harness", StaticFiles(directory=HERE, html=True))


def make_app():
    if os.environ.get("ONEGUY_UPSTREAM"):
        return proxy_app(os.environ["ONEGUY_UPSTREAM"].rstrip("/"), os.environ.get("ONEGUY_UPSTREAM_TOKEN", ""))
    if not os.environ.get("HERMES_HOME"):
        sys.exit("set HERMES_HOME to a scratch directory")
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_1guy", os.path.join(ROOT, "dashboard", "plugin_api.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/1guy")
    _static(app)
    return app


if __name__ == "__main__":
    uvicorn.run(make_app(), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 9190,
                log_level="warning")
