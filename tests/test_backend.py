"""The backend against a synthetic Hermes home: board, tasklist, canvas routes, page."""
import json
import os


def blocks(api, **kw):
    r = api.get("/board?fresh=1")
    assert r.status_code == 200
    return r.json()


def test_board_reads_the_install(api, home):
    b = blocks(api)
    assert all(s["ok"] for s in b["sources"].values()), b["sources"]
    assert set(b["sources"]) == {"tasks", "sessions", "jobs", "brain", "proposals", "artifacts", "talk", "widgets"}
    kinds = {x["kind"] for x in b["blocks"]}
    assert {"task", "session", "job", "widget"} <= kinds
    titles = [x["title"] for x in b["blocks"]]
    assert "backup-notes" in titles and "Plan the garden beds for spring" in titles
    top = b["blocks"][0]
    assert top["title"] == "backup-notes" and top["status"] == "failed"      # what broke ranks first
    assert b["briefing"]["line"].startswith("i went through your hermes.")   # door A on an untouched home
    assert top["id"] in b["briefing"]["for_you"]


def test_board_on_an_empty_home_is_calm(api, home):
    os.remove(os.path.join(home, "state.db"))
    os.remove(os.path.join(home, "cron", "jobs.json"))
    os.remove(os.path.join(home, "tasklist.md"))
    b = blocks(api)
    assert all(s["ok"] for s in b["sources"].values()), b["sources"]
    assert {x["id"] for x in b["blocks"]} == {"widget:todo", "widget:calendar", "widget:email"}
    assert os.path.exists(os.path.join(home, "tasklist.md"))               # created on first read
    assert not os.path.exists(os.path.join(home, "state.db"))              # never created by us


def test_private_sources_are_not_loaded(api):
    b = blocks(api)
    assert not {"crew", "media", "plugins"} & set(b["sources"])
    glances = {x["id"]: x for x in b["blocks"] if x["kind"] == "widget"}
    assert set(glances) == {"widget:todo", "widget:calendar", "widget:email"}
    # this synthetic Hermes has no mail or calendar skill: the tiles say so and carry no data
    assert "can't read email yet" in glances["widget:email"]["summary"]
    assert "can't read your calendar yet" in glances["widget:calendar"]["summary"]


def test_tasklist_roundtrip_and_board_actions(api, home):
    r = api.post("/add", {"section": "To-do", "text": "Water the ferns #garden"})
    assert r.status_code == 200 and r.json()["ok"]
    items = [i for s in api.get("/tasks").json()["sections"] for i in s["items"]]
    fern = next(i for i in items if i["text"] == "Water the ferns")
    assert fern["tags"] == ["garden"]
    r = api.post("/board/task", {"id": "task:" + fern["id"], "op": "toggle"})
    assert r.status_code == 200, r.text
    assert "- [x] Water the ferns #garden" in open(os.path.join(home, "tasklist.md")).read()
    assert api.post("/toggle", {"id": "nope"}).status_code == 404


def test_state_stays_inside_the_plugin_data_dir(api, home):
    assert api.post("/judgment", {"kind": "park", "block": "task:x"}).status_code == 200
    assert api.post("/home/state?ns=live", {"placements": {}, "view": {"level": "blocks", "x": 12}}).status_code == 200
    blocks(api)
    data = os.path.join(home, "plugin-data", "1guy")
    assert os.path.exists(os.path.join(data, "judgments.jsonl"))
    assert os.path.exists(os.path.join(data, "home-state.json"))
    top = set(os.listdir(home))
    assert top <= {"state.db", "cron", "tasklist.md", "tasklist.md.lock", "plugin-data"}, top
    assert api.get("/home/state?ns=live").json()["view"] == {"level": "blocks", "x": 12}


def test_unknown_route_and_bad_input(api):
    assert api.get("/nope").status_code == 404
    assert api.post("/judgment", {"kind": "explode"}).status_code == 400
    listing = api.get("/routes").json()
    assert listing["errors"] == {}
    assert "POST /talk" in listing["routes"] and "POST /threads/open" in listing["routes"]


def test_canvas_page_is_one_self_contained_document(api):
    r = api.get("/app/page").json()
    html = r["html"]
    assert r["version"] and html.lstrip().startswith("<!doctype html>")
    assert 'rel="stylesheet"' not in html and 'src="/app' not in html
    assert "oneguy:rest" in html and "ONEGUY_EMBED" in html
    import re
    leak = re.search(r"(?<![\w./])/home/[a-z]+/|\.ts\.net|127\.0\.0\.1:\d|@gmail\.", html)
    assert not leak, leak.group(0)


def test_ticker_is_plain_words(api):
    t = api.get("/ticker").json()
    assert t["ok"] and t["waiting_on_you"] == 2 and len(t["chips"]) == 2
    texts = [c["text"] for c in t["chips"]] + [i["text"] for i in t["items"]]
    assert any("backup" in x for x in texts)
    assert not any(("#" in x or "^" in x or "(from:" in x) for x in texts)


def test_talk_bar_adds_a_todo_without_any_model(api, home):
    r = api.post("/talk", {"text": "add buy stamps to my list"})
    assert r.status_code == 200, r.text
    assert "buy stamps" in open(os.path.join(home, "tasklist.md")).read().lower(), json.dumps(r.json())[:400]


def test_every_read_route_the_canvas_polls_answers(api):
    for path in ("/feed?live=1", "/surface/list", "/talk/history", "/threads", "/judgments", "/stage/log"):
        r = api.get(path)
        assert r.status_code == 200, (path, r.text[:200])


def test_headlines_use_the_host_model_and_can_be_turned_off(api):
    from og_core import board, talk
    seen = []

    class Door:
        def complete(self, messages, **kw):
            seen.append(messages[0]["content"])
            return type("R", (), {"text": "{}"})()
    talk.set_llm(Door())
    app = board.app()
    assert app.headlines.chat("say hi") == "{}" and seen == ["say hi"]
    assert app.headlines.api_key() == ""                     # no key file is ever consulted


def test_diag_keeps_reports_short_capped_and_free_of_home_paths(api, home):
    from og_core import store
    r = api.post("/diag", {"meta": {"version": "9.9.9", "ua": "TestApp/1", "secret": "x"}, "entries": [
        {"kind": "error", "message": "boom at /Users/someone/Library/thing.js and /home/someone/x.py " + "y" * 900, "where": "canvas:12:3"},
        {"kind": "t:first_cards", "message": "first cards on screen", "ms": 812.4},
        "not an entry"]})
    assert r.json() == {"ok": True, "kept": 2}
    d = api.get("/diag").json()
    assert d["plugin_version"] and d["canvas"] and d["problem_count"] == 1 and d["by_kind"] == {"error": 1}
    assert d["last_load"]["first_cards"]["ms"] == 812 and d["backend"]["blocks"] > 10
    err = d["entries"][0]
    assert "someone" not in err["message"] and "~/Library/thing.js" in err["message"] and len(err["message"]) <= 300
    assert err["version"] == "9.9.9" and err["ua"] == "TestApp/1" and "secret" not in err
    for _ in range(30):
        api.post("/diag", {"entries": [{"kind": "error", "message": f"e{i}"} for i in range(40)]})
    lines = open(os.path.join(home, "plugin-data", "1guy", "client-errors.jsonl")).read().splitlines()
    assert len(lines) == store.DIAG_KEEP and len(api.get("/diag").json()["entries"]) == 50


def test_data_folder_from_the_old_plugin_id_is_moved_once(home):
    """Up to 1.0.1 the plugin's id was 'oneguy'. Its data folder moves to plugin-data/1guy."""
    old = os.path.join(home, "plugin-data", "oneguy")
    os.makedirs(old)
    with open(os.path.join(old, "threads.json"), "w") as f:
        f.write('{"version": 1, "threads": {"task:^abcd": {"primary": {"session_id": "s1"}}}, "aliases": {}}')
    from og_core import store
    d = store.data_dir()
    assert str(d).endswith(os.path.join("plugin-data", "1guy")) and not os.path.exists(old)
    assert "task:^abcd" in open(d / "threads.json").read()
    os.makedirs(old)                                  # a stray old folder later is left alone
    assert store.data_dir() == d and os.path.isdir(old)


def test_a_200_card_board_stays_fast(home, api):
    """A heavy user's install (about 170 cards on the board): the cold board and the canvas
    document must each answer well inside the 1.5 s first-view budget."""
    import shutil
    import time
    import make_home
    shutil.rmtree(home)
    make_home.build(home, heavy=True)
    t0 = time.time()
    b = api.get("/board?fresh=1").json()
    cold = time.time() - t0
    t0 = time.time()
    api.get("/board")
    warm = time.time() - t0
    t0 = time.time()
    page = api.get("/app/page").json()
    page_s = time.time() - t0
    assert len(b["blocks"]) >= 150 and all(s["ok"] for s in b["sources"].values())
    assert cold < 1.0 and warm < 0.3 and page_s < 0.3, (cold, warm, page_s)
    assert page["bytes"] < 800_000        # the canvas document; grows only when art is added


def test_dropping_a_card_on_a_day_schedules_it_in_the_tasklist(api, home):
    """The Time lens: a drop is `schedule`. It anchors the line, writes the date, and can undo it."""
    b = api.get("/board?fresh=1").json()
    task = next(x for x in b["blocks"] if x["kind"] == "task" and x["title"].startswith("Book the dentist"))
    r = api.post("/board/task", {"id": task["id"], "op": "schedule", "due": "2031-01-05T17:15"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    line = next(ln for ln in open(os.path.join(home, "tasklist.md")).read().splitlines() if "dentist" in ln)
    assert "@2031-01-05" in line and "17:15" in line and " ^" in line and "#health" in line
    b = api.get("/board?fresh=1").json()
    again = next(x for x in b["blocks"] if x["kind"] == "task" and x["title"].startswith("Book the dentist"))
    assert again["id"].startswith("task:^") and str(again["due"]).startswith("2031-01-05")
    assert api.post("/board/task", {"id": again["id"], "op": "schedule", "due": None}).json()["ok"]
    assert "@2031" not in open(os.path.join(home, "tasklist.md")).read()
    assert api.post("/board/task", {"id": again["id"], "op": "schedule", "due": "next tuesday"}).status_code == 400
    assert api.post("/judgment", {"kind": "schedule", "block": again["id"], "target": "day:2"}).status_code == 200


def test_no_vendored_module_points_outside_this_hermes_home(api, home):
    """Several vendored modules compute a path at import time. Every one of them must end up
    inside this Hermes home (most inside plugin-data/1guy), never under the user's ~/.hermes/1guy."""
    import sys
    from og_core import board, talk
    board.app()
    talk.routes()
    user_home = os.path.expanduser("~")
    stray = []
    for name, mod in list(sys.modules.items()):
        top = name.split(".")[0]
        if not (top in ("board", "threads", "router", "talk_resolve", "feed") or name.startswith("oneguy_routes_")):
            continue
        for attr, val in vars(mod).items():
            if isinstance(val, str) and val.startswith(user_home) and not val.startswith(home) and "vendor" not in val:
                stray.append(f"{name}.{attr} = {val}")
    assert not stray, stray
    assert board.app().board.artifacts.REGISTRY == os.path.join(home, "plugin-data", "1guy", "artifacts.jsonl")


def test_nothing_sent_to_the_model_names_the_first_user():
    """1guy's source calls its first user by his initial; the vendor step says "the user"
    instead. No python string in the plugin may carry the bare initial (a card would say it)."""
    import io, pathlib, re, tokenize
    root = pathlib.Path(__file__).resolve().parent.parent / "og_core"
    bad = []
    for f in root.rglob("*.py"):
        for t in tokenize.generate_tokens(io.StringIO(f.read_text(encoding="utf-8")).readline):
            if t.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)) and \
                    re.search(r"(?:\b|(?<=\\n))J\b", t.string):
                bad.append(f"{f.relative_to(root)}:{t.start[0]}")
    assert not bad, bad
