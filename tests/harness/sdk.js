// Harness stand-in for @hermes/plugin-sdk. It records host calls in window.__calls and plays
// a tiny fake gateway (session.create / prompt.submit / session.interrupt + streamed events)
// so the REAL desktop/plugin.js can be driven end to end in a plain browser.
window.__calls = []
const rec = (name, args) => window.__calls.push({ name, args: JSON.parse(JSON.stringify(args ?? null)) })
const listeners = new Map()
const emit = (type, ev) => (listeners.get(type) || []).forEach(fn => fn(ev))
let n = 0
const REPLY = ['Sure. ', 'Here is ', 'the plan ', 'for that item.']

export const host = {
  openSession: async (id, opts) => rec('openSession', [id, opts]),
  notify: msg => rec('notify', [msg]),
  navigate: path => rec('navigate', [path]),
  revealPane: id => rec('revealPane', [id]),
  onEvent: (type, fn) => ctx.onEvent(type, fn),
  request: async (method, params) => {
    rec('request', [method, params])
    if (method === 'session.create') {
      n += 1
      return { session_id: 'rt_' + n, stored_session_id: '20260101_00000' + n + '_fake', message_count: 0, messages: [], info: {} }
    }
    if (method === 'session.resume') return { session_id: 'rt_resumed_' + params.session_id }
    if (method === 'prompt.submit') {
      const sid = params.session_id
      REPLY.forEach((text, i) => setTimeout(() => emit('message.delta', { session_id: sid, payload: { text } }), 150 * (i + 1)))
      setTimeout(() => emit('message.complete', { session_id: sid, payload: { text: REPLY.join(''), status: 'complete' } }), 150 * (REPLY.length + 1))
      return { status: 'accepted' }
    }
    return {}
  }
}

export const ctx = {
  source: 'plugin:1guy',
  contribs: [],
  register(c) { this.contribs.push(c); return () => {} },
  registerMany(cs) { cs.forEach(c => this.contribs.push(c)); return () => {} },
  async rest(path, opts = {}) {
    const r = await fetch('/api/plugins/1guy' + path, {
      method: opts.method || 'GET',
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined
    })
    const data = await r.json().catch(() => null)
    // same shape as the real app: a plain Error, status only inside the message
    if (!r.ok) throw new Error("Error invoking remote method 'hermes:api': Error: " + r.status + ': ' + JSON.stringify(data))
    return data
  },
  onEvent(type, fn) {
    if (!listeners.has(type)) listeners.set(type, [])
    listeners.get(type).push(fn)
    return () => {}
  },
  onDispose() {},
  setTimeout: (fn, ms) => { const id = setTimeout(fn, ms); return () => clearTimeout(id) },
  setInterval: (fn, ms) => { const id = setInterval(fn, ms); return () => clearInterval(id) },
  addEventListener: (target, type, fn, o) => { target.addEventListener(type, fn, o); return () => target.removeEventListener(type, fn, o) },
  os: { openExternal: async url => rec('openExternal', [url]) },
  i18n: { register() {}, t: () => null }
}

export const cn = (...xs) => xs.filter(Boolean).join(' ')
export const PALETTE_AREA = 'palette'
export const PANES_AREA = 'panes'
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar.nav'
export const TRANSCRIPT_DIRECTIVE_AREA = 'transcript.directive'
// same posture as the app's primitive: opaque origin, scripts only
// ?break=frame: the frame loads a dead document until window.__frameBroken is set false,
// to exercise the "did not finish loading" card and its reload button.
if (new URLSearchParams(location.search).get('break') === 'frame') window.__frameBroken = true
export const SandboxedFrame = ({ src, title, className, ref }) => ({
  type: 'iframe',
  props: { src: window.__frameBroken ? 'data:text/html,<p>dead</p>' : src, title, className, ref, sandbox: 'allow-scripts allow-forms', referrerpolicy: 'no-referrer' }
})
// one-shot query: enough to render a pane with real backend data
import { useEffect, useState } from './react.js'
export const useQuery = ({ queryFn }) => {
  const [state, set] = useState({ data: null, isLoading: true, isError: false })
  useEffect(() => {
    queryFn().then(data => set({ data, isLoading: false, isError: false }), () => set({ data: null, isLoading: false, isError: true }))
  })
  return state
}
