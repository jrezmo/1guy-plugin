#!/usr/bin/python3
"""Screenshots of the first run on a day-one Hermes (3 chats, 2 jobs, no tasklist).

    env XDG_CACHE_HOME=~/.cache /usr/bin/python3 tests/first_run_shots.py <port> <out-dir>

Starts its own scratch server on 127.0.0.1:<port> and kills it by PID at the end.
"""
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
sys.path.insert(0, HERE)
import make_home  # noqa: E402


def main(port, out, want=int(os.environ.get("FIRST_RUN_CARDS", "2"))):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="1guy-first-")
    home = make_home.build(os.path.join(tmp, ".hermes"), sparse=True)
    py = os.environ.get("ONEGUY_TEST_PYTHON") or os.path.join(ROOT, ".venv", "bin", "python")
    env = {"HOME": tmp, "HERMES_HOME": home, "PATH": "/usr/bin:/bin"}
    for k in ("ONEGUY_UPSTREAM", "ONEGUY_UPSTREAM_TOKEN"):     # stranger test: front a real dashboard
        if os.environ.get(k):
            env[k] = os.environ[k]
    srv = subprocess.Popen([py, os.path.join(HERE, "harness", "serve.py"), str(port)], env=env)
    base = f"http://127.0.0.1:{port}"
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
            problems = []
            pg.on("pageerror", lambda e: problems.append(str(e)[:200]))
            pg.on("console", lambda m: problems.append(m.text[:200]) if m.type == "error" else None)
            pg.goto(base + "/harness/index.html")
            pg.wait_for_function("document.querySelector('iframe')", timeout=15000)
            f = next(x for x in pg.frames if x != pg.main_frame)
            for _ in range(150):      # he places the cards one at a time; wait for them
                try:
                    ready = f.evaluate("!!(window.OneGuy && document.querySelector('#og-brief'))") and f.evaluate("document.querySelectorAll('#og-brief .og-fy-card, #og-brief .og-fy [data-id]').length >= " + str(want))
                except Exception:
                    ready = False          # the loader swaps the frame's document once
                if ready:
                    break
                time.sleep(0.2)
            pg.wait_for_timeout(2500)
            pg.screenshot(path=os.path.join(out, "first-run.png"))
            line = f.evaluate("document.querySelector('#og-brief').innerText.split('\\n')[0]")
            for lens in ("inbox", "time", "board"):
                f.click(f'#og-lens [data-lens="{lens}"]')
                pg.wait_for_timeout(1600)
                pg.screenshot(path=os.path.join(out, f"first-run-{lens}.png"))
            b.close()
        print(line)
        if problems:
            print("PROBLEMS:", problems[:5])
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)       # the scratch home
        srv.kill()
        srv.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 9191,
                  sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots"))
