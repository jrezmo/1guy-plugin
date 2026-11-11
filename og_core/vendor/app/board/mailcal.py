"""mailcal: email and calendar through the user's own Hermes (docs/product.md, feature 8).

1guy never reads mail or a calendar itself and makes no network call for them. The user's Hermes
does, with its own skills/tools, in a normal scheduled job named `1guy-mail-cal`; this module reads
what that job last wrote (HERMES_HOME/cron/output/<job id>/*.md, read-only) and turns it into the
email tile, the calendar tile, `event` blocks for the Time lens and reply-needed cards.

    capability(home=None)   -> {email: bool, calendar: bool, via: [names]}   read-only
    PROMPT, SCHEDULE        the job (one constant each); create_command() is the exact argv
    extract(text)           -> the last digest JSON object in a cron output file, or None
    digest(now)             -> {job, data, as_of, age_s, stale, error}        read-only
    blocks(now)             -> [widget:calendar, widget:email, event:…, note:mail-…]
    waiting_order(rows,now) -> the inbox as next actions: people first, oldest first; the ONE ordering rule
    mail_judge(body)        -> later / not mine / talk about one email: a ledger line + (for the first
                               two) the row is hidden in ONEGUY_HOME/mail_hidden.json. Never touches the mailbox.
    setup(runner=None)      -> creates the job, then runs it once (the user's yes)
    not_now()               -> remembers the no (ONEGUY_HOME/mailcal.json); the tiles stay quiet

Each tile is in one of three honest states: real data · an offer to set it up (this Hermes can
read mail/calendar, the job isn't there) · "your hermes can't read email yet".

The only way anything here runs a command is `runner(argv) -> (returncode, output)`, an injectable
hook: tests stub it, the plugin supplies the subprocess call, and left as None nothing is run.
Stdlib only. Nothing outside HERMES_HOME / ONEGUY_HOME.
"""
import datetime as _dt
import json
import os
import re

from . import jobs, ledger, util
from .block import make

JOB_NAME = "1guy-mail-cal"
SCHEDULE = "0 7-22 * * *"        # hourly, 7am-10pm, read in the profile's (the user's) timezone
MAX_EMAIL, MAX_EVENTS = 15, 40
EMAIL_DAYS, CAL_DAYS = 3, 7
WHY_WORDS = 12
SNIPPET_WORDS = 40
STALE_S = 2 * 3600               # older than this, the tile says how old
ROWS = 8                         # rows carried on a tile
REPLY_CARDS = 5                  # reply-needed emails that become cards

PROMPT = f"""You are the read-only mail and calendar digest for 1guy.

Use your own email and calendar tools or skills. READ ONLY: never send, reply, forward, draft,
archive, delete, label, mark read, accept, decline, create or edit anything. Do not message anyone.

Collect:
- email: messages from the last {EMAIL_DAYS} days that are unread or that need a reply from me, newest
  first, at most {MAX_EMAIL}. Skip newsletters, receipts and automated notices unless they need an answer.
- calendar: every event in the next {CAL_DAYS} days starting now, earliest first, at most {MAX_EVENTS}.

Then print EXACTLY ONE JSON object and nothing else (no prose, no code fence):

{{"at": "<now, ISO-8601 with offset>",
 "email": [{{"id": "<message id>", "from": "<sender name>", "subject": "<subject>",
            "address": "<sender address>", "received": "<ISO-8601>", "needs_reply": true,
            "why": "<plain words, at most {WHY_WORDS}>", "snippet": "<how the message starts, at most {SNIPPET_WORDS} words>",
            "list": false}}],
 "calendar": [{{"id": "<event id>", "title": "<title>", "start": "<ISO-8601, or YYYY-MM-DD if all day>",
               "end": "<ISO-8601 or YYYY-MM-DD>", "all_day": false, "where": "<place or empty>",
               "who": ["<other people's names>"]}}],
 "errors": ["<one short line for anything you could not read>"]}}

"needs_reply" is true only when a person is waiting on an answer from me. "why" says what they want.
"snippet" is the message's own opening words, copied, not rewritten. "list" is true for bulk mail
(it has a List-Unsubscribe header, or it is a newsletter, receipt or automated notice).
If you have no email tool, leave "email" empty and say so in "errors"; the same for the calendar.
Never invent a message or an event."""

runner = None                    # the plugin / the route shim sets this: runner(argv) -> (rc, output)

_MAIL_IN = ("gmail", "email", "e-mail", "outlook", "fastmail", "protonmail")
_MAIL_TOK = {"mail", "imap", "inbox", "himalaya", "mutt", "jmap"}
_CAL_IN = ("calendar", "gcal", "caldav")
_CAL_TOK = {"cal", "ical", "ics"}
_BOTH_IN = ("google-workspace", "google_workspace", "googleworkspace", "gsuite", "microsoft-365",
            "microsoft365", "office365", "office-365", "ms-graph", "msgraph")
_BOTH_TOK = {"gws", "gog", "m365", "o365"}
_SKIP_DIRS = {"references", "scripts", "assets", "templates", "node_modules", "__pycache__"}


# ------------------------------------------------------------------ 1. can this Hermes read them?
def hermes_home():
    """The Hermes home the board already reads: CFG["hermes_home"] if set, else the folder that
    holds cron/jobs.json."""
    return util.CFG.get("hermes_home") or os.path.dirname(os.path.dirname(util.CFG["cron_jobs"]))


def _kind_of(name):
    """-> (email?, calendar?) for one skill folder / MCP server name."""
    n = str(name or "").lower()
    toks = set(re.split(r"[^a-z0-9]+", n))
    both = any(s in n for s in _BOTH_IN) or bool(toks & _BOTH_TOK)
    mail = both or any(s in n for s in _MAIL_IN) or bool(toks & _MAIL_TOK)
    cal = both or any(s in n for s in _CAL_IN) or bool(toks & _CAL_TOK)
    return mail, cal


def skill_names(home):
    """Folder names under HERMES_HOME/skills, three levels deep (skills sit in category folders)."""
    root = os.path.join(home, "skills")
    out = []
    for cur, dirs, _files in os.walk(root):
        depth = cur[len(root):].count(os.sep)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS)
        if depth >= 2:
            out += dirs
            dirs[:] = []
        else:
            out += dirs
    return out


def mcp_names(home):
    """Server names under `mcp_servers:` in HERMES_HOME/config.yaml. A small indent reader, no
    YAML library: the keys one level under the section are the names. Values are never read."""
    try:
        with open(os.path.join(home, "config.yaml"), encoding="utf-8", errors="replace") as f:
            lines = f.read(400_000).splitlines()
    except OSError:
        return []
    out, base, child = [], None, None
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        if base is None:
            if re.match(r"^\s*mcp_servers\s*:\s*(#.*)?$", ln):
                base = ind
            continue
        if ind <= base:
            break
        child = ind if child is None else child
        m = re.match(r"^\s*['\"]?([A-Za-z0-9_.@ -]+?)['\"]?\s*:", ln)
        if ind == child and m:
            out.append(m.group(1).strip())
    return out


def capability(home=None):
    """Does this Hermes have email and/or calendar ability? Names only; reads no skill bodies,
    no credentials. -> {email, calendar, via: [the names that said so]}"""
    home = home or hermes_home()
    email = cal = False
    via = []
    for name in skill_names(home) + ["mcp:" + n for n in mcp_names(home)]:
        m, c = _kind_of(name[4:] if name.startswith("mcp:") else name)
        if (m or c) and name not in via:
            via.append(name)
        email, cal = email or m, cal or c
    return {"email": email, "calendar": cal, "via": via[:12]}


# ------------------------------------------------------------------ 2. the job
def create_command():
    """The exact argv that creates the job (`hermes cron create --help`, Hermes 0.23). No
    --timezone: the schedule is read in the profile's timezone, which is the user's."""
    return ["hermes", "cron", "create", SCHEDULE, PROMPT, "--name", JOB_NAME, "--deliver", "local"]


def run_command(job_id):
    return ["hermes", "cron", "run", str(job_id)]


def find_job():
    """-> the `1guy-mail-cal` job record from cron/jobs.json, or None."""
    try:
        js = jobs.load_jobs()
    except Exception:   # noqa: BLE001 - no cron file is simply "no job"
        return None
    mine = [j for j in js if isinstance(j, dict) and j.get("name") == JOB_NAME and j.get("id")]
    return mine[-1] if mine else None


# ------------------------------------------------------------------ 3. what the job wrote
def extract(text):
    """-> the LAST JSON object in a cron output file that looks like the digest (has an "email" or
    a "calendar" key), or None. Prose around it, code fences, an earlier draft object and broken
    JSON are all survived: every `{` is tried, the last one that parses and fits wins."""
    if not text:
        return None
    m = None
    for m in re.finditer(r"^## Response\s*$", text, re.M):
        pass
    body = text[m.end():] if m else text       # only what the agent answered: the file echoes the prompt too
    dec, found, i = json.JSONDecoder(), None, body.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(body, i)
        except ValueError:
            i = body.find("{", i + 1)
            continue
        if isinstance(obj, dict) and ("email" in obj or "calendar" in obj):
            found = obj
        i = body.find("{", end)
    return found


def _fit(s, cap=WHY_WORDS):
    return " ".join(str(s or "").split()[:cap]).rstrip(",;:")


def _sender(s):
    """'Priya Shah <priya@example.org>' -> 'Priya Shah'; a bare address keeps its name part."""
    s = util.clip(s, 120)
    m = re.match(r'^\s*"?([^"<]*?)"?\s*<[^>]*>\s*$', s)
    name = (m.group(1).strip() if m else s) or s.strip("<> ")
    return util.clip(name.split("@")[0] if "@" in name and " " not in name else name, 60)


def _addr(s):
    """'Priya Shah <priya@example.org>' -> 'priya@example.org'; no address in it -> ''."""
    m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", str(s or ""))
    return m.group(0).lower()[:120] if m else ""


def _emails(raw):
    out, seen = [], set()
    for i, e in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(e, dict) or not (e.get("subject") or e.get("from")):
            continue
        mid = util.clip(e.get("id") or f"n{i}", 80)
        if mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "from": _sender(e.get("from")), "addr": _addr(e.get("address") or e.get("from")),
                    "subject": util.clip(e.get("subject") or "(no subject)", 120),
                    "received": util.iso(util.parse_ts(e.get("received"))),
                    "needs_reply": e.get("needs_reply") is True, "why": _fit(e.get("why")),
                    "snippet": _fit(e.get("snippet"), SNIPPET_WORDS + 10), "bulk": e.get("list") is True})
        if len(out) >= MAX_EMAIL:
            break
    return out


def _events(raw, now):
    floor = _dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    out, seen = [], set()
    for i, e in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(e, dict) or not e.get("start"):
            continue
        start = str(e["start"]).strip()
        t = util.parse_ts(start)
        if t is None:
            continue
        all_day = e.get("all_day") is True or "T" not in start
        end = str(e.get("end") or "").strip() or None
        te = util.parse_ts(end) if end else None
        if (te if te is not None else t) < floor and not (all_day and t >= floor - 86400 and te and te > floor):
            continue                                   # already over before today
        eid = util.clip(e.get("id") or f"n{i}", 80)
        if eid in seen:
            continue
        seen.add(eid)
        who = [util.clip(w, 40) for w in (e.get("who") if isinstance(e.get("who"), list) else []) if isinstance(w, str) and w.strip()]
        out.append({"id": eid, "title": util.clip(e.get("title") or "(busy)", 120), "start": start[:10] if all_day else start,
                    "end": (end[:10] if all_day else end) if te is not None else None, "all_day": all_day,
                    "where": util.clip(e.get("where"), 60), "who": who[:6], "_t": t})
        if len(out) >= MAX_EVENTS:
            break
    out.sort(key=lambda x: x["_t"])
    return out


def ago(span_s):
    s = max(0.0, span_s or 0.0)
    if s < 3600:
        return f"{max(1, int(s // 60))}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d} days ago"


def digest(now=None):
    """-> {job, data: {email, calendar, errors} | None, as_of, age_s, stale, error}. Read-only.
    `as_of` is the output file's own time (when the run wrote it), never the model's word for it.
    `error` says what failed in plain words: the last run broke, or it didn't print the digest."""
    now = now or util.now()
    job = find_job()
    out = {"job": job, "data": None, "as_of": None, "age_s": None, "stale": False, "error": None}
    if not job:
        return out
    text, mtime = jobs.latest_output(job["id"])
    last_run = util.parse_ts(job.get("last_run_at"))
    failed = job.get("last_status") in ("error", "failed", "timeout")
    raw = extract(text)
    if raw is not None:
        errs = [util.clip(x, 140) for x in (raw.get("errors") if isinstance(raw.get("errors"), list) else []) if isinstance(x, str) and x.strip()]
        out["data"] = {"email": _emails(raw.get("email")), "calendar": _events(raw.get("calendar"), now), "errors": errs[:6]}
        out["as_of"], out["age_s"] = util.iso(mtime), max(0.0, now - (mtime or now))
        out["stale"] = out["age_s"] > STALE_S
    if failed and (raw is None or (last_run or 0) > (mtime or 0) + 60):
        why = util.safe_excerpt(job.get("last_error") or job.get("last_status") or "", 120)
        out["error"] = "the last read failed" + (f": {why}" if why else "")
    elif raw is None and text:
        out["error"] = "the last read didn't come back as a digest i can use"
    return out


# ------------------------------------------------------------------ the tiles, in his voice
OFFER = {
    (True, True): "want me to keep an eye on your email and calendar? your hermes can read them.",
    (True, False): "want me to keep an eye on your email? your hermes can read it.",
    (False, True): "want me to keep an eye on your calendar? your hermes can read it.",
}
CANT = {
    "email": "your hermes can't read email yet. add an email or calendar skill and i'll pick it up.",
    "calendar": "your hermes can't read your calendar yet. add a calendar skill and i'll pick it up.",
}
WAITING = "the first read is on its way. about a minute."


def _prefs():
    d = util.read_json(util.oneguy("mailcal.json"), None)
    return d if isinstance(d, dict) else {}


def _tile(ref, state, note, **extra):
    d = {"widget": ref, "wired": state == "ready", "state": state, "count": None, "overdue": 0,
         "next": None, "items": [], "note": note, "readonly": True}
    d.update(extra)
    return make("widget", ref, ref, summary=note, status="idle", tags=["housekeeping"],
                source_type="hermes-digest", detail=d,
                match={"tags": ["housekeeping"], "text": f"{ref} glance"})


def _errors_for(ref, errs):
    words = ("mail", "inbox", "message") if ref == "email" else ("calendar", "event")
    return [e for e in errs if any(w in e.lower() for w in words)]


def _quiet_tile(ref, dg, cap):
    """No data to show: the offer, the can't, the wait, or what failed."""
    if dg["job"]:
        if dg["error"]:
            return _tile(ref, "failed", dg["error"], error=dg["error"], job_id=dg["job"]["id"])
        return _tile(ref, "waiting", WAITING, job_id=dg["job"]["id"])
    if cap[ref]:
        text = OFFER[(cap["email"], cap["calendar"])]
        declined = bool(_prefs().get("declined_at"))
        return _tile(ref, "offer", text, declined=declined, via=cap["via"],
                     offer={"text": text, "yes": "yes", "no": "not now", "post": "/api/board/mailcal"})
    return _tile(ref, "cant", CANT[ref])


def _stamp(b, dg):
    d = b["detail"]
    d["as_of"], d["stale"] = dg["as_of"], dg["stale"]
    if dg["stale"]:
        d["as_of_text"] = "as of " + ago(dg["age_s"])
    if dg["error"]:
        d["last_error"] = dg["error"]            # older data is still shown, with what went wrong since
    d["job_id"] = dg["job"]["id"]
    return b


def email_tile(dg, cap, now=None):
    data = dg["data"]
    mine = _errors_for("email", data["errors"]) if data else []
    if not data or (not data["email"] and mine):
        if data and mine:
            return _stamp(_tile("email", "failed", mine[0], error=mine[0]), dg)
        return _quiet_tile("email", dg, cap)
    now = now or util.now()
    rows = waiting_order(visible(data["email"], now), now)
    n = len([r for r in rows if r["person"]])
    b = make("widget", "email", "email", summary=mail_summary(rows, now), status="active" if n else "idle",
             tags=["housekeeping"], updated=dg["as_of"], source_type="hermes-digest",
             detail={"widget": "email", "wired": True, "state": "ready", "readonly": True, "overdue": 0,
                     "unread": len(rows), "empty": "nothing waiting on you", **mail_detail(rows, now)},
             match={"tags": ["housekeeping"], "text": "email glance"})
    b["headline"] = {"title": opener(rows, now), "why": "", "own": True}
    return _stamp(b, dg)


def event_block(e, now):
    """One digest event -> an `event` block the Time lens places at its hour (time.js isEvent)."""
    people = e["who"][:4]
    summary = " · ".join(x for x in [("with " + ", ".join(people)) if people else "", e["where"]] if x)
    b = make("event", "mc-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", e["id"])[:60], e["title"], summary=summary,
             status="active", source_type="hermes-digest",
             detail={"start": e["start"], "end": e["end"], "all_day": e["all_day"], "location": e["where"] or None,
                     "people": people, "readonly": True},
             match={"tags": [], "text": e["title"]})
    b["when"] = {"at": e["start"], "kind": "event", "all_day": e["all_day"]}
    if e["end"]:
        b["when"]["end"] = e["end"]
    return b


def calendar_tile(dg, cap, now):
    """-> (tile, [event blocks])."""
    data = dg["data"]
    mine = _errors_for("calendar", data["errors"]) if data else []
    if not data or (not data["calendar"] and mine):
        if data and mine:
            return _stamp(_tile("calendar", "failed", mine[0], error=mine[0]), dg), []
        return _quiet_tile("calendar", dg, cap), []
    evs = data["calendar"]
    today = _dt.date.fromtimestamp(now)
    todays = [e for e in evs if _dt.date.fromtimestamp(e["_t"]) == today]
    ahead = [e for e in evs if e["_t"] >= now or (e["all_day"] and _dt.date.fromtimestamp(e["_t"]) >= today)]
    nxt = ahead[0] if ahead else None
    rows = [{"id": e["id"], "text": e["title"], "due": e["start"], "where": e["where"]} for e in (todays + [e for e in ahead if e not in todays])[:ROWS]]
    summary = f"{len(todays)} today" + (f" · next {nxt['title'][:60]}" if nxt else "")
    b = make("widget", "calendar", "calendar", summary=summary, status="active" if nxt else "idle",
             tags=["housekeeping"], updated=dg["as_of"], source_type="hermes-digest",
             detail={"widget": "calendar", "wired": True, "state": "ready", "readonly": True, "count": len(todays),
                     "count_label": "today", "overdue": 0,
                     "next": {"text": nxt["title"], "due": nxt["start"], "at": nxt["start"], "all_day": nxt["all_day"]} if nxt else None,
                     "empty": "nothing on the calendar this week", "items": rows},
             match={"tags": ["housekeeping"], "text": "calendar glance"})
    return _stamp(b, dg), [event_block(e, now) for e in evs]


def reply_blocks(dg, now=None):
    """Emails a person is waiting on -> cards (status needs-you), so they rank with everything else
    and can be among the three that need you (untangle.needs_you reads detail.needs)."""
    out = []
    for e in [e for e in visible((dg["data"] or {}).get("email") or [], now) if e["needs_reply"]][:REPLY_CARDS]:
        why = e["why"] or "is waiting on a reply"
        b = make("note", "mail-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", e["id"])[:60], e["subject"],
                 summary=f"{e['from']}: {why}", status="needs-you", tags=["email"], updated=e["received"],
                 source_type="hermes-digest",
                 detail={"from": e["from"], "subject": e["subject"], "received": e["received"], "why": why,
                         "snippet": e.get("snippet") or "", "readonly": True, "body": f"from {e['from']}. {why}.",
                         "needs": {"kind": "reply", "why": _fit(f"{e['from']} is waiting on a reply: {why}")}},
                 match={"tags": [], "text": e["subject"]}, decision=True)
        b["inbox"] = {"why": "needs a reply", "group": "decide", "verbs": []}
        if e["received"]:
            b["inbox"]["since"] = e["received"]
        out.append(b)
    return out


def blocks(now=None, want=("calendar", "email")):
    """-> the digest's blocks: the tiles asked for (calendar first, as the glance row has them),
    then the week's events and the reply-needed cards. Never raises."""
    now = now or util.now()
    try:
        dg = digest(now)
        cap = {"email": False, "calendar": False, "via": []} if dg["job"] else capability()
        tiles, events, cards = [], [], []
        if "calendar" in want:
            cal, events = calendar_tile(dg, cap, now)
            tiles.append(cal)
        if "email" in want:
            tiles.append(email_tile(dg, cap, now))
            cards = reply_blocks(dg, now)
        return tiles + events + cards
    except Exception as e:   # noqa: BLE001 - the tiles say so, they never guess and never sink the board
        note = f"couldn't read the digest: {type(e).__name__}"
        return [_tile(w, "failed", note, error=note) for w in ("calendar", "email") if w in want]


# ------------------------------------------------------------------ 5. the inbox as next actions
# An email row is something he opens, not something he ticks. Both mail paths (this digest and the
# direct glance in widgets._email_block) hand their rows to the same three functions: visible()
# drops what he put off or said isn't his, waiting_order() ranks the rest, mail_detail() is the tile.
# Nothing here reads or writes a mailbox.
HIDDEN_FILE = "mail_hidden.json"     # ONEGUY_HOME: {message id: {verb, until (epoch) | None, at}}
MAIL_VERBS = ("later", "not-mine", "talk")


def waited(span_s):
    """How long, in his words: '6 days', '1 day', '5 hours', 'under an hour'."""
    s = max(0.0, span_s or 0.0)
    d, h = int(s // 86400), int(s // 3600)
    if d:
        return f"{d} day" + ("" if d == 1 else "s")
    return (f"{h} hour" + ("" if h == 1 else "s")) if h else "under an hour"


def waiting_order(rows, now=None):
    """THE ordering rule for the inbox: what is waiting on him comes first.

    A row is {id, from, addr?, subject, received, needs_reply?, bulk?, …}. Each comes back with
    `person` (bool) and `waiting` (words) added, in this order:

      1. mail from people, OLDEST first (the one that has waited longest is at the top);
      2. everything automated, newest first, underneath.

    A row is automated ("not a person") when any of these is true:
      - the source said it is bulk (`bulk`: a List-Unsubscribe header, a Gmail promotions /
        social / updates / forums category, or the digest's own "list": true);
      - the sender has a machine's shape: no-reply, do-not-reply, notifications, notify, alerts,
        receipts, billing, invoices, orders, newsletter, news, digest, updates, mailer, bounce,
        postmaster, marketing, promo, automated (as a whole word in the address's name part, or
        in the display name when there is no address);
      - the subject has a receipt's shape: receipt, invoice, order confirmation / order #123,
        newsletter, daily/weekly digest, verification code, password reset.
    When the source itself says whether a person is waiting (the Hermes digest's `needs_reply`,
    true or false), that is the answer and the shapes above are not consulted.
    No model call, no network: only the fields the source already carried."""
    now = now or util.now()
    machine = re.compile(r"(?:^|[^a-z])(?:no[-_. ]?reply|do[-_. ]?not[-_. ]?reply|notifications?|notify|alerts?|receipts?"
                         r"|billing|invoices?|orders?|newsletters?|news|digest|updates?|mailer(?:-daemon)?|bounces?"
                         r"|postmaster|marketing|promo(?:tion)?s?|automated)(?:[^a-z]|$)")
    receipt = re.compile(r"\b(?:receipt|invoice|order confirmation|order #?\d+|newsletter|(?:daily|weekly) digest"
                         r"|verification code|password reset)\b", re.I)
    people, rest = [], []
    for r in rows or []:
        addr = str(r.get("addr") or "")
        who = (addr.split("@")[0] if addr else str(r.get("from") or "")).lower()
        person = r["needs_reply"] is True if isinstance(r.get("needs_reply"), bool) else not (
            r.get("bulk") is True or machine.search(who) or receipt.search(str(r.get("subject") or "")))
        t = util.parse_ts(r.get("received"))
        span = None if t is None else max(0.0, now - t)
        words = "" if span is None else ("waiting " + waited(span) if person else ago(span))
        (people if person else rest).append((t, dict(r, person=bool(person), waiting=words)))
    people.sort(key=lambda x: x[0] if x[0] is not None else now)          # oldest first; undated last
    rest.sort(key=lambda x: -(x[0] or 0))
    return [r for _t, r in people + rest]


def opener(rows, now=None):
    """The inbox's first line counts the people waiting, never the unread:
    '6 people are waiting on a reply, oldest 9 days'. `rows` = waiting_order()'s output."""
    now = now or util.now()
    people = [r for r in rows if r.get("person")]
    n = len({(r.get("addr") or r.get("from") or r.get("id") or "").lower() for r in people})
    if not n:
        return "nobody is waiting on a reply"
    ts = [t for t in (util.parse_ts(r.get("received")) for r in people) if t is not None]
    long = waited(now - min(ts)) if ts else ""
    if n == 1:
        return "1 person is waiting on a reply" + (f", {long}" if long else "")
    return f"{n} people are waiting on a reply" + (f", oldest {long}" if long else "")


def mail_summary(rows, now=None):
    top = rows[0] if rows and rows[0].get("person") else None
    return opener(rows, now) + (f" · {top['from']}: {top.get('why') or top['subject']}" if top else "")


def mail_detail(rows, now=None):
    """The part of the email tile's detail both paths share: the count is people waiting, `next`
    is the one who has waited longest, `items` are the rows a tap opens in place."""
    people = [r for r in rows if r.get("person")]
    top = people[0] if people else None
    items = [{"id": r["id"], "text": r["subject"], "subject": r["subject"], "from": r["from"],
              "why": r.get("why") or "", "snippet": r.get("snippet") or r.get("why") or "",
              "needs_reply": bool(r.get("needs_reply")), "received": r.get("received"),
              "person": r["person"], "waiting": r["waiting"]} for r in rows[:ROWS]]
    return {"count": len({(r.get("addr") or r.get("from") or r["id"]).lower() for r in people}),
            "count_label": "waiting on a reply", "mail": True,
            "opener": opener(rows, now), "people": len(people), "automated": len(rows) - len(people),
            "next": {"text": f"{top['from']}: {top.get('why') or top['subject']}", "from": top["from"],
                     "waiting": top["waiting"]} if top else None,
            "items": items}


def _hidden(now=None):
    """-> {message id: record} for what is hidden right now (a `later` past its time is not)."""
    now = now or util.now()
    d = util.read_json(util.oneguy(HIDDEN_FILE), None)
    return {k: v for k, v in (d if isinstance(d, dict) else {}).items()
            if isinstance(v, dict) and (v.get("until") is None or v["until"] > now)}


def visible(rows, now=None):
    """Rows minus the ones he put off until tomorrow or said aren't his."""
    gone = _hidden(now)
    return [r for r in rows or [] if str(r.get("id")) not in gone]


def tomorrow(now):
    """The next local midnight after `now`: when a `later` comes back."""
    t = _dt.datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (t + _dt.timedelta(days=1)).timestamp()


def mail_judge(body, now=None):
    """One verb on one open email: {op: later | not-mine | talk, id, from?, subject?}.
    later    -> hidden until tomorrow        (ledger: not-now, target "tomorrow")
    not-mine -> hidden for good              (ledger: not-mine)
    talk     -> stays; a thread opens on it  (ledger: talk)
    READ-ONLY toward the mailbox: this writes the ledger and ONEGUY_HOME/mail_hidden.json, nothing else.
    -> (result, http status)"""
    now = now or util.now()
    op = body.get("op")
    mid = util.clip(body.get("id"), 80)
    if op not in MAIL_VERBS or not mid:
        raise ValueError("need {op: later | not-mine | talk, id}")
    note = util.clip(": ".join(x for x in (util.clip(body.get("from"), 60), util.clip(body.get("subject"), 120)) if x), 200)
    kind = {"later": "not-now", "not-mine": "not-mine", "talk": "talk"}[op]
    ledger.append(kind, "mail:" + mid, target="tomorrow" if op == "later" else None, note=note or None)
    until = None
    if op != "talk":
        until = tomorrow(now) if op == "later" else None
        gone = _hidden(now)
        gone[mid] = {"verb": op, "until": until, "at": util.iso(now)}
        p = util.oneguy(HIDDEN_FILE)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(gone, f)
        os.replace(tmp, p)
        util.CACHE.clear("widgets")
    return {"ok": True, "op": op, "id": mid, "hidden": op != "talk", "until": util.iso(until) if until else None}, 200


# ------------------------------------------------------------------ 4. the yes and the not-now
def _run(argv, run=None):
    run = run or runner
    if run is None:
        return 1, "nothing here can run hermes commands"
    try:
        rc, out = run(list(argv))
        return int(rc or 0), str(out or "")
    except Exception as e:   # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def setup(run=None):
    """The user's yes: create the job (once), then run it once so data arrives in a minute.
    The yes is a judgment in the ledger. -> (result, http status)"""
    cap = capability()
    if not (cap["email"] or cap["calendar"]):
        return {"ok": False, "error": CANT["email"]}, 409
    job, created = find_job(), False
    if not job:
        rc, out = _run(create_command(), run)
        if rc != 0:
            return {"ok": False, "error": "couldn't create the job: " + util.safe_excerpt(out, 200)}, 502
        created = True
        util.CACHE.clear("jobs")
        job = find_job()
        if not job:
            m = re.search(r"Created job:\s*(\S+)", out)
            job = {"id": m.group(1)} if m else None
    ran = False
    if job:
        ran = _run(run_command(job["id"]), run)[0] == 0
    try:
        ledger.append("decide", "widget:email", target="yes", note="keep an eye on my email and calendar")
        prefs = _prefs()
        if prefs.pop("declined_at", None):
            _write_prefs(prefs)
    except OSError:
        pass
    util.CACHE.clear("jobs")
    util.CACHE.clear("widgets")
    return {"ok": True, "op": "setup", "created": created, "ran": ran, "job_id": (job or {}).get("id"),
            "say": WAITING if ran else "it's set up. the first read comes on the hour."}, 200


def _write_prefs(prefs):
    p = util.oneguy("mailcal.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(prefs, f)


def not_now():
    """The user's no: remembered, so the offer stops asking (the tile still says it can)."""
    prefs = _prefs()
    prefs["declined_at"] = util.iso(util.now())
    _write_prefs(prefs)
    util.CACHE.clear("widgets")
    return {"ok": True, "op": "not-now"}, 200


def action(body, run=None):
    """POST /api/board/mailcal {op: "setup" | "not-now"} for the offer, or {op: "later" | "not-mine" |
    "talk", id} for one email (mail_judge); GET-shaped status is status()."""
    op = (body or {}).get("op") if isinstance(body, dict) else None
    if op in MAIL_VERBS:
        return mail_judge(body)
    if op == "setup":
        return setup(run)
    if op == "not-now":
        return not_now()
    raise ValueError("op must be 'setup', 'not-now', 'later', 'not-mine' or 'talk'")


def status(now=None):
    """GET /api/board/mailcal: what this Hermes can read, whether the job exists, how fresh it is."""
    dg = digest(now)
    return {"can": capability(), "job": {"id": dg["job"]["id"], "name": JOB_NAME} if dg["job"] else None,
            "as_of": dg["as_of"], "stale": dg["stale"], "error": dg["error"],
            "declined": bool(_prefs().get("declined_at")), "schedule": SCHEDULE}
