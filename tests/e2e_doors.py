#!/usr/bin/python3
"""Door B and the sample desk, in a real browser, inside the plugin's sandboxed frame.

    env XDG_CACHE_HOME=~/.cache /usr/bin/python3 tests/e2e_doors.py <port> <out-dir>

A brand-new (empty) Hermes home. He asks "what are you in the middle of?"; three typed lines
become cards beside the box and to-dos in tasklist.md; "look around a sample desk" loads the
bundled synthetic world (marked, read-only) and leaving it returns to the real, unchanged desk.
Prints one JSON line; exits non-zero on any failed check. Kills its server by PID.
"""
import hashlib
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
LINES = ["finish the deck for thursday", "book the flights", "call the plumber"]


def digest(path):
    return hashlib.sha1(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def main(port, out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="1guy-e2e-")
    home = os.path.join(tmp, ".hermes")
    os.makedirs(home)
    py = os.environ.get("ONEGUY_TEST_PYTHON") or os.path.join(ROOT, ".venv", "bin", "python")
    env = {"HOME": tmp, "HERMES_HOME": home, "PATH": "/usr/bin:/bin"}
    for k in ("ONEGUY_UPSTREAM", "ONEGUY_UPSTREAM_TOKEN"):     # stranger test: front a real dashboard,
        if os.environ.get(k):                                  # whose (empty) home is ONEGUY_E2E_HOME
            env[k] = os.environ[k]
    home = os.environ.get("ONEGUY_E2E_HOME") or home
    srv = subprocess.Popen([py, os.path.join(HERE, "harness", "serve.py"), str(port)], env=env)
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
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.on("pageerror", lambda e: problems.append(str(e)[:200]))
            pg.on("console", lambda m: problems.append(m.text[:200]) if m.type == "error" else None)

            def wait(expr, timeout=25.0):
                """The canvas frame once `expr` is true in it. The frame's document is replaced when
                the loader writes the canvas and again on sample on/off, so every look is retried."""
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
            ask = "what are you in the middle of?"
            f = wait("document.querySelector('.og-doorb-in') && document.querySelector('#og-brief').innerText.includes(%s)" % json.dumps(ask))
            checks["he_asks"] = ask
            box = f.query_selector(".og-doorb-in")
            for line in LINES:
                box.type(line)
                box.press("Enter")
                pg.wait_for_timeout(700)
            f = wait("document.querySelectorAll('.og-doorb-card .og-doorb-t').length >= %d" % len(LINES))
            checks["cards_beside_the_box"] = f.evaluate(
                "[...document.querySelectorAll('.og-doorb-card .og-doorb-t')].map(e => e.textContent)")
            for _ in range(40):                              # each line is saved as it lands
                text = open(os.path.join(home, "tasklist.md")).read() if os.path.exists(os.path.join(home, "tasklist.md")) else ""
                if all(f"- [ ] {ln}" in text for ln in LINES):
                    break
                pg.wait_for_timeout(150)
            checks["todos_written"] = [ln for ln in LINES if f"- [ ] {ln}" in text]
            pg.wait_for_timeout(1200)
            pg.screenshot(path=os.path.join(out, "door-b.png"))

            before = digest(os.path.join(home, "tasklist.md"))
            prefs = os.path.join(home, "plugin-data", "1guy", "local.json")
            prefs_before = digest(prefs)
            f.click(".og-doorb-sample")
            f = wait("window.ONEGUY_SAMPLE === true && document.querySelector('.og-sample-chip') && "
                     "document.querySelectorAll('#og-brief [data-id], #og-cards > *').length > 5")
            checks["sample"] = f.evaluate("""(() => ({ chip: document.querySelector('.og-sample-chip').innerText.replace(/\\s+/g, ' ').trim(),
                marked: document.body.classList.contains('og-sample'),
                cards: document.querySelectorAll('#og-brief [data-id], #og-cards > *').length > 5 }))()""")
            pg.wait_for_timeout(1500)
            pg.screenshot(path=os.path.join(out, "sample-desk.png"))
            checks["sample_saves_nothing"] = digest(prefs) == prefs_before
            f.click(".og-sample-chip")
            f = wait("window.ONEGUY_SAMPLE !== true && !document.querySelector('.og-sample-chip') && "
                     "document.querySelector('#og-brief')")
            checks["left_sample"] = f.evaluate("!document.body.classList.contains('og-sample')")
            checks["real_desk_untouched_by_sample"] = digest(os.path.join(home, "tasklist.md")) == before
            b.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        srv.kill()
        srv.wait(timeout=10)
    want = {"he_asks": "what are you in the middle of?", "cards_beside_the_box": LINES, "todos_written": LINES,
            "sample": {"chip": "sample desk · leave", "marked": True, "cards": True},
            "left_sample": True, "real_desk_untouched_by_sample": True, "sample_saves_nothing": True}
    failed = {k: checks.get(k) for k, v in want.items() if checks.get(k) != v}
    if problems:
        failed["page_errors"] = problems[:5]
    print(json.dumps({"ok": not failed, "failed": failed, "checks": checks}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 9191,
                  sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="1guy-shots-")))
