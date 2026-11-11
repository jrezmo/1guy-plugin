#!/usr/bin/env python3
"""Vendor the 1guy canvas (client + the python it needs) into this plugin.

    python3 scripts/vendor_canvas.py <1guy-ref> [--repo ~/projects/1guy]

Reads the 1guy repo at <ref> through `git archive` (never touches its working tree) and
rewrites two things here, wholesale, so a rerun is always safe:

  og_core/vendor/app/         copies (verbatim but for PATCHES + SCRUB below): home.html, home/, icons/icon.svg, lab/places/, board/, the
                             route modules in ROUTES, router.py, talk_resolve.py, threads.py, feed.py
  og_core/static/canvas.html  ONE self-contained page built from those copies: every stylesheet
                             inlined, the ES modules bundled by esbuild into one inline module,
                             and bridge.js (our fetch -> postMessage shim) ahead of it.

The page has to be one document because the desktop app shows it in a sandboxed frame with an
opaque origin: it cannot fetch siblings, and plugin routes need auth only ctx.rest carries.

esbuild is a build-time tool only (nothing at runtime needs node). It is found via $ESBUILD,
PATH, or `npx --yes esbuild@0.28.2`.
"""
import ast
import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENDOR = os.path.join(ROOT, "og_core", "vendor")
STATIC = os.path.join(ROOT, "og_core", "static")

# Route modules the plugin serves. Everything else in app/routes is J's private box
# (build watch, news, brain, trace, reader, runs page, artifacts) and stays out.
ROUTES = ["board", "briefing", "feed", "home_state", "judgments", "mailcal", "stage", "surface", "talk", "threads"]
PATHS = (["app/home.html", "app/home", "app/icons/icon.svg", "app/lab/places", "app/board",
          "app/router.py", "app/talk_resolve.py", "app/threads.py", "app/feed.py"]
         + [f"app/routes/{r}.py" for r in ROUTES])
SKIP = re.compile(r"(^|/)(__pycache__|fixtures)(/|$)|harness\.html$|\.fixture\.json$|verify\.py$")
# The only edits made to vendored text. Each is (file under app/, old, new) and must match exactly
# once, so an upstream change fails loudly instead of silently dropping the patch.
PATCHES = [
    # the catalog's prompt-injection scanner reads "send it as a conversation" as an exfil order
    # a data: document has no query string, so "housed in the desktop app" comes from the bridge
    ("home/core.js", "export const PANE = params.get('pane') === '1';",
     "export const PANE = params.get('pane') === '1' || window.ONEGUY_EMBED === true;"),
    # images cannot be fetched from inside the frame, so the page carries them (ASSETS below)
    # and the client's one URL resolver hands them out
    ("home/core.js", "  const p = String(path == null ? '' : path);\n",
     "  const p = String(path == null ? '' : path);\n"
     "  if (window.ONEGUY_ASSETS && window.ONEGUY_ASSETS[p]) return window.ONEGUY_ASSETS[p];\n"),
    # the sample desk is entered and left by changing the page URL, which a sandboxed data:
    # document cannot do; when embedded, the switch is asked of the host and the flag comes back
    # from the loader (window.ONEGUY_SAMPLE)
    ("home/core.js", "export const SAMPLE = params.get('sample') === '1';",
     "export const SAMPLE = params.get('sample') === '1' || window.ONEGUY_SAMPLE === true;"),
    ("home/doors.js", "export const leaveSample = () => location.assign(sampleUrl(false));",
     "const hostSample = (on) => window.parent.postMessage({ type: 'oneguy:sample', on }, '*');\n"
     "export const leaveSample = () => (window.ONEGUY_EMBED ? hostSample(false) : location.assign(sampleUrl(false)));"),
    ("home/doors.js", "  sample.onclick = () => location.assign(sampleUrl(true));",
     "  sample.onclick = () => (window.ONEGUY_EMBED ? hostSample(true) : location.assign(sampleUrl(true)));"),
    ("home/talk.js", "(tap to send it as a conversation)", "(tap to talk it through instead)"),
]
# Modules replaced by a stub: sources that only exist on the private box (GitHub issues through
# the gh CLI, a media folder, another plugin's tiles) and the Gmail glance, which reads and
# refreshes an OAuth token file (catalog rule 11 forbids that). The board package imports them
# by name, so they must exist; the stub is an always-empty source.
STUB = '''"""Not part of the plugin build (see scripts/vendor_canvas.py STUB): an always-empty source."""
TTL = 3600.0
CFG_KEY = "unused"


def fetch(*_a, **_k):
    return {"blocks": [], "count": 0}


def media_mentions(*_a, **_k):
    return []


def inbox(*_a, **_k):
    return {"widget": "email", "wired": False, "unread": 0, "total": None, "items": []}
'''
STUBBED = ["board/crew.py", "board/media.py", "board/plugins.py", "board/email_glance.py"]

# Functions whose bodies are replaced (found by name with ast, so an upstream edit inside them
# does not break the vendor step, but a rename does). These were the API-server + .env paths:
# in the plugin, model calls go through the host (og_core.talk.llm) and agent turns through
# the desktop relay, so nothing here may read a key file or call a local port.
GONE = "the plugin build has no API-server path; og_core.talk supplies this seam"
REDEF = [
    ("board/headlines.py", "api_key", 'def api_key():\n    return ""\n'),
    ("board/headlines.py", "chat", f'def chat(prompt, timeout=TIMEOUT_S):\n    raise RuntimeError("{GONE}")\n'),
    ("router.py", "api_key", 'def api_key():\n    return ""\n'),
    ("routes/surface.py", "hermes_key", 'def hermes_key():\n    return ""\n'),
    ("routes/surface.py", "_hreq", f'def _hreq(method, path, body=None, timeout=30):\n    raise RuntimeError("{GONE}")\n'),
    ("routes/surface.py", "create_session", f'def create_session(title):\n    raise RuntimeError("{GONE}")\n'),
    ("routes/surface.py", "stop_run", 'def stop_run(hermes_run_id):\n    return {}\n'),
    ("routes/surface.py", "r_transcribe", 'def r_transcribe(q, body):\n'
     '    return {"error": "voice input is not part of the plugin yet; type instead"}, 501\n'),
]

# Scrubbed from every vendored text file: the private box 1guy grew up on. These were only ever
# defaults (overridden by og_core.store / board), but a public plugin must not carry them.
SCRUB = [
    (re.compile(r"https?://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net(:\d+)?"), "http://oneguy.invalid"),
    (re.compile(r"[a-z0-9-]+\\\.[a-z0-9-]+\\\.ts\\\.net"), r"oneguy\\.invalid"),
    (re.compile(r"http://127\.0\.0\.1:(8120|8170|8642)"), "http://oneguy.invalid"),
    # a unix home path ("/home/<user>/..."), not the client's own app/home/ folder
    (re.compile(r"(?<![\w./])/home/[a-z][a-z0-9_-]*/"), ""),
    (re.compile(r"~/\.hermes/\.env"), "the Hermes env file"),
    (re.compile(r":(8120|8170|8642)\b"), ""),
]
FORBIDDEN = re.compile(r"(?<![\w./])/home/[a-z][a-z0-9_-]*/|\.ts\.net|@gmail\.|\b(8120|8170|8642)\b")
# Names and handles are scrubbed from a local, uncommitted list so this public script does not
# have to spell them: scripts/scrub.local.tsv, one "regex<TAB>replacement" per line. Every
# regex in it is also treated as forbidden after scrubbing.
LOCAL_SCRUB = os.path.join(HERE, "scrub.local.tsv")


# 1guy grew up with one user, and its python (the prompts above all) calls him by his initial.
# On anyone else's Hermes the model must hear "the user", or a card ends up saying "needs J".
# Python only: the client never shows the initial (its J is a key on the keyboard).
_INITIAL = re.compile(r"(?P<pre>(?:^|[\^\"'.!?]\s*)?)\bJ\b")


def the_user(text):
    def sub(m):
        pre = m.group("pre")
        start = m.start() == 0 or pre.strip()[-1:] in ('^', '"', "'", ".", "!", "?")
        return pre + ("The user" if start else "the user")
    text = text.replace('"J via bar', '"the talk bar').replace("J's (the user's)", "the user's")
    text = re.sub(r"(\\[nt])J\b", r"\1The user", text)          # "\nJ says:" (no word boundary after an escape)
    return _INITIAL.sub(sub, text)


def scrub_rules():
    rules, forbidden = list(SCRUB), [FORBIDDEN]
    if os.path.exists(LOCAL_SCRUB):
        for line in open(LOCAL_SCRUB, encoding="utf-8"):
            if line.strip() and not line.startswith("#"):
                rx, _, rep = line.rstrip("\n").partition("\t")
                rules.insert(0, (re.compile(rx), rep))
                forbidden.append(re.compile(rx))
    else:
        print("vendor: note: no scripts/scrub.local.tsv; only the generic scrub ran", file=sys.stderr)
    return rules, forbidden


# The canvas document can run its own inline code and styles and nothing else: no network, no
# frames, no remote images. Its only way out is postMessage to the host page (bridge.js).
CSP = ('<meta http-equiv="Content-Security-Policy" content="'
       "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; "
       "frame-src 'none'; base-uri 'none'; form-action 'none'"
       '">\n')
# Images the canvas shows, carried inside the page as data: URIs keyed by the path the client
# asks for (the page policy allows images from data: only).
ASSETS = ("lab/places", ".webp", "image/webp")


def stylesheets(app):
    """Every stylesheet the client has, home.css first, and the marker that stops each module
    from loading its own. Modules bring a stylesheet with
        if (document.querySelector('link[<attr>]')) return;  ...  new URL('./x.css', import.meta.url)
    so for each such loader we find its guard and emit an inert <link <attr>> (no rel, no href:
    it loads nothing). A loader without a guard we can satisfy fails the build, because the
    page policy would refuse that <link> at runtime and the styles are already inlined here."""
    css, markers = [], []
    for base, _dirs, files in os.walk(os.path.join(app, "home")):
        for name in sorted(files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, app)
            if name.endswith(".css"):
                css.append(rel)
            elif name.endswith(".js"):
                text = open(path, encoding="utf-8").read()
                loads = len(re.findall(r"new URL\('\./[\w-]+\.css', import\.meta\.url\)", text))
                guards = re.findall(r"querySelector\('link\[([^\]]+)\]'\)", text)
                if loads != len(guards):
                    sys.exit(f"vendor: {rel} loads {loads} stylesheet(s) but has {len(guards)} "
                             f"link[...] guard(s); teach stylesheets() about it")
                for attr in guards:
                    markers.append(attr if "=" in attr else attr + '="1"')
    css.sort(key=lambda c: (c != "home/home.css", c))
    return css, markers


def sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, **kw)


def esbuild_cmd():
    if os.environ.get("ESBUILD"):
        return [os.environ["ESBUILD"]]
    if shutil.which("esbuild"):
        return ["esbuild"]
    return ["npx", "--yes", "esbuild@0.28.2"]


def extract(repo, ref, dest):
    sha = sh(["git", "-C", repo, "rev-parse", ref]).stdout.decode().strip()
    present = [p for p in PATHS
               if subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{sha}:{p}"],
                                 capture_output=True).returncode == 0]
    missing = sorted(set(PATHS) - set(present))
    if missing:
        sys.exit(f"vendor: {ref} lacks {missing}")
    tar = sh(["git", "-C", repo, "archive", sha, *present]).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        for m in tf.getmembers():
            if not m.isfile() or SKIP.search(m.name):
                continue
            out = os.path.join(dest, m.name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(tf.extractfile(m).read())
            n += 1
    for rel, old, new in PATCHES:
        path = os.path.join(dest, "app", rel)
        text = open(path, encoding="utf-8").read()
        if text.count(old) != 1:
            sys.exit(f"vendor: patch for {rel} no longer applies ({text.count(old)} matches of {old!r})")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new))
    for rel in STUBBED:
        with open(os.path.join(dest, "app", rel), "w", encoding="utf-8") as f:
            f.write(STUB)
    for rel, name, new in REDEF:
        path = os.path.join(dest, "app", rel)
        text = open(path, encoding="utf-8").read()
        fns = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(fns) != 1:
            sys.exit(f"vendor: {rel} no longer defines exactly one top-level {name}()")
        lines = text.splitlines(keepends=True)
        lines[fns[0].lineno - 1:fns[0].end_lineno] = [new]
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(lines))
    scrubbed = 0
    rules, forbidden = scrub_rules()
    for base, _dirs, files in os.walk(dest):
        for name in files:
            path = os.path.join(base, name)
            try:
                text = open(path, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            new = text
            for rx, rep in rules:
                new = rx.sub(rep, new)
            if name.endswith(".py"):
                new = the_user(new)
            hit = next((m for m in (f.search(new) for f in forbidden) if m), None)
            if hit:
                sys.exit(f"vendor: {os.path.relpath(path, dest)} still names the private box: "
                         f"{hit.group(0)!r}; extend SCRUB")
            if new != text:
                scrubbed += 1
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
    return sha, n, scrubbed


def build_page(app):
    """app = the vendored app/ dir -> the single-document canvas."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "bundle.js")
        try:
            # run from app/ so the bundle's per-module comments read "home/core.js", not a temp path
            sh(esbuild_cmd() + ["home/main.js", "--bundle", "--format=esm",
                                "--target=es2022", "--log-level=error",
                                # stylesheets are inlined below and the page policy refuses the leftover <link>s,
                                # so this base only has to parse; nothing is ever fetched from it
                                '--define:import.meta.url="https://oneguy.invalid/app/home/main.js"',
                                f"--outfile={out}"], cwd=app)
        except subprocess.CalledProcessError as e:
            sys.exit("vendor: esbuild failed\n" + e.stderr.decode()[-2000:])
        bundle = open(out, encoding="utf-8").read()
    css_files, markers_found = stylesheets(app)
    css = "\n".join(f"/* {c} */\n" + open(os.path.join(app, c), encoding="utf-8").read() for c in css_files)
    css += "\n" + open(os.path.join(HERE, "plugin.css"), encoding="utf-8").read()
    bridge = open(os.path.join(HERE, "bridge.js"), encoding="utf-8").read()
    html = open(os.path.join(app, "home.html"), encoding="utf-8").read()
    icon = open(os.path.join(app, "icons", "icon.svg"), encoding="utf-8").read()

    folder, ext, mime = ASSETS
    assets = {}
    for name in sorted(os.listdir(os.path.join(app, folder))):
        if name.endswith(ext):
            with open(os.path.join(app, folder, name), "rb") as f:
                assets[f"/app/{folder}/{name}"] = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    if not assets:
        sys.exit(f"vendor: no {ext} files under app/{folder}")
    bridge = "window.ONEGUY_ASSETS = " + json.dumps(assets, indent=0) + ";\n" + bridge

    def inline_js(s):
        return s.replace("</script", "<\\/script")

    # drop every <link> (manifest, icons, stylesheet): none can load from an opaque origin
    html, n_links = re.subn(r"[ \t]*<link\b[^>]*>\n?", "", html)
    markers = "".join(f"<link {m}>\n" for m in markers_found)
    html, n_css = re.subn(r"</head>", lambda _: CSP + markers + "<style>\n" + css.replace("</style", "<\\/style")
                          + "\n</style>\n</head>", html, count=1)
    html, n_js = re.subn(r'<script type="module" src="(?:/app/)?home/main\.js"></script>',
                         lambda _: "<script>\n" + inline_js(bridge) + "\n</script>\n"
                         '<script type="module">\n' + inline_js(bundle) + "\n</script>", html, count=1)
    if not (n_links and n_css == 1 and n_js == 1):
        sys.exit(f"vendor: home.html changed shape (links={n_links} css={n_css} js={n_js}); update build_page")
    return html, icon


def main():
    argv, repo = sys.argv[1:], os.path.expanduser("~/projects/1guy")
    if "--repo" in argv:
        i = argv.index("--repo")
        repo = os.path.expanduser(argv[i + 1])
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    ref = args[0]
    with tempfile.TemporaryDirectory() as tmp:
        sha, n, scrubbed = extract(repo, ref, tmp)
        html, _icon = build_page(os.path.join(tmp, "app"))
        shutil.rmtree(VENDOR, ignore_errors=True)
        os.makedirs(VENDOR)
        shutil.copytree(os.path.join(tmp, "app"), os.path.join(VENDOR, "app"))
    os.makedirs(STATIC, exist_ok=True)
    with open(os.path.join(STATIC, "canvas.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(VENDOR, "VENDOR.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "1guy", "ref": ref, "sha": sha, "files": n, "scrubbed_files": scrubbed, "routes": ROUTES,
                   "patches": [p[0] for p in PATCHES]}, f, indent=2)
        f.write("\n")
    print(f"vendored 1guy {ref} ({sha[:10]}): {n} files -> og_core/vendor/app, "
          f"canvas.html {len(html) // 1024} KB")


if __name__ == "__main__":
    main()
