#!/usr/bin/python3
"""End to end in a real browser: the REAL desktop/plugin.js (stub SDK + fake gateway) against
the real backend on a synthetic Hermes home.

    env XDG_CACHE_HOME=~/.cache /usr/bin/python3 tests/e2e_canvas.py <port> <out-dir>

Starts its own scratch server on 127.0.0.1:<port>, kills it by PID at the end, prints one
JSON line of findings and exits non-zero on any failed check. Screenshots land in <out-dir>.
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
sys.path.insert(0, HERE)
import make_home  # noqa: E402


def until(frame, expr, timeout=15.0):
    """Poll a JS expression in the canvas frame. (wait_for_function needs eval, and the
    canvas's Content-Security-Policy rightly forbids eval.)"""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if frame.evaluate(expr):
                return
        except Exception as e:      # the loader replaces the frame's document once, mid-load:
            if "context was destroyed" not in str(e) and "navigation" not in str(e):
                raise               # an evaluate racing that moment is retried, anything else is real
        time.sleep(0.15)
    raise AssertionError("never true: " + expr)


def canvas_frame(page):
    """The canvas frame, once the canvas document (not the loader) is running in it."""
    page.wait_for_function("document.querySelector('iframe')", timeout=15000)
    frame = next(x for x in page.frames if x != page.main_frame)
    until(frame, "!!(window.OneGuy && document.querySelector('#og-brief'))", 20)
    return frame


def main(port, out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="1guy-e2e-")
    home = make_home.build(os.path.join(tmp, ".hermes"))
    py = os.environ.get("ONEGUY_TEST_PYTHON") or os.path.join(ROOT, ".venv", "bin", "python")
    env = {"HOME": tmp, "HERMES_HOME": home, "PATH": "/usr/bin:/bin"}
    for k in ("ONEGUY_UPSTREAM", "ONEGUY_UPSTREAM_TOKEN"):     # stranger test: front a real dashboard
        if os.environ.get(k):
            env[k] = os.environ[k]
    srv = subprocess.Popen([py, os.path.join(HERE, "harness", "serve.py"), str(port)], env=env)
    base = f"http://127.0.0.1:{port}"
    checks, errors, clean_errors = {}, [], []
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
            pg.on("pageerror", lambda e: errors.append(str(e)[:300]))
            # Every console error fails the run, and a Content-Security-Policy refusal is counted
            # on its own: the canvas must make zero requests its policy forbids. (The policy is
            # the page's own <meta>; native Hermes Desktop adds none to a SandboxedFrame.)
            violations = []

            def on_console(m):
                if "Content Security Policy" in m.text or "Content-Security-Policy" in m.text:
                    violations.append(m.text[:300])
                elif m.type == "error" and "status of 400" not in m.text:   # the deliberate bad request
                    errors.append(m.text[:300])
            pg.on("console", on_console)
            pg.goto(base + "/harness/index.html")
            f = canvas_frame(pg)
            f.wait_for_selector("#og-brief .og-fy-card, #og-brief [data-id]", timeout=15000)
            until(f, "document.querySelector('#og-brief').innerText.includes('i went through your hermes')")
            pg.wait_for_timeout(1500)
            pg.screenshot(path=os.path.join(out, "canvas-briefing.png"))
            checks["frame_is_data_url"] = f.url.startswith("data:text/html")
            checks["frame_url_bytes"] = len(f.url)
            checks["frame_origin_is_opaque"] = f.evaluate("location.origin") == "null"
            checks["sandbox"] = pg.evaluate("document.querySelector('iframe').getAttribute('sandbox')")
            checks["briefing_line"] = f.evaluate("document.querySelector('#og-brief').innerText.split('\\n')[0]")
            # sound cues: off until asked for; saying yes plays synthesized cues (no audio files)
            f.evaluate("""(() => { const o = OscillatorNode.prototype.start; window.__osc = 0;
                OscillatorNode.prototype.start = function () { window.__osc++; return o.apply(this, arguments); }; })()""")
            f.evaluate("window.OneGuy.sounds.play('land')")
            silent = f.evaluate("window.__osc")
            f.click("#og-snd .og-snd-yes")
            pg.wait_for_timeout(300)
            f.evaluate("window.OneGuy.sounds.play('land')")
            checks["sounds"] = {"silent_when_off": silent == 0, "on": f.evaluate("window.OneGuy.sounds.on"),
                                "voices_started": f.evaluate("window.__osc") > 0}

            # places art: every room's picture is really decoded, from inside the page
            until(f, "document.querySelectorAll('img.og-place-art').length > 0")
            pg.wait_for_timeout(600)
            checks["places_art"] = f.evaluate("""(() => { const im = [...document.querySelectorAll('img.og-place-art')];
                return { some: im.length > 0, all_decoded: im.every(i => i.complete && i.naturalWidth > 0),
                         all_inline: im.every(i => i.src.startsWith('data:image/webp;base64,')) }; })()""")
            pg.screenshot(path=os.path.join(out, "canvas-places.png"))

            # walk every lens so each module that carries styles has loaded and painted
            for lens in ("board", "time", "inbox", "today"):
                f.click(f'#og-lens [data-lens="{lens}"]')
                pg.wait_for_timeout(900)
            checks["styles"] = f.evaluate("""(() => {
                const has = (sel) => [...document.styleSheets].some(s => [...s.cssRules].some(r => (r.selectorText || '').includes(sel)));
                return { home: has('#og-brief'), kinds: has('.kb-'), surface: has('.tk-input'), stage: has('.og-stage') || has('#og-stage'),
                         time: has('.og-world') || has('[data-og-time]') || has('.og-drop') || has('og-time'),
                         links: document.querySelectorAll('link[rel=stylesheet]').length };
            })()""")
            checks["cards"] = f.evaluate("document.querySelectorAll('#og-cards > *').length")
            checks["contribs"] = pg.evaluate("window.__contribs.map(c => c.area + ':' + c.id)")
            # the to-do pane owns a zone (dock, no placement) so it cannot land as a hidden tab
            checks["todo_pane_zone"] = pg.evaluate("window.__contribs.find(c => c.id === 'tasks-pane').data")

            # one item = one thread: open the first card, say something, watch it stream back
            f.click("#og-brief .og-fy-card, #og-brief [data-id]")
            f.wait_for_selector(".tk-input", timeout=8000)
            f.fill(".tk-input", "what should I do first?")
            f.press(".tk-input", "Enter")
            until(f, "document.body.innerText.includes('Here is the plan for that item.')", 20)
            pg.wait_for_timeout(800)
            pg.screenshot(path=os.path.join(out, "canvas-thread.png"))
            calls = pg.evaluate("window.__calls")
            reqs = [c["args"] for c in calls if c["name"] == "request"]
            checks["gateway_calls"] = [r[0] for r in reqs]
            created = next(r[1] for r in reqs if r[0] == "session.create")
            sent = next(r[1] for r in reqs if r[0] == "prompt.submit")
            checks["session_titled"] = created["title"]
            checks["submit_uses_runtime_id"] = sent["session_id"] == "rt_1"
            checks["message_carries_item"] = "what should I do first?" in sent["text"]
            threads = json.load(urllib.request.urlopen(base + "/api/plugins/1guy/threads"))["threads"]
            checks["thread_stored_id"] = [t["primary"]["session_id"] for t in threads.values()]

            checks["csp_violations"] = list(violations)      # before the deliberate probes below
            clean_errors = list(errors)
            pg.wait_for_timeout(1800)                        # let the batched report land
            diag0 = json.load(urllib.request.urlopen(base + "/api/plugins/1guy/diag"))
            checks["diag_clean_run"] = {"problems": diag0["problem_count"], "timings": sorted(diag0["last_load"])}
            checks["csp_blocks_network"] = f.evaluate(
                "fetch('https://example.com/').then(() => 'open', () => 'blocked')")

            # a backend refusal reaches the canvas with its real status and reason
            checks["error_passthrough"] = f.evaluate(
                "fetch('/api/judgment', {method:'POST', body: JSON.stringify({kind:'explode'})})"
                ".then(async r => r.status + ' ' + (await r.json()).error.slice(0, 12))")

            # the page's link doors reach the host, not the network
            f.evaluate("window.parent.postMessage({type:'oneguy:open-session', session_id:'abc'}, '*')")
            f.evaluate("window.parent.postMessage({type:'oneguy:open-url', url:'https://example.com/x'}, '*')")
            f.evaluate("window.parent.postMessage({type:'oneguy:open-url', url:'file:///tmp/not-a-web-link'}, '*')")
            f.evaluate("window.parent.postMessage({type:'oneguy:rest', id: 99999, method:'GET', path:'/../x'}, '*')")
            pg.wait_for_timeout(400)
            calls = pg.evaluate("window.__calls")
            checks["open_session"] = [c["args"][0] for c in calls if c["name"] == "openSession"]
            checks["open_external"] = [c["args"][0] for c in calls if c["name"] == "openExternal"]

            # remote eyes: a script error, a refused request and a failed backend call inside the
            # canvas all reach GET /diag, with load timings, and nothing was there before them
            f.evaluate("setTimeout(() => { throw new Error('boom from the test') }, 0)")
            f.evaluate("fetch('https://example.com/x').catch(() => 0)")
            f.evaluate("fetch('/api/surface/poll?block_id=!!bad!!').then(r => r.status)")
            pg.wait_for_timeout(2600)
            diag1 = json.load(urllib.request.urlopen(base + "/api/plugins/1guy/diag"))
            checks["diag_sees"] = {"by_kind": sorted(diag1["by_kind"]), "version": diag1["plugin_version"],
                                   "boom": any("boom from the test" in e["message"] for e in diag1["entries"])}

            # the sounds choice was handed to the host to keep, and a fresh load starts from it
            pg.wait_for_timeout(900)
            page_json = json.load(urllib.request.urlopen(base + "/api/plugins/1guy/app/page"))
            pgr = b.new_page(viewport={"width": 1200, "height": 800})
            pgr.goto(base + "/harness/index.html")
            fr = canvas_frame(pgr)
            until(fr, "!!(window.OneGuy && window.OneGuy.sounds && document.querySelector('#og-snd'))")
            checks["choice_remembered"] = {"stored": page_json["local"].get("og.sounds.on"),
                                           "after_reload": fr.evaluate("window.OneGuy.sounds.on")}
            pgr.close()

            # a frame that never starts: an honest card with a reload button, and reload recovers
            pg2 = b.new_page(viewport={"width": 1000, "height": 700})
            pg2.goto(base + "/harness/index.html?break=frame")
            pg2.wait_for_selector("text=1guy did not finish loading", timeout=15000)
            pg2.screenshot(path=os.path.join(out, "frame-failed.png"))
            pg2.evaluate("window.__frameBroken = false")
            pg2.click("button:has-text(\"reload\")")
            f2 = canvas_frame(pg2)
            until(f2, "!!document.querySelector('#og-brief') && document.querySelector('#og-brief').innerText.includes('i went through your hermes')")
            checks["stuck_then_reload_recovers"] = True

            # the backend not answering: a different honest card, same button
            pg3 = b.new_page(viewport={"width": 1000, "height": 700})
            pg3.route("**/api/plugins/1guy/app/page", lambda r: r.abort())
            pg3.goto(base + "/harness/index.html")
            pg3.wait_for_selector("text=1guy is not answering", timeout=10000)
            pg3.unroute("**/api/plugins/1guy/app/page")
            pg3.click("button:has-text(\"reload\")")
            pg3.wait_for_function("document.querySelector('iframe')", timeout=10000)
            checks["down_then_reload_recovers"] = True
            b.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)       # the scratch home
        srv.kill()            # by PID; a long-poll in flight would stall a graceful stop
        srv.wait(timeout=10)
    want = {
        "frame_is_data_url": True, "csp_blocks_network": "blocked", "csp_violations": [],
        "sounds": {"silent_when_off": True, "on": True, "voices_started": True},
        "places_art": {"some": True, "all_decoded": True, "all_inline": True},
        "choice_remembered": {"stored": "1", "after_reload": True},
        "diag_clean_run": {"problems": 0, "timings": ["canvas_start", "first_board", "first_cards", "loader", "page_fetch"]},
        "styles": {"home": True, "kinds": True, "surface": True, "stage": True, "time": True, "links": 0}, "error_passthrough": "400 kind must be",
        "stuck_then_reload_recovers": True, "down_then_reload_recovers": True, "frame_origin_is_opaque": True, "sandbox": "allow-scripts allow-forms",
        "gateway_calls": ["session.create", "prompt.submit"], "submit_uses_runtime_id": True,
        "message_carries_item": True, "thread_stored_id": ["20260101_000001_fake"],
        "open_session": ["abc"],
        "contribs": ["routes:page", "sidebar.nav:nav", "panes:tasks-pane", "panes:dock", "palette:open",
                     "palette:show-dock", "palette:show-tasks", "transcript.directive:task-card"],
        "todo_pane_zone": {"dock": {"pane": "workspace", "pos": "right"}, "width": "320px"}, "open_external": ["https://example.com/x"],
    }
    failed = {k: checks.get(k) for k, v in want.items() if checks.get(k) != v}
    if checks.get("cards", 0) < 8:
        failed["cards"] = checks.get("cards")
    if "i went through your hermes" not in str(checks.get("briefing_line")):
        failed["briefing_line"] = checks.get("briefing_line")
    if checks.get("frame_url_bytes", 10 ** 9) > 4000:      # the page must not ride in the URL
        failed["frame_url_bytes"] = checks.get("frame_url_bytes")
    seen = checks.get("diag_sees") or {}
    if not (seen.get("boom") and {"csp", "error"} <= set(seen.get("by_kind") or [])):
        failed["diag_sees"] = seen
    if clean_errors:
        failed["page_errors"] = clean_errors[:5]
    print(json.dumps({"ok": not failed, "failed": failed, "checks": checks}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 9191,
                  sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix="1guy-shots-")))
