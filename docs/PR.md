# Add `1guy` to the plugin catalog

> PR description for the entry `plugin-catalog/1guy.yaml` in `NousResearch/hermes-agent`.
> `<SHA>` is filled in by `scripts/make_public.sh`, which writes the ready-to-paste copies to
> `.release/PR.md` and `.release/1guy.yaml`. Submitted by the repository owner (rule 5).

## What it is

1guy is an early prototype of a bigger idea: the next interface should hold your
open work across sessions, not hand it off. The core loop works (opener, sorting
into piles, handing off a card). The edges are rough, built fast for this hackathon.

**Hermes does the work. 1guy brings it back to you.** Running Hermes well means many sessions,
scheduled jobs and helpers, each one a chat you have to go back into. 1guy is one desk over the
whole Hermes home: every session, job and to-do is a card, every card is its own titled Hermes
session, and one companion on top tells you the few things that need you. Chat apps put artifacts
inside conversations; 1guy puts the conversations inside your work.

```
hermes plugins install 1guy
```

Then switch it on under **Capabilities → Plugins** in Hermes Desktop and open **1guy** in the
sidebar. Nothing to configure, no API key. Repository: https://github.com/jrezmo/1guy-plugin

### The first 60 seconds

Which door opens is picked from what the Hermes home holds, never asked.

- **A lived-in Hermes.** He opens with numbers he counted: *"i went through your hermes. 20 chats
  in two weeks, 5 scheduled jobs. three need you."* The three are real: a chat that ended on a
  question to the user, a job whose last run failed, a thread left mid-task. He then proposes
  piles from the history as cards that preview before yes (hold the button and the cards slide
  into the pile; tap and they stay, one ledger line each; undo puts them back).
- **A brand-new Hermes.** He asks *"what are you in the middle of?"* Each typed line is a card at
  once and a to-do in `tasklist.md`. A clearly marked *sample desk* shows a made-up world,
  read-only, and leaves with one tap; nothing on the real desk is ever invented.

After that, on either door: every card has one button; a line about some cards frames them with
what he is doing; anything that changed while the user was away wears an outline until they look;
a box dragged round several cards asks one question about all of them; a pile can be written up
as a brief.

It adds a **1guy** page (sidebar row, ⌘K *Open 1guy*) with four lenses (today, board, time,
inbox), a docked ticker, and a small to-do pane.

Screenshots (pinned to the commit):

- `https://raw.githubusercontent.com/jrezmo/1guy-plugin/<SHA>/docs/screenshots/board.png`
- `https://raw.githubusercontent.com/jrezmo/1guy-plugin/<SHA>/docs/screenshots/first-run.png`
- `https://raw.githubusercontent.com/jrezmo/1guy-plugin/<SHA>/docs/screenshots/thread.png`
- `https://raw.githubusercontent.com/jrezmo/1guy-plugin/<SHA>/docs/screenshots/time-lens.png`

## Hermes surfaces it uses

| Surface | Use |
|---|---|
| Desktop plugin SDK (`desktop/plugin.js`) | one route + sidebar row, two panes, palette commands, a transcript directive; `SandboxedFrame`; `ctx.rest`; `ctx.onEvent`; `host.request` (`session.create`, `session.resume`, `prompt.submit`, `session.interrupt`); `host.openSession`; `ctx.os.openExternal` |
| Dashboard backend (`dashboard/plugin_api.py`) | routes under `/api/plugins/1guy/` |
| Plugin LLM access (`ctx.llm.complete`) | short background calls for card headlines and to route a typed line |
| Plugin data dir (`plugin_data_dir("1guy")`) | all of its own state |
| Plugin events (`broadcast_plugin_event`) | one nudge event, `plugin.1guy.relay.pending` |

`register(ctx)` registers no tools, hooks, middleware or commands; it only hands `ctx.llm` to
the backend. `capabilities` is therefore empty, and no capability ids are requested.

## Disclosures (rule 13)

- **Reads outside its own data (read-only):** `<HERMES_HOME>/state.db` (opened `mode=ro`),
  `cron/jobs.json`, `cron/output/`, `cache/delegation/live/`, folder names under `skills/`, and
  the server names under `mcp_servers:` in `config.yaml`.
- **Reads and writes:** `<HERMES_HOME>/tasklist.md`, a plain markdown checklist (created if
  missing). It edits single lines, writes a date onto a line when its card is dropped on a day,
  and appends a short anchor such as `^k7f2` when that item gets a thread or a date.
- **Its own state:** `<HERMES_HOME>/plugin-data/1guy/` (item → session map, placements, a
  log of the user's gestures, cached headlines, small remembered choices, briefs the user asked
  for, and a capped
  self-report log of client errors and load timings that `GET /diag` reads back).
- **Model use:** short background calls on the user's active model through `ctx.llm` (card
  headlines, naming the piles proposed from history, writing a brief on request, routing a
  typed line), carrying card
  titles, summaries and short excerpts of recent sessions and job output. They spend the
  user's tokens. `ONEGUY_HEADLINES=off` turns the headline calls off.
- **Sessions:** talking to a card creates an ordinary Hermes session through the desktop app's
  own gateway connection and sends the message plus that card's context into it. Turns run on
  the user's normal agent with their normal tools and approvals; nothing is auto-approved.
- **Shell:** three commands, each only on an explicit user action. `hermes cron run <job id>`
  when the user presses *run now* on a job card and confirms. `hermes cron create '0 7-22 * * *'
  '<read-only digest prompt>' --name 1guy-mail-cal --deliver local` and then `hermes cron run
  <that job>` when the user says yes to the email/calendar offer. The route refuses any other
  argv.
- **Email and calendar:** the plugin reads no mail and no calendar and holds no account. It
  checks whether this Hermes has an email/calendar ability by NAME only (folder names under
  `skills/`, keys under `mcp_servers:` in `config.yaml`; no values, no skill bodies), offers the
  job above, and afterwards reads that job's last output file under `cron/output/`.
- **Network:** none of its own. No telemetry. The canvas runs in a sandboxed frame under a
  Content-Security-Policy with `connect-src 'none'`; its images are embedded and its sounds
  synthesized. The plugin build has no direct mail or calendar reader (the vendored ones are
  disabled in `og_core/board.py` and stubbed in `og_core/vendor/app/board/`).
- **Credentials:** none read, none stored. No `.env`, `auth.json` or token files are touched.
- **Background work:** short-lived threads for headline refresh; *run now* starts
  `hermes cron run <id>` detached (it ends on its own); the `1guy-mail-cal` job, if the user
  accepts it, recurs hourly 07:00–22:00 until removed with `hermes cron`. No daemon, no
  self-update.

## Things a reviewer will want to know

- **Earlier versions used the id `oneguy`.** `scripts/migrate_from_oneguy.sh` (dry run by
  default) moves such an install; the backend also moves its own data folder once.
- **A generated bundle is committed.** `og_core/static/canvas.html` is the canvas as one
  document: an unminified esbuild bundle of the readable sources in
  `og_core/vendor/app/home/`, with the stylesheets and six small webp illustrations
  inlined. `scripts/vendor_canvas.py <ref>` rebuilds it; nothing is fetched at runtime. The
  six `.webp` files under `og_core/vendor/app/lab/places/` are plain images.
- **Why a bundle and a message bridge.** A sandboxed frame cannot authenticate to
  `/api/plugins/<id>/`, so the page is delivered through `ctx.rest`, the frame navigates to a
  small loader document (a `data:` URL), and the page's API calls travel over `postMessage` to
  the desktop half, which answers with `ctx.rest`. The frame has an opaque origin and no reach
  into the app.
- **Vendored modules are adapted, not Hermes core.** `og_core/board.py` and `talk.py`
  rebind functions on the plugin's *own* vendored modules (to point them at this install and
  at `ctx.llm`). No Hermes module, class or table is patched (rule 9).
- **The backend adds its vendored folder to `sys.path`** so those modules can import each
  other by bare name (`board`, `threads`, `router`, `feed`, `talk_resolve`). It refuses to load
  if a different `board` module is already imported.
- **Desktop half:** imports only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`; no
  `window.hermesDesktop`, no app-markup selectors, no prototype patching, theme variables only.
  It renders one `<style>` element inside its own dock pane for a marquee keyframe.
- **Requires** Hermes `>=0.23.0` (uses `SandboxedFrame`).

## Validation

```
hermes plugins validate . --install-deps      # zero warnings, security scan: safe
.venv/bin/pytest -q                           # backend, relay, first run, browser run of plugin.js
tests/stranger_install.sh                     # real install into a scratch Hermes home, end to end
```

## Not in this version

Email, calendar and news cards, voice input, and pending approvals as a source.
