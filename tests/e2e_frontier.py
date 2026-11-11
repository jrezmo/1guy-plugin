#!/usr/bin/python3
"""The frontier patterns, in a real browser, INSIDE the plugin's sandboxed frame.

    env XDG_CACHE_HOME=~/.cache /usr/bin/python3 tests/e2e_frontier.py <port> <out-dir>

A lived-in synthetic Hermes home (tests/make_home.py). Four things the bare canvas is tested for
upstream, proven here through the loader, the fetch bridge and the frame's own coordinates:
  preview before yes   hold a pile proposal's button: the desk shows what it would do; let go, it stops
  change outlines      something a crew member moved while the user was away wears an outline
  lasso                shift-drag a box round cards on the board: they are picked, one row of words
  claim frame          "turn into a brief" on the picked cards: they wear his frame while he works
Prints one JSON line; exits non-zero on any failed check. Kills its server by PID.
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

# cards that sit together with empty canvas at a corner of the box round them (frame coordinates)
PICK = """() => {
  const cards = [...document.querySelectorAll('#og-cards .og-card[data-id]')].filter((n) => !n.classList.contains('og-gone'))
    .map((n) => { const r = n.getBoundingClientRect(); return { id: n.dataset.id, cluster: n.dataset.cluster, l: r.left, t: r.top, r: r.right, b: r.bottom }; })
    .filter((c) => c.r - c.l > 20 && c.t > 80 && c.b < innerHeight - 110 && c.l > 0 && c.r < innerWidth);
  const empty = (x, y) => { const e = document.elementFromPoint(x, y); return !!e && !e.closest('.og-card, .og-region-l, .og-fold, button, a, input, label, [data-nodrag], #og-rail, .tk-bar, header, .og-sel-menu') && !!e.closest('#og-stage'); };
  const inside = (c, B) => { const w = Math.min(B.r, c.r) - Math.max(B.l, c.l), h = Math.min(B.b, c.b) - Math.max(B.t, c.t); return w > 0 && h > 0 ? (w * h) / ((c.r - c.l) * (c.b - c.t)) : 0; };
  const byCluster = {};
  for (const c of cards) (byCluster[c.cluster] = byCluster[c.cluster] || []).push(c);
  for (const want of [3, 2]) for (const g of Object.values(byCluster).concat([cards])) {
    g.sort((a, b) => a.t - b.t || a.l - b.l);
    for (let i = 0; i + want <= g.length; i++) {
      const set = g.slice(i, i + want);
      const B = { l: Math.min(...set.map((c) => c.l)) - 7, t: Math.min(...set.map((c) => c.t)) - 7, r: Math.max(...set.map((c) => c.r)) + 7, b: Math.max(...set.map((c) => c.b)) + 7 };
      const got = cards.filter((c) => inside(c, B) >= 0.5).map((c) => c.id).sort();
      if (got.length !== want || got.join() !== set.map((c) => c.id).sort().join()) continue;
      for (const [sx, sy, ex, ey] of [[B.l, B.t, B.r, B.b], [B.r, B.t, B.l, B.b], [B.l, B.b, B.r, B.t], [B.r, B.b, B.l, B.t]]) {
        if (empty(sx, sy) && sy > 80 && sy < innerHeight - 110) return { ids: set.map((c) => c.id).sort(), from: [sx, sy], to: [ex, ey] };
      }
    }
  }
  return null;
}"""


def call(base, path, body=None):
    req = urllib.request.Request(base + "/api/plugins/1guy" + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def main(port, out):
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="1guy-e2e-")
    home = os.path.join(tmp, ".hermes")
    subprocess.run([sys.executable, os.path.join(HERE, "make_home.py"), home], check=True, stdout=subprocess.DEVNULL)
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
        # the desk settles (his pile proposals land a moment after the first read), then the user
        # "looks" at all of it, then a crew member moves one to-do up: that is the only change.
        ids, b = None, {}
        for _ in range(16):
            b = call(base, "/board?fresh=1")
            now = sorted(x["id"] for x in b["blocks"])
            if now == ids:
                break
            ids = now
            time.sleep(1.2)
        for x in b["blocks"]:
            if x.get("changed"):
                call(base, "/seen", {"id": x["id"]})
        call(base, "/seen", {"board": True})
        task = next(x["id"] for x in b["blocks"] if x["kind"] == "task" and x.get("status") not in ("done", "parked"))
        time.sleep(1.1)
        with open(os.path.join(home, "plugin-data", "1guy", "judgments.jsonl"), "a", encoding="utf-8") as j:
            j.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "promote", "block": task,
                                "target": None, "note": None, "by": "retro"}) + "\n")

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

            def at(f, x, y):                                  # frame coordinates -> page coordinates
                o = f.frame_element().bounding_box()
                return o["x"] + x, o["y"] + y

            pg.goto(base + "/harness/index.html")
            f = wait("document.querySelector('.og-brief-line') && document.querySelector('.og-fy')")

            # ---- change outlines
            try:
                f = wait("document.querySelector('.fx-moved')", 12)
                checks["outline"] = f.evaluate("""(() => { const n = document.querySelector('.fx-moved'); const w = n.querySelector('.fx-moved-what');
                    return { style: getComputedStyle(n).outlineStyle, what: w ? w.textContent.trim() : null }; })()""")
            except AssertionError:
                checks["outline"] = None
            pg.screenshot(path=os.path.join(out, "frontier-outline.png"))

            # ---- preview before yes
            v = f.locator(".og-fy .fx-verb-pv:visible")
            if v.count():
                v.first.evaluate("n => n.scrollIntoView({ block: 'center', inline: 'center' })")
                pg.wait_for_timeout(800)
                bb = v.first.bounding_box()                   # page coordinates already
                pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                pg.mouse.down()
                pg.wait_for_timeout(800)
                checks["hold_previews"] = f.evaluate("document.body.classList.contains('fx-previewing')")
                pg.screenshot(path=os.path.join(out, "frontier-preview.png"))
                pg.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] - 200, steps=4)     # slide off, so letting go is not a yes
                pg.mouse.up()
                pg.wait_for_timeout(500)
                checks["letting_go_ends_it"] = not f.evaluate("document.body.classList.contains('fx-previewing')")
            else:
                checks["hold_previews"] = "no pile proposal with a preview on this desk"

            try:                                                # he types his line out: wait for the whole sentence
                f = wait("document.querySelector('.og-brief-line').textContent.includes('moved while you were away')", 10)
            except AssertionError:
                pass
            checks["opener"] = f.evaluate("document.querySelector('.og-brief-line').textContent")[:160]
            # ---- lasso, then the claim frame
            f.evaluate("document.querySelector('#og-lens [data-lens=\"board\"]').click()")
            f = wait("document.body.dataset.view !== 'briefing' && document.querySelectorAll('#og-cards .og-card').length > 3")
            pg.wait_for_timeout(1500)
            pk = f.evaluate(PICK)
            checks["cards_to_box"] = bool(pk)
            if pk:
                pg.keyboard.down("Shift")
                pg.mouse.move(*at(f, *pk["from"]))
                pg.mouse.down()
                pg.mouse.move(*at(f, (pk["from"][0] + pk["to"][0]) / 2, (pk["from"][1] + pk["to"][1]) / 2), steps=5)
                checks["box_drawn"] = f.locator(".og-lasso").count() == 1
                pg.mouse.move(*at(f, *pk["to"]), steps=6)
                pg.mouse.up()
                pg.keyboard.up("Shift")
                pg.wait_for_timeout(500)
                checks["picked"] = f.evaluate("OneGuy.selected.slice().sort()") == pk["ids"]
                checks["menu"] = [x.inner_text() for x in f.locator(".og-sel-menu button:visible").all()]
                checks["bar_says"] = f.inner_text(".tk-about-t") if f.locator(".tk-about-t").count() else None
                pg.screenshot(path=os.path.join(out, "frontier-lasso.png"))
                # the frame can come and go inside one poll (no model here: the plain brief is quick), so watch for it
                f.evaluate("""() => { window.__claim = { cards: 0, word: null };
                    new MutationObserver(() => { const n = document.querySelectorAll('#og-cards .og-card.og-claim').length;
                      if (n > window.__claim.cards) window.__claim = { cards: n, word: (document.querySelector('.og-claim-tag .og-claim-t') || {}).textContent || null }; })
                      .observe(document.body, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true }); }""")
                f.click(".og-sel-menu button:has-text('turn into a brief')")
                pg.wait_for_timeout(4000)
                checks["claim_frame"] = f.evaluate("window.__claim")
                checks["brief_written"] = f.evaluate("[...document.querySelectorAll('#og-cards .og-card')].filter(n => /brief/i.test(n.textContent)).length")
                pg.screenshot(path=os.path.join(out, "frontier-claim.png"))
                pg.wait_for_timeout(1500)
            br.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        srv.kill()
        srv.wait(timeout=10)
    want = {"outline": lambda v: bool(v) and v["style"] == "dashed" and bool(v["what"]),
            "opener": lambda v: "moved while you were away" in (v or ""),
            "hold_previews": lambda v: v is True, "letting_go_ends_it": lambda v: v is True,
            "cards_to_box": lambda v: v is True, "box_drawn": lambda v: v is True, "picked": lambda v: v is True,
            "menu": lambda v: v == ["ask about these", "group", "put away", "turn into a brief"],
            "bar_says": lambda v: bool(v) and v.startswith("about: ") and v.endswith(" cards"),
            "claim_frame": lambda v: bool(v) and v["cards"] >= 2 and bool(v["word"]),
            "brief_written": lambda v: bool(v)}
    failed = {k: checks.get(k) for k, ok in want.items() if not ok(checks.get(k))}
    print(json.dumps({"checks": checks, "problems": problems, "failed": failed}))
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
