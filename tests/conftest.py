"""Every test runs against a throwaway synthetic Hermes home. Never a real one."""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

VENDORED = ("board", "threads", "router", "talk_resolve", "feed")


def _forget():
    for name in list(sys.modules):
        top = name.split(".")[0]
        if top in VENDORED or top == "og_core" or name.startswith(("oneguy_routes_", "hermes_dashboard_plugin_1guy")):
            del sys.modules[name]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A synthetic Hermes home (sessions, cron jobs, tasklist) and a clean import state."""
    import make_home
    h = make_home.build(str(tmp_path / "hermes"))
    monkeypatch.setenv("HERMES_HOME", h)
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in [k for k in os.environ if k.startswith("ONEGUY_")]:
        monkeypatch.delenv(k)
    _forget()
    yield h
    _forget()


@pytest.fixture()
def sparse(home):
    """Turn the synthetic home into a day-one install: 3 chats, 2 jobs, no tasklist."""
    import make_home
    import shutil
    shutil.rmtree(home)
    make_home.build(home, sparse=True)
    return home


@pytest.fixture()
def api(home):
    """TestClient with the router mounted exactly where Hermes mounts it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location(
        "hermes_dashboard_plugin_1guy", os.path.join(ROOT, "dashboard", "plugin_api.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/1guy")

    class Client:
        def __init__(self):
            self.c, self.mod = TestClient(app), mod

        def get(self, path, **kw):
            return self.c.get("/api/plugins/1guy" + path, **kw)

        def post(self, path, body=None):
            return self.c.post("/api/plugins/1guy" + path, json=body or {})
    return Client()
