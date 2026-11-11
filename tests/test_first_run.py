"""The first run on a nearly empty Hermes: he says hello and offers real places to start."""
import json
import os


def board(api):
    return api.get("/board?fresh=1").json()


def test_sparse_install_door_a_counts_the_home_and_the_starters_fill_the_rail(sparse, api):
    """Door A speaks on any home with chats or jobs. With nothing pressing its line says so, and
    the plugin's starters keep the first screen from being an empty rail."""
    b = board(api)
    br, by_id = b["briefing"], {x["id"]: x for x in b["blocks"]}
    assert br["by"] == "door"
    assert br["line"] == "i went through your hermes. 3 chats in two weeks, 2 scheduled jobs. nothing needs you right now."
    picks = [by_id[i] for i in br["for_you"]]
    assert b["first_run"] == "a" and b["door"]["close"] == "pick one and i'll take it from here."
    assert [p["kind"] for p in picks] == ["session", "job"]
    assert picks[0]["fy_why"] == "your latest chat"
    assert picks[1]["fy_why"] == "runs every day at 07:00" and picks[1]["status"] != "done"   # shows on the rail
    assert all(s["ok"] for s in b["sources"].values()), b["sources"]


def test_two_weeks_of_chats_are_counted_but_only_real_ones_become_cards(sparse, api):
    b = board(api)
    assert b["history"]["chats"] == 3                                  # counted, every one of them real
    titles = {x["title"] for x in b["blocks"] if x["kind"] == "session"}
    assert "Summarise the quarterly report" in titles


def test_empty_home_opens_on_door_b_and_a_typed_line_becomes_a_todo(sparse, api):
    """Door B: nothing to go through, so he asks. Each line the user writes is a to-do in the
    plugin's own tasklist; after the first judgment the first run is over."""
    os.remove(os.path.join(sparse, "state.db"))
    os.remove(os.path.join(sparse, "cron", "jobs.json"))
    b = board(api)
    assert b["first_run"] == "b" and b["briefing"]["line"] == "what are you in the middle of?"
    assert b["door"]["sample"] == "look around a sample desk" and b["door"]["hint"]
    assert not [x for x in b["blocks"] if x["kind"] in ("session", "job", "task", "note")]   # nothing invented
    for line in ("finish the deck for thursday", "book the flights", "call the plumber"):
        r = api.post("/board/task", {"op": "add", "text": line})
        assert r.status_code == 200 and r.json()["ok"], r.text
    text = open(os.path.join(sparse, "tasklist.md")).read()
    assert "- [ ] finish the deck for thursday" in text and "- [ ] call the plumber" in text
    b = board(api)
    assert sum(1 for x in b["blocks"] if x["kind"] == "task") == 3 and b["first_run"] == "b"
    assert api.post("/judgment", {"kind": "done", "block": "task:x"}).status_code == 200
    assert board(api)["first_run"] is None


def test_sample_desk_is_read_only_and_marked(sparse, api):
    """`look around a sample desk`: a bundled synthetic world, every card marked sample, served
    from one file. It reads nothing of the user's and writes nothing."""
    import hashlib

    def fingerprint():
        h = hashlib.sha1()
        for base, _dirs, files in sorted(os.walk(sparse)):
            for name in sorted(files):
                if name.endswith(".lock"):
                    continue
                h.update(os.path.join(base, name).encode())
                h.update(open(os.path.join(base, name), "rb").read())
        return h.hexdigest()
    import time
    board(api)                                   # let the real desk create what it creates,
    before = fingerprint()                       # including what its background passes write
    for _ in range(40):
        time.sleep(0.25)
        again = fingerprint()
        if again == before:
            break
        before = again
    s = api.get("/board?sample=1").json()
    assert len(s["blocks"]) > 10 and all(x.get("sample") for x in s["blocks"])
    import re
    leak = re.search(r"(?<![\w./])/home/[a-z]+/|\.ts\.net|127\.0\.0\.1:\d|@gmail\.", json.dumps(s))
    assert not leak, leak.group(0)
    assert fingerprint() == before


def test_first_run_steps_aside_once_the_user_has_started(sparse, api):
    assert api.post("/add", {"section": "To-do", "text": "Call the dentist"}).status_code == 200
    b = board(api)
    assert api.post("/judgment", {"kind": "park", "block": "task:x"}).status_code == 200
    b = board(api)
    assert b["first_run"] is None and b["briefing"]["by"] != "door" and "door" not in b


def test_lived_in_install_door_a_names_what_needs_you_then_hands_back(api):
    """Door A on a lived-in home: every number is counted, the things that need you are real,
    piles are proposed from history without any model (the deterministic fallback), and once the
    user has judged something and answered the piles the ordinary briefing takes over."""
    import time
    b = board(api)
    br, by_id = b["briefing"], {x["id"]: x for x in b["blocks"]}
    assert br["by"] == "door"
    assert br["line"] == "i went through your hermes. 4 chats in two weeks, 4 scheduled jobs. two need you."
    need = {n["kind"]: by_id[n["id"]] for n in b["needs_you"]}
    assert need["question"]["title"] == "Fix the flaky invoice export test"     # ended on "Should I push it?"
    assert need["failed"]["title"] == "backup-notes"
    assert br["for_you"][:2] == [need["question"]["id"], need["failed"]["id"]] and br["broke"] == []
    for _ in range(40):                                  # untangle runs in the background, once
        piles = [x for x in board(api)["blocks"] if x["kind"] == "proposal"]
        if piles:
            break
        time.sleep(0.25)
    assert [p["title"] for p in piles] == ["these 3 things are all garden"]
    assert api.post("/judgment", {"kind": "park", "block": "task:x"}).status_code == 200
    pid = piles[0]["id"].split(":", 1)[1]
    assert api.post("/brain/proposal", {"id": pid, "writer": "untangle", "op": "dismiss"}).status_code == 200
    b = board(api)
    assert b["briefing"]["by"] != "door" and b["briefing"]["line"] == "three things today. and one thing broke."
    assert not [x for x in b["blocks"] if x["kind"] == "proposal"]            # a rejected pile stays rejected


def test_untangle_asks_the_host_model_and_never_a_url(api):
    from og_core import board as B, talk
    seen = []

    class Door:
        def complete(self, messages, **kw):
            seen.append(messages[0]["content"])
            return type("R", (), {"text": "{}"})()
    talk.set_llm(Door())
    app = B.app()
    assert app.board.untangle.chat is app.headlines.chat                 # both point at the host model
    assert app.board.untangle.chat("name these piles") == "{}" and seen == ["name these piles"]


def test_what_moved_while_you_were_away_is_marked_until_you_look(api, home):
    """Stream job 6 (change outlines): something Hermes changed since the user last looked is
    marked on the briefing; focusing it clears the mark. The record lives in plugin-data."""
    import time
    b = board(api)                                           # the baseline
    assert not (b["briefing"].get("moved") or {}).get("ids")
    assert api.post("/seen", {"board": True}).status_code == 200
    time.sleep(2.2)                                          # past the "he was just looking" slack
    path = os.path.join(home, "cron", "jobs.json")
    doc = json.load(open(path))
    job = next(j for j in doc["jobs"] if j["name"] == "morning-brief")
    job.update(last_status="error", last_error="could not reach the calendar", failure_streak=1)
    json.dump(doc, open(path, "w"))
    b = board(api)
    moved = b["briefing"].get("moved") or {}
    jid = "job:" + job["id"]
    assert jid in (moved.get("ids") or []), moved
    card = next(x for x in b["blocks"] if x["id"] == jid)
    assert card["changed"]["what"] == "this broke" and card["changed"]["by"] == "the routine"
    assert os.path.exists(os.path.join(home, "plugin-data", "1guy", "seen.json"))
    assert api.post("/seen", {"id": jid}).status_code == 200       # he focused it
    assert jid not in ((board(api)["briefing"].get("moved") or {}).get("ids") or [])
    assert api.post("/seen", {}).status_code == 400
