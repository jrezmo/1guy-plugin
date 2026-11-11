"""Email + calendar through the user's own Hermes (product.md feature 8), inside the plugin.

1guy never reads mail itself. It can offer to create ONE Hermes scheduled job that uses the
user's own skills, read-only, and it reads what that job writes. No test here runs a command:
the runner is always a stub.
"""
import json
import os
import time

import pytest


@pytest.fixture()
def mailcal(api):
    """The vendored module, with a recording stub in place of the subprocess runner."""
    from og_core import board, talk
    talk.routes()                                   # loads the route shim (which sets the real runner)
    m = board.app().board.mailcal
    calls = []
    m.runner = lambda argv: (calls.append(list(argv)) or (0, "Created job: abc123def456"))
    m.calls = calls
    return m


def tiles(api):
    b = api.get("/board?fresh=1").json()
    return {x["id"]: x for x in b["blocks"] if x["kind"] == "widget"}, b


def test_without_a_mail_skill_the_tiles_say_so_and_nothing_can_be_set_up(api, home, mailcal):
    st = api.get("/board/mailcal").json()
    assert st["can"] == {"email": False, "calendar": False, "via": []} and st["job"] is None
    t, _ = tiles(api)
    assert "can't read email yet" in t["widget:email"]["summary"]
    r = api.post("/board/mailcal", {"op": "setup"})
    assert r.status_code == 409 and mailcal.calls == []           # no command without the ability


def test_with_a_skill_he_offers_and_yes_creates_exactly_one_disclosed_job(api, home, mailcal):
    os.makedirs(os.path.join(home, "skills", "productivity", "gmail"))
    os.makedirs(os.path.join(home, "skills", "productivity", "google-calendar"))
    st = api.get("/board/mailcal").json()
    assert st["can"]["email"] and st["can"]["calendar"] and st["job"] is None and not st["declined"]
    r = api.post("/board/mailcal", {"op": "setup"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    create, run = mailcal.calls
    assert create[:3] == ["hermes", "cron", "create"] and create[-4:] == ["--name", "1guy-mail-cal", "--deliver", "local"]
    assert "READ ONLY" in create[4] and "never send" in create[4]            # the job is told to only read
    assert run == ["hermes", "cron", "run", "abc123def456"]
    ledger = [json.loads(ln) for ln in open(os.path.join(home, "plugin-data", "1guy", "judgments.jsonl"))]
    assert ledger[-1]["kind"] == "decide" and ledger[-1]["block"] == "widget:email"   # the yes is a judgment
    assert api.post("/board/mailcal", {"op": "explode"}).status_code == 400


def test_not_now_is_remembered(api, home, mailcal):
    os.makedirs(os.path.join(home, "skills", "productivity", "gmail"))
    assert api.post("/board/mailcal", {"op": "not-now"}).json()["ok"]
    assert api.get("/board/mailcal").json()["declined"] is True and mailcal.calls == []
    assert os.path.exists(os.path.join(home, "plugin-data", "1guy", "mailcal.json"))


def test_tiles_and_time_lens_read_what_the_job_wrote(api, home, mailcal):
    """The job's last output file is the only source: real rows on the tiles, events as blocks."""
    os.makedirs(os.path.join(home, "skills", "productivity", "gmail"))
    jobs_path = os.path.join(home, "cron", "jobs.json")
    doc = json.load(open(jobs_path))
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + time.strftime("%z")
    doc["jobs"].append({"id": "abc123def456", "name": "1guy-mail-cal", "enabled": True, "state": "scheduled",
                        "schedule_display": "hourly", "last_status": "ok", "last_run_at": iso(now - 600)})
    json.dump(doc, open(jobs_path, "w"))
    out = os.path.join(home, "cron", "output", "abc123def456")
    os.makedirs(out)
    digest = {"at": iso(now - 600),
              "email": [{"id": "m1", "from": "Sam Rivera", "subject": "Can you send the draft by Friday?",
                         "received": iso(now - 3600), "needs_reply": True, "why": "wants the newsletter draft"}],
              "calendar": [{"id": "e1", "title": "Dentist", "start": iso(now + 5 * 3600), "end": iso(now + 6 * 3600),
                            "all_day": False, "where": "", "who": []}],
              "errors": []}
    with open(os.path.join(out, "2031-01-01_00-00-00.md"), "w") as f:
        f.write("# Cron Job: 1guy-mail-cal\n\n## Response\n\n" + json.dumps(digest) + "\n")
    t, b = tiles(api)
    assert "Sam Rivera" in json.dumps(t["widget:email"]["detail"]) and "can't read" not in t["widget:email"]["summary"]
    assert "Dentist" in json.dumps(t["widget:calendar"]["detail"])
    assert any(x["kind"] == "event" and x["title"] == "Dentist" for x in b["blocks"])
    assert api.get("/board/mailcal").json()["job"]["id"] == "abc123def456"


def _write_digest(home, email):
    """A `1guy-mail-cal` job and its last output, written straight into a scratch home."""
    os.makedirs(os.path.join(home, "skills", "productivity", "gmail"), exist_ok=True)
    jobs_path = os.path.join(home, "cron", "jobs.json")
    doc = json.load(open(jobs_path))
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + time.strftime("%z")
    doc["jobs"].append({"id": "abc123def456", "name": "1guy-mail-cal", "enabled": True, "state": "scheduled",
                        "schedule_display": "hourly", "last_status": "ok", "last_run_at": iso(now - 600)})
    json.dump(doc, open(jobs_path, "w"))
    out = os.path.join(home, "cron", "output", "abc123def456")
    os.makedirs(out, exist_ok=True)
    rows = [dict(r, received=iso(now - r.pop("ago"))) for r in email]
    with open(os.path.join(out, "2031-01-01_00-00-00.md"), "w") as f:
        f.write("# Cron Job: 1guy-mail-cal\n\n## Response\n\n" + json.dumps({"at": iso(now - 600), "email": rows,
                                                                                "calendar": [], "errors": []}) + "\n")


def test_the_inbox_is_people_waiting_oldest_first_and_a_judgment_hides_one(api, home, mailcal):
    """#23: the count and his opener are people who wrote to the user, not the unread total; a
    verb on one email is a judgment (ledger + a file in the plugin's folder), never a mailbox write."""
    _write_digest(home, [
        {"id": "p1", "from": "Sam Rivera <sam@example.test>", "subject": "Can you send the draft by Friday?",
         "ago": 3 * 86400, "needs_reply": True, "why": "wants the newsletter draft"},
        {"id": "p2", "from": "Dana Lee <dana@example.test>", "subject": "Lunch next week?", "ago": 3600,
         "needs_reply": True},
        {"id": "n1", "from": "Shop <deals@shop.example.test>", "subject": "50% off", "ago": 600, "needs_reply": False},
    ])
    t, b = tiles(api)
    d = t["widget:email"]["detail"]
    assert d["people"] == 2 and d["automated"] == 1 and d["count"] == 2      # people, not 3 unread
    assert [i["id"] for i in d["items"]][:2] == ["p1", "p2"]                 # the one who waited longest first
    assert t["widget:email"]["headline"].get("own") is True                  # his opener for the tile is the count of people
    r = api.post("/board/mailcal", {"op": "not-mine", "id": "p2", "from": "Dana Lee", "subject": "Lunch next week?"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    t, _ = tiles(api)
    assert [i["id"] for i in t["widget:email"]["detail"]["items"]][0] == "p1"
    assert "p2" not in json.dumps(t["widget:email"]["detail"]["items"])      # hidden for good
    ledger = [json.loads(ln) for ln in open(os.path.join(home, "plugin-data", "1guy", "judgments.jsonl"))]
    assert ledger[-1]["kind"] == "not-mine" and ledger[-1]["block"] == "mail:p2"
    assert os.path.exists(os.path.join(home, "plugin-data", "1guy", "mail_hidden.json"))   # the one new file, in the plugin's folder
    assert mailcal.calls == []                                               # no command ran: the mailbox is never touched


def test_the_route_only_ever_runs_the_two_disclosed_commands(api):
    from og_core import talk
    talk.routes()
    import sys
    run = sys.modules["oneguy_routes_mailcal"].run
    assert run(["rm", "-rf", "x"]) == (1, "not a command this route runs")
    assert run(["hermes", "config", "set", "x", "y"])[0] == 1
    assert sys.modules["oneguy_routes_mailcal"].ALLOWED == (("hermes", "cron", "create"), ("hermes", "cron", "run"))


def test_claim_names_the_cards_a_line_is_about_without_any_model(api):
    """Stream job 5 (claim frame): POST /talk/claim -> the -ing word and the cards, instantly."""
    b = api.get("/board").json()
    task = next(x for x in b["blocks"] if x["kind"] == "task" and "dentist" in x["title"])
    r = api.post("/talk/claim", {"text": "schedule this for friday", "focus": task["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verb"] == "scheduling" and out["ids"] == [task["id"]] and out["label"].startswith("scheduling")
    out = api.post("/talk/claim", {"text": "group the garden things"}).json()
    assert out["verb"] == "grouping" and len(out["ids"]) >= 2
