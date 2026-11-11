#!/usr/bin/python3
"""The inbox as next actions (#23), in a real browser INSIDE the plugin's sandboxed frame.

    env XDG_CACHE_HOME=~/.cache /usr/bin/python3 tests/e2e_inbox.py <port> <out-dir>

A synthetic Hermes home with a `1guy-mail-cal` job whose last output holds three emails (two from
people, one bulk). The email tile opens in the frame: people first, oldest first; a row opens in
place with three verbs; "not mine" hides it for good and writes one ledger line and one file in the
plugin's folder; nothing runs a command. Prints one JSON line; exits non-zero on any failed check.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main(port, out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="1guy-e2e-")
    home = os.path.join(tmp, ".hermes")
    subprocess.run([sys.executable, os.path.join(HERE, "make_home.py"), home], check=True, stdout=subprocess.DEVNULL)
    os.makedirs(os.path.join(home, "skills", "productivity", "gmail"))
    now = time.time()
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) + time.strftime("%z")
    jp = os.path.join(home, "cron", "jobs.json")
    doc = json.load(open(jp))
    doc["jobs"].append({"id": "abc123def456", "name": "1guy-mail-cal", "enabled": True, "state": "scheduled",
                        "schedule_display": "hourly", "last_status": "ok", "last_run_at": iso(now - 600)})
    json.dump(doc, open(jp, "w"))
    od = os.path.join(home, "cron", "output", "abc123def456")
    os.makedirs(od)
    mails = [{"id": "p1", "from": "Sam Rivera <sam@example.test>", "subject": "Can you send the draft by Friday?",
              "received": iso(now - 3 * 86400), "needs_reply": True, "why": "wants the newsletter draft"},
             {"id": "p2", "from": "Dana Lee <dana@example.test>", "subject": "Lunch next week?",
              "received": iso(now - 3600), "needs_reply": True},
             {"id": "n1", "from": "Shop <deals@shop.example.test>", "subject": "50% off everything",
              "received": iso(now - 600), "needs_reply": False}]
    with open(os.path.join(od, "2031-01-01_00-00-00.md"), "w") as f:
        f.write("# Cron Job: 1guy-mail-cal\n\n## Response\n\n" + json.dumps(
            {"at": iso(now - 600), "email": mails, "calendar": [], "errors": []}) + "\n")
    py = os.environ.get("ONEGUY_TEST_PYTHON") or os.path.join(ROOT, ".venv", "bin", "python")
    srv = subprocess.Popen([py, os.path.join(HERE, "harness", "serve.py"), str(port)],
                           env={"HOME": tmp, "HERMES_HOME": home, "PATH": "/usr/bin:/bin"})
    base = f"http://127.0.0.1:{port}"
    checks, problems = {}, []
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/api/plugins/1guy/routes", timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page(viewport={"width": 1440, "height": 900})
            pg.on("pageerror", lambda e: problems.append(str(e)[:200]))
            pg.on("console", lambda m: problems.append(m.text[:200]) if m.type == "error" else None)

            def wait(expr, timeout=25.0):
                end = time.time() + timeout
                while time.time() < end:
                    for f in pg.frames:
                        if f == pg.main_frame:
                            continue
                        try:
                            if f.evaluate("!!window.OneGuy && !!(" + expr + ")"):
                                return f
                        except Exception:
                            pass
                    pg.wait_for_timeout(150)
                raise AssertionError("never true in the frame: " + expr)

            pg.goto(base + "/harness/index.html")
            f = wait("document.querySelector('.og-brief-line') && document.querySelector('[data-id=\"widget:email\"]')")
            checks["tile"] = f.evaluate("document.querySelector('[data-id=\"widget:email\"]').innerText.replace(/\\s+/g, ' ').trim().slice(0, 120)")
            f.locator(".og-glance[data-id='widget:email']").first.scroll_into_view_if_needed()
            pg.wait_for_timeout(1500)                       # the briefing is still settling (the glances slide in)
            checks["tile_box"] = f.evaluate("(() => { const r = document.querySelector('.og-glance[data-id=\"widget:email\"]').getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })()")
            f.locator(".og-glance[data-id='widget:email']").first.click(timeout=8000)
            f = wait("document.querySelector('[data-mail-list]') && document.querySelectorAll('[data-mail]').length >= 3")
            checks["order"] = f.evaluate("[...document.querySelectorAll('[data-mail]')].map(n => n.dataset.mail)")
            checks["separator"] = f.evaluate("(document.querySelector('.kg-mail-sep') || {}).textContent || null")
            pg.screenshot(path=os.path.join(out, "inbox-list.png"))
            f.locator("[data-mail='p1'] .kg-mail-h").click()
            f = wait("!document.querySelector('[data-mail=\"p1\"] .kg-mail-b').hidden")
            checks["verbs"] = f.evaluate("[...document.querySelectorAll('[data-mail=\"p1\"] [data-mail-verb]')].map(b => b.textContent.trim())")
            checks["waiting"] = f.evaluate("(document.querySelector('[data-mail=\"p1\"] .kg-mail-wait') || {}).textContent || null")
            pg.screenshot(path=os.path.join(out, "inbox-open.png"))
            f.locator("[data-mail='p1'] [data-mail-verb='not-mine']").click()
            f = wait("!document.querySelector('[data-mail=\"p1\"]')")
            checks["after_not_mine"] = f.evaluate("[...document.querySelectorAll('[data-mail]')].map(n => n.dataset.mail)")
            pg.wait_for_timeout(800)
            br.close()
        led = os.path.join(home, "plugin-data", "1guy", "judgments.jsonl")
        rows = [json.loads(x) for x in open(led)] if os.path.exists(led) else []
        checks["ledger"] = [(r["kind"], r["block"]) for r in rows if r["block"].startswith("mail:")]
        checks["hidden_file"] = os.path.exists(os.path.join(home, "plugin-data", "1guy", "mail_hidden.json"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        srv.kill()
        srv.wait(timeout=10)
    want = {"order": lambda v: v == ["p1", "p2", "n1"],
            "separator": lambda v: bool(v) and "not from a person" in v,
            "verbs": lambda v: v == ["talk about this", "later", "not mine"],
            "waiting": lambda v: bool(v) and v.startswith("waiting"),
            "after_not_mine": lambda v: v == ["p2", "n1"],
            "ledger": lambda v: v == [("not-mine", "mail:p1")],
            "hidden_file": lambda v: v is True}
    failed = {k: checks.get(k) for k, ok in want.items() if not ok(checks.get(k))}
    print(json.dumps({"checks": checks, "problems": problems, "failed": failed}))
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
