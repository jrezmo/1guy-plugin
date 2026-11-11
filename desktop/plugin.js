/**
 * 1guy desktop half (v1.0.20): one companion surface on top of Hermes.
 *
 * What it registers:
 *   - a full page at /1guy + sidebar row "1guy" + ⌘K "Open 1guy": the canvas
 *   - a docked ticker under the conversation (⌘K "Show 1guy dock")
 *   - a small to-do pane (⌘K "Show 1guy to-dos") and the ::task-1guy{} transcript card
 *
 * How the canvas is shown. The canvas is a web page that ships INSIDE this plugin. The
 * backend hands it over as one self-contained document (GET /app/page via ctx.rest). The SDK's
 * <SandboxedFrame> (opaque origin, no reach into the app) navigates to a tiny loader document
 * as a data: URL; the loader asks for the canvas and writes it into itself. Because a sandboxed
 * frame cannot authenticate to the plugin backend, the page's API calls travel over a
 * postMessage bridge and this file answers them with ctx.rest:
 *
 *   page -> host  {type:'oneguy:boot'}                       -> {type:'oneguy:page', html}
 *   page -> host  {type:'oneguy:rest', id, method, path, body}
 *   host -> page  {type:'oneguy:rest-reply', id, ok, data | error, status}
 *   page -> host  {type:'oneguy:sample', on}                 -> reload the frame on / off the sample desk
 *   page -> host  {type:'oneguy:diag', entry}                -> POST /diag (errors, refusals, timings)
 *   page -> host  {type:'oneguy:local', data}                -> POST /local (remembered choices)
 *   page -> host  {type:'oneguy:hello'}                      -> {type:'oneguy:pane', version}
 *   page -> host  {type:'oneguy:open-session', session_id}   -> host.openSession
 *   page -> host  {type:'oneguy:open-url', url}              -> ctx.os.openExternal (http/https)
 *
 * Only messages whose source is the mounted frame's own window are answered.
 *
 * How an item's thread runs ("one item = one Hermes session"). The backend queues turn jobs;
 * while a canvas is on screen this file claims them (GET /relay/next) and runs them on the
 * app's own gateway connection: session.create (titled), prompt.submit, session.interrupt.
 * Streamed message.delta / tool.start / message.complete events are pushed back
 * (POST /relay/push) so the canvas streams the reply in place. Two-id rule: prompt.submit
 * takes the RUNTIME session id; everything stored or opened uses the STORED id.
 */
import * as sdk from '@hermes/plugin-sdk'
import {
  cn,
  host,
  PALETTE_AREA,
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  useQuery
} from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { Fragment, jsx, jsxs } from 'react/jsx-runtime'

const ID = '1guy'
const VERSION = '1.0.20'
const PAGE_PATH = '/1guy'
const INTENTS = ['stack', 'tab', 'window', 'main', 'in-place']

// ---- i18n ----------------------------------------------------------------
const STRINGS = {
  en: {
    openOneGuy: 'Open 1guy',
    showDock: 'Show 1guy dock',
    showTasks: 'Show 1guy to-dos',
    paneNeeded: 'Update the desktop app to reveal plugin panes',
    openFailed: 'Could not open that session from this window',
    urlFailed: 'Could not open {{url}}',
    loading: 'setting the table…',
    downTitle: '1guy is not answering',
    downBody:
      'The plugin backend did not respond. Check that 1guy is enabled under Capabilities → Plugins on this Hermes, then retry.',
    retry: 'reload',
    stuckTitle: '1guy did not finish loading',
    stuckBody:
      'The board page was sent to its frame but never started. Reload to try again. If it keeps happening, update Hermes Desktop: 1guy needs version 0.23 or later.',
    tasksTitle: 'to-do',
    tasksEmpty: 'nothing waiting',
    tasksError: 'the list is not answering',
    taskOpen: 'open in 1guy',
    taskCardTitle: 'to-do item',
    dockTitle: 'for you',
    dockEmpty: 'quiet board, nothing moving',
    dockDown: 'holding the last read',
    appTooOld: '1guy needs a newer Hermes Desktop (0.23 or later). Update the app and reopen this page.'
  }
}
let CTX = null
function t(key, vars) {
  let s = (CTX && CTX.i18n && CTX.i18n.t && CTX.i18n.t(key)) || STRINGS.en[key] || key
  if (s === key) s = STRINGS.en[key] || key
  if (vars) for (const k of Object.keys(vars)) s = s.split('{{' + k + '}}').join(String(vars[k]))
  return s
}

// ---- doors -----------------------------------------------------------------
function openSession(id, intent) {
  const fail = () => host.notify({ kind: 'error', message: t('openFailed') })
  try {
    const r = host.openSession(String(id), { intent: INTENTS.includes(intent) ? intent : 'stack' })
    if (r && typeof r.catch === 'function') r.catch(fail)
  } catch (e) {
    fail()
  }
}

function openUrl(url) {
  let u
  try {
    u = new URL(String(url))
  } catch (e) {
    return
  }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return
  const fail = () => host.notify({ kind: 'error', message: t('urlFailed', { url: u.href }) })
  if (CTX && CTX.os && typeof CTX.os.openExternal === 'function') {
    Promise.resolve(CTX.os.openExternal(u.href)).catch(fail)
  } else {
    fail()
  }
}

function reveal(paneId) {
  if (typeof host.revealPane === 'function') host.revealPane(paneId)
  else host.notify({ kind: 'info', message: t('paneNeeded') })
}

// ---- the relay: agent turns on the app's own gateway connection --------------
// Module state, one runner per plugin load. It only polls while a canvas is mounted.
const relay = {
  mounted: 0,
  polling: false,
  runtimeOf: new Map(), // stored session id -> runtime session id
  turnOf: new Map(), // session id (runtime and stored) -> the turn streaming there
  flushing: false
}

function relayPush(turn, event, data) {
  turn.out.push({ event, data: data || {} })
  relayFlush(turn)
}

async function relayFlush(turn) {
  if (turn.flushing || !turn.out.length) return
  turn.flushing = true
  try {
    while (turn.out.length) {
      const events = turn.out.splice(0, turn.out.length)
      await CTX.rest('/relay/push', { method: 'POST', body: { id: turn.job, events } })
    }
  } catch (e) {
    // the backend went away mid-turn; nothing to deliver to
  } finally {
    turn.flushing = false
  }
}

async function runtimeFor(stored) {
  const known = relay.runtimeOf.get(stored)
  if (known) return known
  const r = await host.request('session.resume', { session_id: stored, omit_messages: true })
  const runtime = (r && r.session_id) || stored
  relay.runtimeOf.set(stored, runtime)
  return runtime
}

async function relayRun(job) {
  const turn = { job: job.id, out: [], text: '', flushing: false }
  try {
    if (job.kind === 'create') {
      const r = await host.request('session.create', { title: job.title || '1guy' })
      const stored = r.stored_session_id || r.session_id
      relay.runtimeOf.set(stored, r.session_id)
      relayPush(turn, 'created', { session_id: stored, title: job.title })
    } else if (job.kind === 'turn') {
      const runtime = await runtimeFor(job.session_id)
      // events are stamped with the runtime id; the stored id is registered too so a build
      // that stamps the durable id still finds its turn
      turn.ids = [runtime, job.session_id]
      for (const id of turn.ids) relay.turnOf.set(id, turn)
      await host.request('prompt.submit', { session_id: runtime, text: job.message })
      relayPush(turn, 'run.started', { run_id: job.id })
    } else if (job.kind === 'stop') {
      const runtime = relay.runtimeOf.get(job.session_id)
      if (runtime) await host.request('session.interrupt', { session_id: runtime })
    }
  } catch (e) {
    relayEnd(turn)
    const message = String((e && e.message) || e || 'Hermes refused that')
    report('gateway', job.kind + ': ' + message)
    relayPush(turn, job.kind === 'create' ? 'failed' : 'error', { message })
    if (job.kind === 'turn') relayPush(turn, 'done', {})
  }
}

function relayEnd(turn) {
  for (const id of turn.ids || []) if (relay.turnOf.get(id) === turn) relay.turnOf.delete(id)
}

function relayOnEvent(type, ev) {
  const turn = ev && relay.turnOf.get(ev.session_id)
  if (!turn) return
  const p = ev.payload || {}
  if (type === 'message.delta') {
    if (typeof p.text === 'string' && p.text) {
      turn.text += p.text
      relayPush(turn, 'assistant.delta', { delta: p.text })
    }
  } else if (type === 'tool.start') {
    relayPush(turn, 'tool.started', { tool_name: p.name || 'tool' })
  } else if (type === 'message.complete') {
    relayEnd(turn)
    if (p.status === 'error') {
      relayPush(turn, 'error', { message: String(p.error || p.warning || 'the turn failed') })
      relayPush(turn, 'done', {})
      return
    }
    const interrupted = p.status === 'interrupted' || p.status === 'cancelled'
    relayPush(turn, 'assistant.completed', { content: p.text || turn.text, completed: !interrupted, interrupted })
    relayPush(turn, interrupted ? 'run.cancelled' : 'run.completed', {})
    relayPush(turn, 'done', {})
  } else if (type === 'error') {
    relayEnd(turn)
    relayPush(turn, 'error', { message: String(p.message || 'the turn failed') })
    relayPush(turn, 'done', {})
  }
}

async function relayPoll() {
  if (relay.polling) return
  relay.polling = true
  try {
    while (relay.mounted > 0 || relay.turnOf.size > 0) {
      let res
      try {
        res = await CTX.rest('/relay/next?wait=20', { method: 'GET', timeoutMs: 40000 })
      } catch (e) {
        await new Promise(done => CTX.setTimeout(done, 5000))
        continue
      }
      for (const job of (res && res.jobs) || []) relayRun(job)
    }
  } finally {
    relay.polling = false
  }
}

// ---- the canvas (sandboxed frame + bridge) ------------------------------------
// The frame navigates to a ~1.5 KB loader document (a data: URL). The loader says
// 'oneguy:boot', we post it the canvas document, it writes that into itself, and the canvas
// says 'oneguy:hello' once its code is running. Keeping the big document out of the URL avoids
// any URL-length limit; the two handshakes are what tell us a load really worked.
let PAGE = null // { version, boot, html }, fetched once per plugin load
const BOOT_WAIT_MS = 8000 // loader must report in
const START_WAIT_MS = 15000 // then the canvas must

// ---- remote eyes ---------------------------------------------------------------
// What goes wrong in the real app is otherwise invisible from the backend's side. The canvas
// (bridge) and this file report errors, refused requests, failed calls and load timings to
// POST /diag; the backend keeps the newest few hundred lines in its data folder and GET /diag
// reads them back. Batched, capped, and never reports its own failures.
const diagQ = []
let diagTimer = null
function report(kind, message, ms, where) {
  if (!CTX || diagQ.length >= 40) return
  diagQ.push({ kind, message: String(message || '').slice(0, 300), ms, where })
  if (diagTimer) return
  diagTimer = CTX.setTimeout(() => {
    diagTimer = null
    const entries = diagQ.splice(0, diagQ.length)
    const meta = { version: VERSION, canvas: (PAGE && PAGE.version) || '', ua: String(navigator.userAgent || '').slice(0, 120) }
    CTX.rest('/diag', { method: 'POST', body: { entries, meta } }).catch(() => {})
  }, 1500)
}

async function loadPage(force) {
  if (PAGE && !force) return PAGE
  const t0 = Date.now()
  const r = await CTX.rest('/app/page', { method: 'GET', timeoutMs: 20000 })
  if (!r || typeof r.html !== 'string' || typeof r.boot !== 'string') throw new Error('no page')
  report('t:page_fetch', 'canvas document fetched (' + Math.round(r.html.length / 1024) + ' KB)', Date.now() - t0)
  PAGE = { version: r.version || '', boot: r.boot, html: r.html, local: r.local || {} }
  return PAGE
}

// ctx.rest rejects with a plain Error whose message ends in "<status>: <body>"; there is no
// status field. Recover both so the canvas sees the backend's real answer (a 400 with its
// reason, a 404), not a blanket failure.
function restError(e) {
  const m = /(?:^|Error:\s*)(\d{3}):\s*([\s\S]*)$/.exec(String((e && e.message) || e))
  if (!m) return { status: 502, error: String((e && e.message) || e) }
  let error = m[2]
  try {
    const body = JSON.parse(m[2])
    error = (body && (body.error || body.detail)) || m[2]
  } catch (err) {
    // not JSON: keep the text
  }
  return { status: Number(m[1]), error: String(error) }
}

async function answerRest(win, d) {
  const reply = { type: 'oneguy:rest-reply', id: d.id, path: String(d.path || '').slice(0, 80) }
  const path = String(d.path || '')
  if (!path.startsWith('/') || path.split(/[?#]/, 1)[0].split('/').includes('..')) {
    win.postMessage({ ...reply, ok: false, status: 400, error: 'bad path' }, '*')
    return
  }
  try {
    const method = d.method === 'POST' ? 'POST' : 'GET'
    const data = await CTX.rest(path, { method, body: method === 'POST' ? d.body || {} : undefined, timeoutMs: 60000 })
    win.postMessage({ ...reply, ok: true, data }, '*')
  } catch (e) {
    const failed = restError(e)
    win.postMessage({ ...reply, ok: false, status: failed.status, error: failed.error }, '*')
  }
}

function Notice({ title, body, onRetry }) {
  return jsx('div', {
    className: 'flex h-full w-full items-center justify-center p-6',
    children: jsxs('div', {
      className: 'max-w-sm rounded-lg border border-(--ui-stroke-secondary) p-4 text-sm',
      children: [
        jsx('div', { className: 'mb-1 font-medium text-(--ui-text-secondary)', children: title }),
        jsx('div', { className: 'mb-3 text-xs text-(--ui-text-tertiary)', children: body }),
        jsx('button', {
          type: 'button',
          className: cn(
            'rounded border border-(--ui-stroke-secondary) px-2 py-0.5 text-xs text-(--ui-text-secondary)',
            'hover:bg-(--chrome-action-hover)'
          ),
          onClick: onRetry,
          children: t('retry')
        })
      ]
    })
  })
}

function Canvas() {
  // phase: 'fetch' (asking the backend) -> 'frame' (loader navigating) -> 'start' (canvas
  // written, waiting for it to run) -> 'ok'. Failures: 'down' (backend), 'stuck' (frame).
  const [page, setPage] = useState(PAGE)
  const [phase, setPhase] = useState(PAGE ? 'frame' : 'fetch')
  const [attempt, setAttempt] = useState(0)
  const frameRef = useRef(null)
  const live = useRef({ phase: PAGE ? 'frame' : 'fetch', page: PAGE, timer: null, since: Date.now() })

  const go = (next, ms) => {
    const L = live.current
    if (typeof L.timer === 'function') L.timer()
    L.timer = null
    if (next === 'stuck') report('load', 'frame did not finish loading (was: ' + L.phase + ')')
    if (next === 'down') report('load', 'backend did not hand over the canvas document')
    if (next === 'start') report('t:loader', 'loader document reported in', Date.now() - L.since)
    if (next === 'ok' && L.phase === 'start') report('t:canvas_start', 'canvas code running', Date.now() - L.since)
    L.phase = next
    L.since = Date.now()
    setPhase(next)
    // a phase that should pass on its own gets a deadline; missing it is an honest failure
    if (ms) L.timer = CTX.setTimeout(() => L.phase === next && go('stuck'), ms)
  }

  const load = force => {
    go('fetch')
    loadPage(force).then(
      p => {
        live.current.page = p
        setPage(p)
        setAttempt(n => n + 1) // a new document for the frame, even on a retry
        go('frame', BOOT_WAIT_MS)
      },
      () => go('down')
    )
  }

  useEffect(() => {
    if (live.current.page) go('frame', BOOT_WAIT_MS)
    else load(false)
    relay.mounted += 1
    relayPoll()
    const onMessage = ev => {
      const win = frameRef.current && frameRef.current.contentWindow
      const d = ev.data
      const L = live.current
      if (!win || ev.source !== win || !d || typeof d.type !== 'string') return
      if (d.type === 'oneguy:boot' && L.page) {
        win.postMessage({ type: 'oneguy:page', html: L.page.html, local: L.page.local, sample: L.sample === true }, '*')
        go('start', START_WAIT_MS)
      } else if (d.type === 'oneguy:hello') {
        win.postMessage({ type: 'oneguy:pane', version: VERSION }, '*')
        go('ok')
      } else if (d.type === 'oneguy:rest') {
        if (L.phase !== 'ok') go('ok') // it is clearly running
        answerRest(win, d)
      } else if (d.type === 'oneguy:sample') {
        // the sample desk: reload the frame with (or without) the flag; nothing else changes
        L.sample = d.on === true
        setAttempt(n => n + 1)
        go('frame', BOOT_WAIT_MS)
      } else if (d.type === 'oneguy:diag' && d.entry && typeof d.entry === 'object') {
        report(String(d.entry.kind || 'error').slice(0, 24), d.entry.message, d.entry.ms, d.entry.where)
      } else if (d.type === 'oneguy:local' && d.data && typeof d.data === 'object') {
        L.page.local = d.data // so a reload in this window starts from it too
        CTX.rest('/local', { method: 'POST', body: { data: d.data } }).catch(() => {})
      } else if (d.type === 'oneguy:open-session' && d.session_id) openSession(d.session_id, d.intent)
      else if (d.type === 'oneguy:open-url' && d.url) openUrl(d.url)
    }
    const off = CTX.addEventListener(window, 'message', onMessage)
    return () => {
      relay.mounted -= 1
      if (typeof live.current.timer === 'function') live.current.timer()
      if (typeof off === 'function') off()
    }
  }, [])

  if (phase === 'down') return jsx(Notice, { title: t('downTitle'), body: t('downBody'), onRetry: () => load(true) })
  if (phase === 'stuck') return jsx(Notice, { title: t('stuckTitle'), body: t('stuckBody'), onRetry: () => load(true) })
  if (phase === 'fetch' || !page) {
    return jsx('div', {
      className: 'flex h-full w-full items-center justify-center text-xs text-(--ui-text-quaternary)',
      children: t('loading')
    })
  }
  if (!sdk.SandboxedFrame) {
    return jsx('div', {
      className: 'flex h-full w-full items-center justify-center p-6 text-xs text-(--ui-text-tertiary)',
      children: t('appTooOld')
    })
  }
  // the attempt number makes each try a different URL, so a retry really reloads the frame
  const src = 'data:text/html;charset=utf-8,' + encodeURIComponent(page.boot + '<!-- ' + attempt + ' -->')
  return jsx(sdk.SandboxedFrame, {
    ref: frameRef,
    src,
    title: '1guy',
    sandbox: 'allow-scripts allow-forms',
    className: 'h-full w-full border-0'
  })
}

// ---- to-do quick view ---------------------------------------------------------
const TASKS_KEY = ['1guy', 'tasks']

async function fetchTasks() {
  const data = await CTX.rest('/tasks', { method: 'GET' })
  const out = []
  for (const s of (data && data.sections) || []) for (const it of s.items || []) if (it && !it.done) out.push(it)
  return out
}

function TasksPane() {
  const { data, isLoading, isError } = useQuery({ queryKey: TASKS_KEY, queryFn: fetchTasks, refetchInterval: 30000, retry: 1 })
  const items = data || []
  const note = isLoading ? '…' : isError ? t('tasksError') : items.length === 0 ? t('tasksEmpty') : null
  return jsxs('div', {
    className: 'flex h-full flex-col gap-2 p-3 text-sm select-text',
    children: [
      jsx('div', { className: 'font-medium text-(--ui-text-secondary)', children: t('tasksTitle') }),
      note && jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: note }),
      !note &&
        jsx('div', {
          className: 'flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto',
          children: items.slice(0, 30).map(item =>
            jsx(
              'button',
              {
                type: 'button',
                className: cn(
                  'rounded border border-(--ui-stroke-secondary) px-2 py-1 text-left text-xs text-(--ui-text-secondary)',
                  'hover:bg-(--chrome-action-hover)'
                ),
                onClick: () => host.navigate(PAGE_PATH),
                children: String(item.text || '')
              },
              item.id || String(item.text)
            )
          )
        })
    ]
  })
}

// ---- transcript directive card (::task-1guy{text="…"}) -----------------------
function TaskCard({ attrs }) {
  const text = attrs && attrs.text ? String(attrs.text).slice(0, 140) : t('taskCardTitle')
  return jsxs('div', {
    className: 'my-1 inline-flex flex-col gap-1 rounded-lg border border-(--ui-stroke-secondary) p-2 text-sm',
    children: [
      jsx('div', { className: 'text-(--ui-text-secondary) select-text', children: text }),
      jsx('button', {
        type: 'button',
        className: cn(
          'self-start rounded border border-(--ui-stroke-secondary) px-2 py-0.5 text-xs text-(--ui-text-tertiary)',
          'hover:bg-(--chrome-action-hover)'
        ),
        onClick: () => host.navigate(PAGE_PATH),
        children: t('taskOpen')
      })
    ]
  })
}

// ---- docked ticker ------------------------------------------------------------
// Top row: up to 3 "needs you" chips. Below: a calm marquee of what is moving, in plain
// words (the backend strips paths, ids and tags). Poll failures keep the last good read.
const TICKER_KEY = ['1guy', 'ticker']
// Plain CSS for what the app's stylesheet does not ship as utility classes (a disk plugin's
// class names are not compiled by the app). Theme variables only; no colors of our own.
const DOCK_CSS =
  '.ogdock-chip{max-width:34ch;line-height:1.125rem}' +
  '.ogdock-chip:hover,.ogdock-item:hover{color:var(--ui-accent);border-color:var(--ui-accent)}' +
  '.ogdock-dot{flex:none}' +
  '@keyframes ogdock-scroll{from{transform:translateX(0)}to{transform:translateX(-50%)}}' +
  '.ogdock-track{display:inline-flex;white-space:nowrap;will-change:transform;animation:ogdock-scroll var(--ogdock-dur,70s) linear infinite}' +
  '.ogdock:hover .ogdock-track,.ogdock:focus-within .ogdock-track{animation-play-state:paused}' +
  '@media (prefers-reduced-motion: reduce){.ogdock-track{animation:none}}'

async function fetchTicker() {
  const data = await CTX.rest('/ticker', { method: 'GET' })
  return data && typeof data === 'object' ? data : { ok: false, chips: [], items: [] }
}

function dockOpen(it) {
  if (it && it.session_id) openSession(it.session_id, 'stack')
  else host.navigate(PAGE_PATH)
}

function DockTicker() {
  const { data, isLoading } = useQuery({ queryKey: TICKER_KEY, queryFn: fetchTicker, refetchInterval: 20000, retry: 1 })
  const chips = (data && Array.isArray(data.chips) && data.chips) || []
  const items = (data && Array.isArray(data.items) && data.items) || []
  const loop = items.length ? [...items, ...items] : []
  const quiet = 'text-[0.6875rem] text-(--ui-text-quaternary)'
  return jsxs('div', {
    className: 'ogdock flex h-full w-full flex-col select-text',
    children: [
      jsx('style', { children: DOCK_CSS }),
      jsxs('div', {
        className: 'flex items-center gap-2 px-3 pt-2',
        children: [
          jsx('span', { className: 'ogdock-dot h-2 w-2 rounded-full bg-(--ui-accent)', 'aria-hidden': true }),
          jsx('span', { className: 'text-[0.6875rem] font-semibold text-(--ui-text-tertiary)', children: t('dockTitle') }),
          chips.length === 0 && !isLoading && jsx('span', { className: quiet, children: t('dockEmpty') }),
          chips.map((c, i) =>
            jsx(
              'button',
              {
                type: 'button',
                title: c.text,
                className: cn(
                  'ogdock-chip truncate rounded-lg border px-2 text-[0.72rem]',
                  i === 0
                    ? 'border-(--ui-accent) text-(--ui-accent)'
                    : 'border-(--ui-stroke-secondary) text-(--ui-text-secondary)'
                ),
                onClick: () => dockOpen(c),
                children: c.text
              },
              c.id || i
            )
          ),
          jsx('span', { className: 'flex-1' }),
          data && data.stale && jsx('span', { className: quiet, children: t('dockDown') })
        ]
      }),
      jsxs('div', {
        className: 'relative mt-1.5 flex min-h-0 flex-1 items-center overflow-hidden border-t border-(--ui-stroke-secondary)',
        children: [
          isLoading && jsx('span', { className: cn(quiet, 'px-5'), children: '…' }),
          !isLoading && loop.length === 0 && jsx('span', { className: cn(quiet, 'px-5'), children: t('dockEmpty') }),
          !isLoading &&
            loop.length > 0 &&
            jsx('div', {
              className: 'ogdock-track h-full items-center',
              style: { '--ogdock-dur': Math.max(30, loop.length * 7) + 's' },
              children: loop.map((it, i) =>
                jsx(
                  'button',
                  {
                    type: 'button',
                    className:
                      'ogdock-item inline-flex h-full items-center gap-2 border-0 bg-transparent px-5 text-[0.78rem] text-(--ui-text-secondary)',
                    onClick: () => dockOpen(it),
                    children: jsxs(Fragment, {
                      children: [
                        jsx('span', {
                          className: cn(
                            'font-mono text-[0.6rem] uppercase tracking-widest',
                            it.tone === 'you' ? 'text-(--ui-accent)' : 'text-(--ui-text-quaternary)'
                          ),
                          children: (it.tone === 'bad' ? '⚠ ' : '') + String(it.src || '')
                        }),
                        jsx('span', { children: String(it.text || '') })
                      ]
                    })
                  },
                  (it.id || 'i') + ':' + i
                )
              )
            })
        ]
      })
    ]
  })
}

// ---- plugin ----------------------------------------------------------------
export default {
  id: ID,
  name: '1guy',
  register(ctx) {
    CTX = ctx
    if (ctx.i18n && typeof ctx.i18n.register === 'function') ctx.i18n.register(STRINGS)

    for (const type of ['message.delta', 'tool.start', 'message.complete', 'error']) {
      ctx.onEvent(type, ev => relayOnEvent(type, ev))
    }
    // the backend nudges when a turn is queued while no canvas is polling (e.g. just closed)
    ctx.onEvent('plugin.1guy.relay.pending', () => {
      relay.mounted += 1
      relayPoll()
      ctx.setTimeout(() => {
        relay.mounted -= 1
      }, 30000)
    })
    ctx.onDispose(() => {
      relay.mounted = 0
      relay.turnOf.clear()
    })

    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, title: '1guy', data: { path: PAGE_PATH }, render: () => jsx(Canvas, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PAGE_PATH, label: '1guy', codicon: 'circle-large-filled' } },
      {
        id: 'tasks-pane',
        area: PANES_AREA,
        title: '1guy to-do',
        // its own zone beside the conversation: no `placement`, so it never stacks as a
        // background tab behind whatever else lives on the right edge
        data: { dock: { pane: 'workspace', pos: 'right' }, width: '320px' },
        render: () => jsx(TasksPane, {})
      },
      {
        id: 'dock',
        area: PANES_AREA,
        title: '1guy dock',
        data: { placement: 'bottom', dock: { pane: 'workspace', pos: 'bottom' }, height: '120px' },
        render: () => jsx(DockTicker, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: '1guy.open',
          label: t('openOneGuy'),
          keywords: ['1guy', 'one guy', 'home', 'board'],
          run: () => host.navigate(PAGE_PATH)
        }
      },
      {
        id: 'show-dock',
        area: PALETTE_AREA,
        data: {
          id: '1guy.show-dock',
          label: t('showDock'),
          keywords: ['1guy', 'dock', 'ticker'],
          run: () => reveal('1guy:dock')
        }
      },
      {
        id: 'show-tasks',
        area: PALETTE_AREA,
        data: {
          id: '1guy.show-tasks',
          label: t('showTasks'),
          keywords: ['1guy', 'tasks', 'tasklist', 'todo'],
          run: () => reveal('1guy:tasks-pane')
        }
      }
    ])

    if (sdk.TRANSCRIPT_DIRECTIVE_AREA) {
      ctx.register({
        id: 'task-card',
        area: sdk.TRANSCRIPT_DIRECTIVE_AREA,
        data: { name: 'task-1guy', render: ({ attrs }) => jsx(TaskCard, { attrs }) }
      })
    }
  }
}
