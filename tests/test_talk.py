"""Agent turns through the relay (a thread plays the desktop half), LLM seams, and the e2e."""
import json
import os
import subprocess
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fake_desktop(api, stop, seen, home=None):
    """What desktop/plugin.js does: claim jobs, run them on a (fake) gateway, push events."""
    n = 0
    while not stop.is_set():
        for job in api.get("/relay/next?wait=1").json()["jobs"]:
            seen.append(job)
            if job["kind"] == "create":
                n += 1
                if home:   # the real gateway writes the session row; the runner checks it exists
                    import sqlite3
                    con = sqlite3.connect(os.path.join(home, "state.db"))
                    con.execute("INSERT INTO sessions (id, source, started_at, title) VALUES (?,?,?,?)",
                                (f"sess_{n}", "desktop", time.time(), job["title"]))
                    con.commit()
                    con.close()
                ev = [{"event": "created", "data": {"session_id": f"sess_{n}", "title": job["title"]}}]
            elif job["kind"] == "turn":
                ev = [{"event": "run.started", "data": {"run_id": job["id"]}},
                      {"event": "assistant.delta", "data": {"delta": "On it. "}},
                      {"event": "assistant.delta", "data": {"delta": "Tomatoes go south."}},
                      {"event": "assistant.completed", "data": {"content": "On it. Tomatoes go south.", "completed": True}},
                      {"event": "run.completed", "data": {}}, {"event": "done", "data": {}}]
            else:
                continue
            api.post("/relay/push", {"id": job["id"], "events": ev})


def poll_until_done(api, block_id, run_id):
    text, since = "", 0
    for _ in range(40):
        p = api.get(f"/surface/poll?block_id={block_id}&run={run_id}&since={since}&wait=2").json()
        text += p["delta"]
        since = p["total"]
        if p["done"]:
            return text, p
    raise AssertionError("turn never finished")


def test_item_thread_runs_through_the_desktop_relay(api, home):
    stop, seen = threading.Event(), []
    t = threading.Thread(target=fake_desktop, args=(api, stop, seen, home), daemon=True)
    t.start()
    try:
        block = next(b for b in api.get("/board").json()["blocks"] if b["kind"] == "task" and "tomatoes" in b["title"])
        opened = api.post("/surface/open", {"block": block}).json()
        assert opened["session_id"] is None and opened["history"] == []          # opening never creates
        said = api.post("/surface/say", {"block_id": block["id"], "block": block, "text": "which bed?", "mode": "say"}).json()
        text, last = poll_until_done(api, block["id"], said["run_id"])
        assert text == "On it. Tomatoes go south." and last["status"] == "completed"
        assert [j["kind"] for j in seen] == ["create", "turn"]
        assert seen[0]["title"].startswith("1guy") and "tomatoes" in seen[0]["title"]
        assert seen[1]["session_id"] == "sess_1" and "which bed?" in seen[1]["message"]
        assert "/home/" not in seen[1]["message"]
        threads = api.get("/threads").json()
        (key, rec), = threads["threads"].items()
        assert key.startswith("task:^") and rec["primary"]["session_id"] == "sess_1"   # anchored, survives edits
        assert key[5:] in open(os.path.join(home, "tasklist.md")).read()
        # second turn: same session, no second create
        said = api.post("/surface/say", {"block_id": key, "text": "and the basil?", "mode": "say"}).json()
        poll_until_done(api, key, said["run_id"])
        assert [j["kind"] for j in seen] == ["create", "turn", "turn"] and seen[2]["session_id"] == "sess_1"
    finally:
        stop.set()
        t.join(timeout=5)


def test_turn_without_a_desktop_fails_in_plain_words(api, monkeypatch):
    from og_core import talk
    monkeypatch.setattr(talk, "CLAIM_TIMEOUT", 0.5)
    said = api.post("/surface/say", {"block_id": "task:zzz", "block": {"id": "task:zzz", "title": "x"}, "text": "hi", "mode": "say"}).json()
    _, last = poll_until_done(api, "task:zzz", said["run_id"])
    assert last["status"] == "failed" and "Hermes Desktop" in last["error"]
    assert api.get("/threads").json()["threads"] == {}                           # nothing half-recorded


def test_llm_calls_go_through_the_host_door(api):
    from og_core import talk

    class Door:
        calls = []

        def complete(self, messages, **kw):
            Door.calls.append((messages, kw))
            return type("R", (), {"text": '{"route": "todo", "todo_text": "buy stamps"}'})()
    talk.set_llm(Door())
    talk.routes()
    import router
    d = router.route("could you remind me about stamps at some point", faces=[], task_titles=[])
    assert d["route"] == "todo" and d["todo_text"] == "buy stamps"
    assert Door.calls and Door.calls[0][1]["purpose"].startswith("1guy.")
    assert talk.llm("ping") == '{"route": "todo", "todo_text": "buy stamps"}'


def test_register_hands_over_ctx_llm(home):
    import importlib.util
    spec = importlib.util.spec_from_file_location("plugin_root_1guy", os.path.join(ROOT, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    door = object()
    mod.register(type("Ctx", (), {"llm": door})())
    from og_core import talk
    assert talk._llm_door is door


@pytest.mark.skipif(not os.path.exists("/usr/bin/python3"), reason="needs system python with playwright")
def test_canvas_end_to_end_in_a_browser(tmp_path):
    probe = subprocess.run(["/usr/bin/python3", "-c", "import playwright"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("playwright not installed for /usr/bin/python3")
    env = dict(os.environ, XDG_CACHE_HOME=os.path.expanduser("~/.cache"))
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tests", "e2e_canvas.py"), "9192", str(tmp_path)],
                       capture_output=True, text=True, timeout=180, env=env)
    out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"failed": r.stderr[-800:]}
    assert r.returncode == 0 and out.get("ok"), out.get("failed")


def test_talk_takes_the_view_and_the_plugin_keeps_its_notes_in_its_own_folder(api, home):
    """Stream job 1 (view context): /talk accepts what the user is looking at. 'this' means the
    selected card, and layout problems the page measured go to a ring file in plugin-data."""
    from og_core import talk
    talk.routes()
    import router
    b = api.get("/board").json()
    task = next(x for x in b["blocks"] if x["kind"] == "task" and "dentist" in x["title"])
    kind = sorted(router._ISSUE_KINDS)[0]
    view = {"lens": "board", "selected": [task["id"]], "visible": [x["id"] for x in b["blocks"][:6]],
            "issues": [{"id": task["id"], "kind": kind}]}
    faces = [{"id": x["id"], "title": x["title"]} for x in b["blocks"]]
    new = lambda *a: {"route": "new", "title": "x"}                     # what the model would say
    d = router.route("deal with these first", faces=faces, task_titles=[], view=router.clean_view(view), classify=new)
    assert d["route"] == "block" and d["key"] == task["id"]            # "these" = the one selected card
    assert d["refers"] == {"what": "selected", "ids": [task["id"]]}
    d = router.route("move this to friday", faces=faces, task_titles=[], classify=new,
                     view=router.clean_view({"focused": task["id"]}))
    assert d["key"] == task["id"] and d["refers"]["what"] == "focused"   # "this" = the focused card
    r = api.post("/talk", {"text": "what is on my screen", "view": view})
    assert r.status_code == 200, r.text
    ring = os.path.join(home, "plugin-data", "1guy", "view-issues.jsonl")
    assert os.path.exists(ring) and task["id"] in open(ring).read()
    assert set(os.listdir(home)) <= {"state.db", "cron", "tasklist.md", "tasklist.md.lock", "plugin-data"}


def test_proposal_with_edits_applies_on_yes_and_undoes_as_one(api, home):
    """Stream job 2 (preview before yes): a proposal carries edits; yes carries them out through
    the plugin's own tasklist with one ledger line each; undo takes all of it back."""
    from og_core import board
    P = board.app().board.proposals     # writer 'planner': untangle owns (and rewrites) its own file
    b = api.get("/board?fresh=1").json()
    tasks = [x for x in b["blocks"] if x["kind"] == "task" and x["status"] != "done"]
    dentist = next(x for x in tasks if "dentist" in x["title"])
    boiler = next(x for x in tasks if "boiler" in x["title"])
    P.put("planner", [{"id": "u1", "kind": "pile", "title": "Two for the house pile?",
                        "edits": [{"op": "move", "id": boiler["id"], "to_pile": "pile:house"},
                                  {"op": "schedule", "id": dentist["id"], "when": "2031-02-03"}]}])
    b = api.get("/board?fresh=1").json()
    assert "Two for the house pile?" in [x["title"] for x in b["blocks"] if x["kind"] == "proposal"]

    r = api.post("/brain/proposal", {"id": "u1", "writer": "planner", "op": "keep"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["status"] == "kept" and len(out["applied"]) == 2 and out["undo"]["token"]
    text = open(os.path.join(home, "tasklist.md")).read()
    assert "@2031-02-03" in next(ln for ln in text.splitlines() if "dentist" in ln)
    ledger = [json.loads(ln) for ln in open(os.path.join(home, "plugin-data", "1guy", "judgments.jsonl"))]
    assert [l["kind"] for l in ledger] == ["cluster", "schedule"] and all("planner/u1" in l["note"] for l in ledger)
    assert api.post("/brain/proposal", {"id": "u1", "writer": "planner", "op": "keep"}).status_code == 409

    r = api.post("/brain/proposal", {"op": "undo", "token": out["undo"]["token"]})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert "@2031-02-03" not in open(os.path.join(home, "tasklist.md")).read()
    b = api.get("/board?fresh=1").json()
    assert "Two for the house pile?" in [x["title"] for x in b["blocks"] if x["kind"] == "proposal"]   # proposed again


def test_plain_proposals_keep_and_dismiss_without_the_crew_library(api, home):
    from og_core import board
    P = board.app().board.proposals
    P.put("watcher", [{"id": "w1", "kind": "task", "title": "Renew the passport", "draft": "Renew the passport #admin"},
                      {"id": "w2", "kind": "idea", "title": "Try a weekly review"}])
    r = api.post("/brain/proposal", {"id": "w1", "writer": "watcher", "op": "keep"})
    assert r.status_code == 200 and r.json()["status"] == "kept", r.text
    assert "- [ ] Renew the passport #admin" in open(os.path.join(home, "tasklist.md")).read()
    assert api.post("/brain/proposal", {"id": "w2", "writer": "watcher", "op": "dismiss"}).json()["status"] == "dismissed"
    assert api.post("/brain/proposal", {"id": "w2", "writer": "watcher", "op": "keep"}).status_code == 409
    assert api.post("/brain/proposal", {"id": "nope", "writer": "watcher", "op": "keep"}).status_code == 404
    assert api.post("/brain/proposal", {"id": "w1", "writer": "../x", "op": "keep"}).status_code == 400
    left = [x["id"] for x in api.get("/board?fresh=1").json()["blocks"] if x["kind"] == "proposal"]
    assert not [i for i in left if "w1" in i or "w2" in i]


def test_door_b_and_the_sample_desk_in_a_browser(tmp_path):
    probe = subprocess.run(["/usr/bin/python3", "-c", "import playwright"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("playwright not installed for /usr/bin/python3")
    env = dict(os.environ, XDG_CACHE_HOME=os.path.expanduser("~/.cache"))
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tests", "e2e_doors.py"), "9194", str(tmp_path)],
                       capture_output=True, text=True, timeout=180, env=env)
    out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"failed": r.stderr[-800:]}
    assert r.returncode == 0 and out.get("ok"), out.get("failed")


def test_frontier_patterns_work_inside_the_frame(tmp_path):
    """Preview before yes, change outlines, the lasso and the claim frame, through the loader and
    the fetch bridge in a real browser (tests/e2e_frontier.py)."""
    probe = subprocess.run(["/usr/bin/python3", "-c", "import playwright"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("playwright not installed for /usr/bin/python3")
    env = dict(os.environ, XDG_CACHE_HOME=os.path.expanduser("~/.cache"))
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tests", "e2e_frontier.py"), "9192", str(tmp_path)],
                       capture_output=True, text=True, timeout=240, env=env)
    out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"failed": r.stderr[-800:]}
    assert r.returncode == 0 and not out.get("failed") and not out.get("problems"), out


def test_the_inbox_as_next_actions_works_inside_the_frame(tmp_path):
    """#23 in a real browser through the loader and the fetch bridge: people first, a row opens in
    place, `not mine` hides it with one ledger line (tests/e2e_inbox.py)."""
    probe = subprocess.run(["/usr/bin/python3", "-c", "import playwright"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("playwright not installed for /usr/bin/python3")
    env = dict(os.environ, XDG_CACHE_HOME=os.path.expanduser("~/.cache"))
    r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tests", "e2e_inbox.py"), "9191", str(tmp_path)],
                       capture_output=True, text=True, timeout=240, env=env)
    out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"failed": r.stderr[-800:]}
    assert r.returncode == 0 and not out.get("failed") and not out.get("problems"), out


def test_lasso_two_cards_and_ask_opens_one_new_thread_about_both(api, home):
    """Stream job 7 (lasso to ask): a line sent with two or more cards selected opens a NEW
    thread that names them, through the same desktop relay as any other turn."""
    stop, seen = threading.Event(), []
    t = threading.Thread(target=fake_desktop, args=(api, stop, seen, home), daemon=True)
    t.start()
    try:
        b = api.get("/board").json()
        tasks = [x for x in b["blocks"] if x["kind"] == "task" and x["status"] != "done"]
        dentist = next(x for x in tasks if "dentist" in x["title"])
        boiler = next(x for x in tasks if "boiler" in x["title"])
        r = api.post("/talk", {"text": "which of these is more urgent?",
                               "view": {"lens": "board", "selected": [dentist["id"], boiler["id"]]}})
        assert r.status_code == 200, r.text
        route = r.json()["route"]
        assert route["kind"] == "selection" and set(route["ids"]) == {dentist["id"], boiler["id"]}
        text, last = poll_until_done(api, route["key"], route["run_id"])
        assert last["status"] == "completed" and text
        assert [j["kind"] for j in seen] == ["create", "turn"]
        message = seen[1]["message"]
        assert "dentist" in message and "boiler" in message and "which of these is more urgent?" in message
        threads = api.get("/threads").json()["threads"]
        assert route["key"] in threads and len(threads) == 1            # one thread, neither card's own
    finally:
        stop.set()
        t.join(timeout=5)


def test_turn_a_pile_into_a_brief_with_and_without_a_model(api, home):
    """Stream job 'brief': a pile of cards becomes one written brief, filed as a card in the pile.
    One call on the host model; with no model it still writes a plain one. Stays in plugin-data."""
    from og_core import talk
    b = api.get("/board?fresh=1").json()
    garden = [x["id"] for x in b["blocks"] if x["kind"] == "task" and "garden" in (x.get("tags") or [])]
    assert len(garden) >= 2
    talk.set_llm(None)
    r = api.post("/brief", {"ids": garden, "name": "garden"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["by"] == "plain", r.text
    assert "path" not in r.json()

    seen = []

    class Door:
        def complete(self, messages, **kw):
            seen.append(messages[0]["content"])
            return type("R", (), {"text": "# Garden plan\n\n## Where it stands\n\nOrder the seed potatoes first, then settle the tomato bed.\n"})()
    talk.set_llm(Door())
    out = api.post("/brief", {"ids": garden, "name": "garden"}).json()
    assert out["ok"] and out["by"] == "model" and out["title"] == "Garden plan" and set(out["sources"]) == set(garden)
    assert len(seen) == 1 and "seed potatoes" in seen[0]                 # the cards went to the host model, once
    briefs = os.path.join(home, "plugin-data", "1guy", "briefs")
    assert len(os.listdir(briefs)) == 2 and os.path.exists(os.path.join(home, "plugin-data", "1guy", "artifacts.jsonl"))
    card = next(x for x in api.get("/board?fresh=1").json()["blocks"] if x["id"] == out["block_id"])
    assert "settle the tomato bed" in json.dumps(card["detail"])
    assert api.post("/brief", {"ids": []}).status_code == 400
    assert not os.path.exists(os.path.join(os.path.expanduser("~"), ".hermes", "1guy"))   # nothing beside the Hermes home
