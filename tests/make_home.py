#!/usr/bin/env python3
"""Build a synthetic Hermes home: what a stranger's install looks like after a week of use.

    python3 tests/make_home.py <dir>            # writes <dir>/state.db, cron/jobs.json, tasklist.md
    python3 tests/make_home.py <dir> --sparse   # a day-one install: 3 chats, 2 jobs, no tasklist
    python3 tests/make_home.py <dir> --heavy    # a heavy user: about 200 cards

Everything here is invented. Nothing is copied from a real machine.
"""
import json
import os
import sqlite3
import sys
import time

H = 3600.0

SESSIONS = [  # (title, source, hours ago, cwd, turns)
    ("Plan the garden beds for spring", "desktop", 2, "~/notes", [
        ("user", "I want four raised beds this year. Help me plan what goes where."),
        ("assistant", "Four beds works well. Bed one for tomatoes and basil, bed two for greens."),
        ("user", "The north side only gets morning sun."),
        ("assistant", "Then put the greens on the north side. They like the cooler light."),
        ("user", "Good. What should I order this week?"),
        ("assistant", "Order seed potatoes and onion sets now. Everything else can wait until March.")]),
    ("Fix the flaky invoice export test", "cli", 5, "~/code/ledger", [
        ("user", "The invoice export test fails about one run in five."),
        ("assistant", "It compares timestamps with second precision. Two rows can tie."),
        ("user", "Can you make the ordering stable?"),
        ("assistant", "Done. I sort by created time and then id. Twenty runs passed in a row."),
        ("user", "Open a PR."),
        ("assistant", "I need your go-ahead to push the branch. Should I push it?")]),
    ("Draft the neighbourhood newsletter", "desktop", 26, "~/notes", [
        ("user", "Draft the March newsletter. Street cleaning moves to Thursdays."),
        ("assistant", "Here is a first draft with the cleaning change at the top."),
        ("user", "Shorter. And add the plant swap on the 14th."),
        ("assistant", "Cut to 180 words. The plant swap is the second item now.")]),
    ("Compare three standing desks", "telegram", 50, None, [
        ("user", "Compare these three standing desks for a small room."),
        ("assistant", "The narrow one fits your wall. The other two need 20 cm more."),
        ("user", "Which is quietest?"),
        ("assistant", "The narrow one is also the quietest, by the reviews I found.")]),
]

JOBS = [
    {"id": "a1b2c3d4e5f6", "name": "morning-brief", "schedule_display": "every day at 07:00",
     "last_status": "ok", "hours_ago": 6, "enabled": True, "state": "scheduled",
     "output": "## Response\n\nThree things today: the dentist at 3, the newsletter draft is waiting on you, rain after 5."},
    {"id": "b2c3d4e5f6a1", "name": "backup-notes", "schedule_display": "every 6h",
     "last_status": "error", "hours_ago": 1, "enabled": True, "state": "scheduled", "failure_streak": 3,
     "last_error": "destination disk is full",
     "output": "## Response\n\nBackup failed: the destination disk is full (0 bytes free)."},
    {"id": "c3d4e5f6a1b2", "name": "weekly-review", "schedule_display": "Sundays at 18:00",
     "last_status": "ok", "hours_ago": 70, "enabled": True, "state": "scheduled",
     "output": "## Response\n\nA quiet week. Two tasks closed, one new idea filed."},
    {"id": "d4e5f6a1b2c3", "name": "price-watch", "schedule_display": "every day at 12:00",
     "last_status": None, "hours_ago": None, "enabled": False, "state": "paused", "output": None},
]

TASKLIST = """# Tasklist

## To-do

- [ ] Order seed potatoes and onion sets #garden @{due}
- [ ] Reply to the landlord about the boiler !p1
- [ ] Book the dentist follow-up #health
- [ ] Send the March newsletter draft to Sam #newsletter
- [ ] Measure the wall for the standing desk #desk
- [x] Renew the library card

## Open questions / issues

- [ ] Is the backup disk worth replacing or should notes go to the cloud? #backup
- [ ] Which bed gets the tomatoes if the north side stays shady? #garden
"""


# A judge's machine on day one: three chats (one barely started, one from last month),
# two scheduled jobs, and no tasklist file at all.
SPARSE_SESSIONS = [
    ("Summarise the quarterly report", "desktop", 3, None, [
        ("user", "Summarise this quarterly report in five bullet points."),
        ("assistant", "Revenue is up 8 percent, costs are flat, and hiring is paused until June."),
        ("user", "What should I ask about in the meeting?"),
        ("assistant", "Ask why churn rose in the smallest plan and what the June hiring decision depends on.")]),
    ("What is a good name for a sourdough starter", "desktop", 20, None, [
        ("user", "What is a good name for a sourdough starter?"),
        ("assistant", "Clint Yeastwood is the classic. Bread Pitt is a close second.")]),
    ("Set up the weather script", "cli", 24 * 12, "~/code/weather", [
        ("user", "Write a script that prints tomorrow's weather."),
        ("assistant", "Done. It reads the forecast file and prints one line."),
        ("user", "Make it say whether I need an umbrella."),
        ("assistant", "Added. It prints 'umbrella' when rain is above 40 percent.")]),
]
SPARSE_JOBS = [JOBS[0], dict(JOBS[2], hours_ago=30)]


def big(n_sessions=70, n_jobs=25, n_tasks=110):
    """A heavy user's install: about 200 cards. Returns (sessions, jobs, tasklist text)."""
    topics = ["garden", "ledger", "newsletter", "desk", "kitchen", "taxes", "trip", "bike", "piano", "website"]
    verbs = ["Plan", "Fix", "Draft", "Compare", "Review", "Tidy", "Research", "Rewrite", "Check", "Sort out"]
    sessions = []
    for i in range(n_sessions):
        t = f"{verbs[i % 10]} the {topics[(i * 3) % 10]} notes, part {i + 1}"
        turns = [("user" if k % 2 == 0 else "assistant", f"{t}: message {k + 1} about what to do next.") for k in range(6)]
        sessions.append((t, ["desktop", "cli", "telegram"][i % 3], 1 + i * 2.2, None, turns))
    jobs = []
    for i in range(n_jobs):
        jobs.append({"id": f"{0xa1b2c3d4e5f6 + i:012x}", "name": f"{topics[i % 10]}-check-{i + 1}",
                     "schedule_display": f"every day at {6 + i % 12:02d}:00", "last_status": "error" if i % 9 == 4 else "ok",
                     "hours_ago": 1 + i, "enabled": i % 7 != 6, "state": "scheduled" if i % 7 != 6 else "paused",
                     "failure_streak": 2 if i % 9 == 4 else 0, "last_error": "timed out" if i % 9 == 4 else None,
                     "output": f"## Response\n\nRun {i + 1} finished. Nothing unusual in the {topics[i % 10]} check."})
    lines = ["# Tasklist", "", "## To-do", ""]
    for i in range(n_tasks):
        extra = f" #{topics[i % 10]}" + (" !p1" if i % 17 == 0 else "") + (" @2030-01-0%d" % (1 + i % 9) if i % 11 == 0 else "")
        lines.append(f"- [{'x' if i % 13 == 0 else ' '}] {verbs[(i * 7) % 10]} the {topics[i % 10]} thing number {i + 1}{extra}")
    lines += ["", "## Open questions / issues", ""] + [f"- [ ] Should the {topics[i]} plan change this month? #{topics[i]}" for i in range(10)]
    return sessions, jobs, "\n".join(lines) + "\n"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + time.strftime("%z")


def build(home, sparse=False, heavy=False):
    now = time.time()
    big_sessions, big_jobs, big_tasks = big() if heavy else (None, None, None)
    os.makedirs(os.path.join(home, "cron", "output"), exist_ok=True)
    db = os.path.join(home, "state.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, model TEXT,
            parent_session_id TEXT, started_at REAL NOT NULL, ended_at REAL,
            message_count INTEGER DEFAULT 0, title TEXT, cwd TEXT, git_repo_root TEXT,
            archived INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
            last_activity_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT, timestamp REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1);
    """)
    for i, (title, source, hours, cwd, turns) in enumerate(big_sessions or (SPARSE_SESSIONS if sparse else SESSIONS)):
        end = now - hours * H
        start = end - 900
        sid = time.strftime("%Y%m%d_%H%M%S", time.localtime(start)) + f"_{i:02d}cafe"
        con.execute("INSERT INTO sessions (id, source, model, started_at, message_count, title, cwd, "
                    "last_activity_at) VALUES (?,?,?,?,?,?,?,?)",
                    (sid, source, "example-model", start, len(turns), title, cwd, end))
        for k, (role, content) in enumerate(turns):
            con.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                        (sid, role, content, start + k * (900 / len(turns))))
    con.commit()
    con.close()

    jobs = []
    for j in big_jobs or (SPARSE_JOBS if sparse else JOBS):
        last = now - j["hours_ago"] * H if j["hours_ago"] is not None else None
        jobs.append({"id": j["id"], "name": j["name"], "prompt": "synthetic", "enabled": j["enabled"],
                     "state": j["state"], "schedule_display": j["schedule_display"],
                     "schedule": {"kind": "cron", "display": j["schedule_display"]},
                     "created_at": iso(now - 30 * 24 * H), "last_run_at": iso(last) if last else None,
                     "next_run_at": iso(now + 3 * H) if j["enabled"] else None,
                     "last_status": j["last_status"], "last_error": j.get("last_error"),
                     "failure_streak": j.get("failure_streak", 0), "deliver": "local"})
        if j["output"] and last:
            d = os.path.join(home, "cron", "output", j["id"])
            os.makedirs(d, exist_ok=True)
            name = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(last)) + ".md"
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(f"# Cron Job: {j['name']}\n\n{j['output']}\n")
    with open(os.path.join(home, "cron", "jobs.json"), "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs}, f, indent=2)
    if not sparse:
        with open(os.path.join(home, "tasklist.md"), "w", encoding="utf-8") as f:
            f.write(big_tasks if heavy else TASKLIST.format(due=time.strftime("%Y-%m-%d", time.localtime(now + 24 * H))))
    return home


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a not in ("--sparse", "--heavy")]
    if len(args) != 1:
        sys.exit(__doc__)
    print(build(os.path.abspath(args[0]), sparse="--sparse" in sys.argv, heavy="--heavy" in sys.argv))
