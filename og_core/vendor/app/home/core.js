// 1guy home · core (WS-A1). Defines window.OneGuy per docs/home-contract.md plus the
// deltas from WS-A2/E/F: event bus, kind registry, api, toast, ask dialog, the generic
// action runner, openSession/openUrl per door, persistent placements, and stubs that
// main.js replaces with the real canvas / orb / rail.

export const params = new URLSearchParams(location.search);

// ---------------------------------------------------------------- where this copy is mounted
// APP_BASE = the directory that holds home/ (this module is <app>/home/core.js), so static assets
// follow the module wherever it's served. API_BASE = <meta name="oneguy-api" content="…">, else
// inferred from the mount ('/api/plugins/oneguy/app/' → '/api/plugins/oneguy'), else '/api'.
export const APP_BASE = new URL('../', import.meta.url).pathname;
const mount = APP_BASE.replace(/\/app\/$/, '');
const metaApi = (typeof document !== 'undefined' && document.querySelector('meta[name="oneguy-api"]')?.content || '').trim();
export const API_BASE = (metaApi || mount || '/api').replace(/\/+$/, '') || '/api';
export const MOUNTED = API_BASE !== '/api' || APP_BASE !== '/app/';
// canonical path → mounted URL. Anything that isn't '/api…' or '/app/…' passes through untouched.
export function resolve(path) {
  const p = String(path == null ? '' : path);
  if (window.ONEGUY_ASSETS && window.ONEGUY_ASSETS[p]) return window.ONEGUY_ASSETS[p];
  if (/^\/api(?=[/?#]|$)/.test(p)) return API_BASE + p.slice(4);
  if (p.startsWith('/app/')) return APP_BASE + p.slice(5);
  return p;
}

// Links the server hands us (block actions, artifact urls, kind templates) are canonical too:
// when mounted, fix an <a href="/app/…|/api/…"> up at the moment it's used, in this one place.
if (MOUNTED && typeof document !== 'undefined') {
  const fix = (e) => {
    const a = e.target && e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const h = a.getAttribute('href'), r = resolve(h);
    if (r !== h) a.setAttribute('href', r);
  };
  for (const t of ['pointerdown', 'click', 'auxclick', 'contextmenu', 'focusin']) document.addEventListener(t, fix, true);
}

// ?fixture=1 → fixtures/board.json (v1); ?fixture=2 → fixtures/board-v11.json (v1.1: headlines, for_you, briefing)
// ?sample=1 → the sample desk (door B's `look around a sample desk`): the synthetic demo world from
// GET /api/board?sample=1, read-only. It rides the fixture rails (verbs are simulated, nothing goes to
// the ledger, its own layout namespace) and the network door below refuses every write on top of that.
const FX = params.get('fixture');
export const SAMPLE = params.get('sample') === '1' || window.ONEGUY_SAMPLE === true;
export const FIXTURE = SAMPLE || FX === '1' || FX === '2';
export const FIXTURE_URL = SAMPLE ? '/api/board?sample=1' : FX === '2' ? '/app/home/fixtures/board-v11.json' : '/app/home/fixtures/board.json';
export const PANE = params.get('pane') === '1' || window.ONEGUY_EMBED === true;
const nsParam = (params.get('ns') || '').toLowerCase();
export const NS = SAMPLE ? 'sample' : FIXTURE ? (FX === '2' ? 'fixture2' : 'fixture') : (/^[a-z0-9][a-z0-9_-]{0,31}$/.test(nsParam) ? nsParam : 'live');
export const IOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || navigator.standalone === true;
export const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------------------------------------------------------------- small utils
export const $ = (sel, root = document) => root.querySelector(sel);
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, p) => a + (b - a) * p;
export function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
export function hash(s) {             // stable 32-bit hash (FNV-1a) for deterministic "messiness"
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0) / 4294967295;
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NUMW = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
export const words = (n) => (n <= 10 ? NUMW[n] : String(n));

// ---------------------------------------------------------------- the headline layer (v1.1)
// Every face shows block.headline (written by the one guy, WS-H). Until it exists we fall back
// to the raw title cut to 8 words at a natural break, with the rest (or the summary) as "why".
// No paths, session ids or #tags ever reach a face.
export function plain(s) {
  return String(s || '')
    .replace(/https?:\/\/\S+/g, '')                            // links
    .replace(/\*\*|__|`/g, '')                                 // markdown marks
    .replace(/\s*\([^)]*\)/g, '')                              // asides in parentheses
    .replace(/(^|\s)(~|\.{1,2})?\/[\w.~@%+-]+(\/[\w.~@%+-]*)*/g, ' ')   // paths
    .replace(/(^|\s)[\w.-]+\/[\w./-]+\.(md|py|js|json|html|sh|ya?ml|txt)\b/g, ' ')
    .replace(/\b\d{8}_\d{6}_[0-9a-f]{4,}\b/g, '')            // session ids
    .replace(/\b(?:api|bg|cron|deleg|sess)?_?\d{6,13}_[0-9a-f]{4,}\b/gi, '') // session ids, epoch form
    .replace(/(^|\s)#[\w-]+/g, ' ')                           // #tags
    .replace(/\s+([,.;:?!])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();
}
const cutWords = (s, n) => {
  const w = String(s || '').split(/\s+/).filter(Boolean);
  if (w.length <= n) return w.join(' ');
  return w.slice(0, n).join(' ').replace(/[\s,;:—–-]+$/, '') + '…';
};
function splitTitle(raw) {
  const t = plain(raw);
  // the first natural break (— → . ? ; or "label: lowercase") that leaves a 2–10 word head
  const re = /(\s[—–→]\s|\.\s|\?\s|;\s|:\s(?=[a-z]))/g;
  let m;
  while ((m = re.exec(t))) {
    const head = t.slice(0, m.index).trim() + (m[1].trim() === '?' ? '?' : '');
    const n = head.split(/\s+/).length;
    if (n > 10) break;
    if (n >= 2) return { head, rest: t.slice(m.index + m[0].length).trim() };
  }
  const w = t.split(/\s+/);
  return { head: w.slice(0, 8).join(' '), rest: w.slice(8).join(' ') };
}
// a why is one thought: stop at the first sentence break if that leaves ≥ 3 words
const firstThought = (s) => {
  const m = /^(.+?)([.;?!])\s/.exec(s || '');
  return m && m[1].split(/\s+/).length >= 3 ? m[1] + (m[2] === '?' ? '?' : '') : (s || '');
};
export const codey = (t) => {
  const s = String(t || '');
  const hits = (s.match(/\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|(^|\s)--?[a-z][\w-]+|\w+\([^)]*\)|\b\w+=\S+|[{}<>|;`$]|\b\w+\.(py|js|json|sh|md|ya?ml|db)\b/g) || []).length;
  return hits >= 2 || (hits === 1 && s.split(/\s+/).length <= 6);
};
export function headlineOf(b) {
  if (!b) return { title: '', why: '' };
  const h = b.headline && typeof b.headline === 'object' ? b.headline : null;
  if (h && h.title) return { ...h, title: String(h.title), why: String(h.why || ''), fallback: false };
  const { head, rest } = splitTitle(b.title || b.id || '');
  const sum = plain(b.summary || '');
  // a chat's stock summary ("idle · 10 messages · last: …") is status + count + his own last line, all shown
  // elsewhere on the card: it is not a why
  // (and its title stays whole: "Notes app: write the export" is the thing, "Notes app" is only the topic — )
  // What belongs under the title is the last thing he said in it — the question the chat ended on (the
  // lead card was 60% empty). `said` tells a view that shows that line itself (the chat card, the open card's
  // "where it left off") not to print it twice.
  if (b.kind === 'session' && /^(live|idle) · \d+ messages/.test(sum)) {
    const last = plain((b.detail && b.detail.last_user) || '');
    return { title: cutWords(plain(b.title || head), 8), why: last && !codey(last) ? cutWords(last, 18) : '', fallback: true, said: !!last };
  }
  const same = !sum || sum === plain(b.title || '') || plain(b.title || '').startsWith(sum.replace(/…$/, '')) || sum.startsWith(head);
  const why = firstThought(!same ? sum : rest);
  // a sentence made of config and code (ENV_NAMES, flags, calls, a=b) is not a "why" for a face:
  // the face leads with the headline alone and the raw text stays behind "behind this"
  // the tail of the title is dropped only when it became the why; otherwise the title keeps it
  const title = !same && rest ? plain(b.title || head) : head;
  return { title: cutWords(title, 8), why: codey(why) ? '' : cutWords(why, 14), fallback: true };
}
export const HOT = new Set(['needs-you', 'failed']);
const dormantish = (b) => b.status === 'done' || b.status === 'parked' || (b.kind === 'job' && b.status === 'idle');

// The briefing: ≤ 3 'for you' blocks, ≤ 1 broke job, and his opening line.
// board.briefing (WS-H) wins; else block.for_you; else the top 3 by attention, needs-you first.
export function briefingOf(board, { parked = () => false } = {}) {
  const byId = board.byId || {};
  const live = (b) => b && b.kind !== 'project' && b.status !== 'done' && !parked(b.id);
  const br = board.briefing && typeof board.briefing === 'object' ? board.briefing : null;
  let broke = (br && Array.isArray(br.broke) ? br.broke.map((id) => byId[id]) : board.blocks.filter((b) => b.status === 'failed'))
    .filter((b) => live(b) && b.status === 'failed')
    .sort((a, b) => (b.attention || 0) - (a.attention || 0)).slice(0, 1);
  let fy;
  if (br && Array.isArray(br.for_you)) fy = br.for_you.map((id) => byId[id]).filter(live);
  else if (board.blocks.some((b) => b.for_you)) fy = board.blocks.filter((b) => b.for_you && live(b));
  else {
    fy = board.blocks.filter((b) => live(b) && !dormantish(b) && b.status !== 'failed' && b.kind !== 'goal')
      .sort((a, b) => (b.status === 'needs-you') - (a.status === 'needs-you') || (b.attention || 0) - (a.attention || 0));
  }
  fy = fy.filter((b) => !broke.includes(b));   // no cap: the briefing is a horizontal rail now, scroll as far as it goes
  // his order: what's waiting on a yes from you first, then the rest as picked. Stable.
  fy = fy.map((b, i) => [b, i]).sort((x, y) => (y[0].status === 'needs-you') - (x[0].status === 'needs-you') || x[1] - y[1]).map((x) => x[0]);
  // phase 3.5 (A6): the selector's why rides on each pick (server sets b.fy_why;
  // br.whys is the same map, used as fallback for fixtures)
  for (const b of fy) {
    const w = b.fy_why || (br && br.whys && br.whys[b.id]);
    if (w) b.fy_why = String(w);
  }
  const asks = fy.filter((b) => (headlineOf(b).ask && headlineOf(b).ask.options) || /\?/.test(b.title || '')).length;
  // his headline and the header must say the same number: the server's line stands only
  // while the rail is exactly the server's picks; once the user has put things away or dealt with them, recount
  // (also when the server's picks include the broken thing: it would be counted in "ten things" AND in "one broke")
  const served = br && Array.isArray(br.for_you) && !br.for_you.some((id) => broke.some((b) => b.id === id)) ? br.for_you.length : -1;
  let line = br && br.line && served === fy.length ? String(br.line) : '';
  if (!line) {
    if (!fy.length) line = broke.length ? 'nothing for you today, but one routine broke.' : 'nothing needs you today. here’s everything, quietly.';
    else {
      line = fy.length === 1 ? 'one thing for you today.' : `${words(fy.length)} things for you today.`;
      if (asks === 1 && fy.length > 1) line += ' one’s a quick pick.';
      else if (asks > 1) line += asks === fy.length ? ` all quick picks.` : ` ${words(asks)} are quick picks.`;
      if (broke.length) line += ' and one thing broke.';
    }
  }
  return { forYou: fy, broke, line };
}

// Spring easing for the Web Animations API, sampled into CSS linear (Chrome 113+, Safari 17.2+).
// Falls back to an overshooting cubic-bezier. Motion is never linear: the samples trace a spring.
const SUPPORTS_LINEAR = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('transition-timing-function', 'linear(0, 1)');
const springCache = new Map();
export function springEase(k = 7, w = 10) {
  const key = k + ':' + w;
  if (springCache.has(key)) return springCache.get(key);
  let v = 'cubic-bezier(.2,.9,.25,1.1)';
  if (SUPPORTS_LINEAR) {
    const pts = [];
    for (let i = 0; i <= 40; i++) {
      const p = i / 40;
      pts.push((p >= 1 ? 1 : 1 - Math.exp(-k * p) * Math.cos(w * p)).toFixed(4));
    }
    v = `linear(${pts.join(', ')})`;
  }
  springCache.set(key, v);
  return v;
}
export const EASE_OUT = 'cubic-bezier(.22,.8,.24,1)';
export const EASE_IN_OUT = 'cubic-bezier(.65,0,.35,1)';

// ---------------------------------------------------------------- fly-off (lab/fly-off, graduated)
// A thing that's been dealt with LEAVES: its clone lifts, takes a stamp ("✓ approved"), arcs to a
// point (the one guy takes it) and shrinks away. Transform and opacity are separate animations so
// it stays solid for most of the arc. Used by the canvas, the briefing rail and the bloom.
export function flyOff(node, to, { stamp = '', dur = 640, hold = 260, lift = 110 } = {}) {
  if (!node || !node.isConnected || !to) return Promise.resolve();
  const r = node.getBoundingClientRect();
  if (!r.width || REDUCED) return Promise.resolve();
  const clone = node.cloneNode(true);
  clone.removeAttribute('id');
  clone.classList.remove('glide', 'og-pre', 'og-src-hidden', 'og-gone', 'og-recede');
  clone.classList.add('og-flyer', 'og-flyoff');
  Object.assign(clone.style, { position: 'fixed', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px',
    maxHeight: 'none', margin: '0', transform: 'none', opacity: '1', visibility: 'visible', pointerEvents: 'none', zIndex: '70' });
  if (stamp) clone.append(el('span', 'og-stamp', stamp));
  document.body.append(clone);
  const dx = to.x - (r.left + r.width / 2), dy = to.y - (r.top + r.height / 2);
  const cx = dx / 2, cy = Math.min(0, dy) - lift;
  const end = Math.max(0.06, Math.min(0.2, 44 / r.width));
  const frames = [];
  for (let i = 0; i <= 20; i++) {
    const t = i / 20, u = 1 - t;
    const x = 2 * u * t * cx + t * t * dx, y = 2 * u * t * cy + t * t * dy;
    frames.push({ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${(t * 10).toFixed(1)}deg) scale(${(1.03 - t * (1.03 - end)).toFixed(3)})` });
  }
  clone.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.03)' }], { duration: hold, easing: EASE_OUT, fill: 'forwards' });
  setTimeout(() => emit('fly', { phase: 'launch' }), hold);
  const a = clone.animate(frames, { duration: dur, delay: hold, easing: 'cubic-bezier(.45,.05,.3,1)', fill: 'forwards' });
  clone.animate([{ opacity: 1 }, { opacity: 1, offset: 0.72 }, { opacity: 0 }], { duration: dur, delay: hold, easing: 'linear', fill: 'forwards' });
  return a.finished.catch(() => {}).then(() => { clone.remove(); emit('fly', { phase: 'land' }); });
}
// which of a block's verbs mean "this is dealt with", and what the stamp says
export const SENT = { done: '✓ done', keep: '✓ kept', approve: '✓ approved', dismiss: 'not now', reject: 'sent back', delete: 'deleted' };

// ---------------------------------------------------------------- persistent placements
// Placements are the user's: {x, y, w, h, size, parked, at}. Kept in localStorage AND on the server
// (GET/POST /api/home/state?ns=…), namespaced so fixture/test runs never touch the user's real layout.
const LS_KEY = 'oneguy.home.' + NS;
export const store = {
  data: { placements: {}, view: {}, expanded: [], updated: 0 },
  _t: null,
  async load() {
    let local = null, remote = null;
    try { local = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); } catch (e) { local = null; }
    try { remote = await api.get('/api/home/state?ns=' + encodeURIComponent(NS)); } catch (e) { remote = null; }
    const pick = [local, remote].filter((x) => x && typeof x === 'object' && x.placements)
      .sort((a, b) => (b.updated || 0) - (a.updated || 0))[0];
    if (pick) this.data = { placements: pick.placements || {}, view: pick.view || {}, expanded: pick.expanded || [], updated: pick.updated || 0 };
    return this.data;
  },
  get(id) { return this.data.placements[id] || null; },
  set(id, patch) {
    const cur = this.data.placements[id] || {};
    const next = { ...cur, ...patch, at: Date.now() };
    for (const k of Object.keys(next)) if (next[k] === undefined) delete next[k];
    this.data.placements[id] = next;
    this.save();
    return next;
  },
  drop(id) { delete this.data.placements[id]; this.save(); },
  setView(v) { this.data.view = { ...this.data.view, ...v }; this.save(); },
  setFocus(id) { this.setView({ focus: id || null }); },   // the block we were working on (survives reload)
  setExpanded(list) { this.data.expanded = [...list]; this.save(); },
  save() {
    this.data.updated = Date.now();
    try { localStorage.setItem(LS_KEY, JSON.stringify(this.data)); } catch (e) { /* private mode */ }
    clearTimeout(this._t);
    this._t = setTimeout(() => {
      api.post('/api/home/state?ns=' + encodeURIComponent(NS), this.data).catch((e) => console.warn('[home] state save failed', e));
    }, 700);
  },
};

// ---------------------------------------------------------------- api
// THE one network door. Every client call goes through api(path, opts); callers (and the server's
// own block actions) keep writing canonical paths — '/api/board', '/app/runs.html' — and
// resolve maps them onto wherever this copy is mounted. Standalone: '/api' + '/app/'. As a
// Hermes plugin: '/api/plugins/oneguy' + '/api/plugins/oneguy/app/'.
//   api(path, opts) → fetch Response      api.get / api.post → parsed JSON (throws on !ok)
//   api.url(path)   → the resolved URL (links, iframes, <img>)
// On the sample desk nothing is saved: a write never leaves the page (the tasklist, threads, the
// ledger and the layout file stay exactly as they were), and the only API read is the sample board.
const SAMPLE_READS = /^\/api\/(board\?sample=1|routes|health)\b/;
async function api(path, opts) {
  if (SAMPLE && /^\/api\//.test(String(path))) {
    const write = !!(opts && opts.method && String(opts.method).toUpperCase() !== 'GET');
    if (write || !SAMPLE_READS.test(String(path))) {
      return new Response(JSON.stringify({ ok: false, sample: true, error: 'this is the sample desk. nothing here is saved.' }),
        { status: write ? 403 : 404, headers: { 'Content-Type': 'application/json' } });
    }
  }
  return fetch(resolve(path), opts);
}
async function req(method, url, body) {
  const r = await api(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  });
  let data = null;
  const text = await r.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text.slice(0, 300) }; }
  if (!r.ok) {
    const msg = (data && (data.error || data.message)) || `${r.status} ${r.statusText}`;
    const err = new Error(msg);
    err.status = r.status; err.data = data;
    throw err;
  }
  return data;
}
api.get = (url) => req('GET', url);
api.post = (url, body) => req('POST', url, body || {});
api.url = resolve;
api.base = API_BASE;
api.app = APP_BASE;
export { api };

// ---------------------------------------------------------------- the judgment ledger (one door)
// Every decision the user makes is a judgment and goes to POST /api/judgment — not only drags. Gestures
// that own their own moment call judge directly (cluster, nudge, reset); everything that already
// announces itself on the bus (a verb ran, a card was put away or taken back, he was steered, a
// "next:" was answered) is logged HERE, so no new gesture can forget to.
export function judge(kind, block, target, note) {
  if (FIXTURE) return;               // fixture ids aren't real
  try {
    api('/api/judgment', { method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ kind, block: block ? (block.id || String(block)) : null, target: target || null, note: note || null }) })
      .then((r) => { if (!r.ok) console.warn('[home] judgment refused', kind, r.status); })   // a refused kind must be seen
      .catch(() => {});
  } catch (err) { /* the ledger never blocks the gesture */ }
}
const VERB_KIND = { done: 'done', keep: 'keep', dismiss: 'not-now', delete: 'delete', run: 'run', approve: 'verdict', steer: 'verdict', reject: 'verdict' };

// ---------------------------------------------------------------- toast
let toastT = null;
function toast(msg, isErr) {
  const t = $('#og-toast');
  if (!t) return;
  t.textContent = String(msg);
  t.classList.toggle('err', !!isErr);
  t.classList.add('show');
  clearTimeout(toastT);
  toastT = setTimeout(() => t.classList.remove('show'), isErr ? 5200 : 2800);
}

// ---------------------------------------------------------------- ask dialog (confirm + note)
export function ask({ title, text, note, okLabel = 'ok', placeholder = '' }) {
  return new Promise((resolve) => {
    const scrim = el('div', 'og-ask-scrim');
    const box = el('form', 'og-ask');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    if (title) box.append(el('div', 'og-ask-t', title));
    if (text) box.append(el('p', 'og-ask-p', text));
    let ta = null;
    if (note) {
      ta = el('textarea', 'og-ask-note');
      ta.rows = 3;
      ta.placeholder = placeholder || 'a note for this…';
      box.append(ta);
    }
    const row = el('div', 'og-ask-row');
    const cancel = el('button', 'og-btn', 'cancel'); cancel.type = 'button';
    const ok = el('button', 'og-btn og-btn-pri', okLabel); ok.type = 'submit';
    row.append(cancel, ok);
    box.append(row);
    scrim.append(box);
    document.body.append(scrim);
    requestAnimationFrame(() => scrim.classList.add('show'));
    (ta || ok).focus();
    // the card it asks about went away (put away, closed, he moved on): the question goes with it, so nothing
    // is left standing over the board with the next card's offer stuck behind it
    let settled = false;
    const gone = () => done(null);
    const done = (v) => {
      if (settled) return;
      settled = true;
      for (const off of offs) off();
      scrim.classList.remove('show');
      document.removeEventListener('keydown', onKey, true);
      setTimeout(() => scrim.remove(), 300);
      resolve(v);
    };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); done(null); } };
    document.addEventListener('keydown', onKey, true);
    const offs = [on('park', gone), on('bloom-close', gone)];
    cancel.onclick = () => done(null);
    scrim.addEventListener('pointerdown', (e) => { if (e.target === scrim) done(null); });
    box.onsubmit = (e) => {
      e.preventDefault();
      if (note && !ta.value.trim()) { ta.focus(); ta.classList.add('need'); return; }
      done({ note: ta ? ta.value.trim() : undefined });
    };
    if (ta) ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) box.requestSubmit(); });
  });
}

// ---------------------------------------------------------------- doors (WS-E)
let paneBridge = false;
if (PANE && window.parent !== window) {
  window.addEventListener('message', (e) => {
    if (e.source === window.parent && e.data && e.data.type === 'oneguy:pane') paneBridge = true;
  });
  try { window.parent.postMessage({ type: 'oneguy:hello' }, '*'); } catch (e) { /* ignore */ }
}
export function openUrl(url) {
  if (!url) return;
  let u;
  try { u = new URL(resolve(url), location.href); } catch (e) { return toast('bad link', true); }
  if (!/^(https?:|hermes:|hermesapp:|mailto:)$/.test(u.protocol)) return toast('refusing that link', true);
  const same = u.origin === location.origin;
  if (PANE && window.parent !== window && !same && /^https?:$/.test(u.protocol)) {
    window.parent.postMessage({ type: 'oneguy:open-url', url: u.href }, '*');
    return;
  }
  if (same || PANE) { location.assign(u.href); return; }
  if (/^https?:$/.test(u.protocol)) window.open(u.href, '_blank', 'noopener');
  else location.href = u.href;
}
function openSession(id) {
  if (!id) return;
  if (PANE && window.parent !== window) {
    // inside the Hermes Desktop pane: the plugin opens it (it answered our hello with oneguy:pane)
    window.parent.postMessage({ type: 'oneguy:open-session', session_id: String(id), intent: 'stack' }, '*');
    if (paneBridge) return;
  }
  location.href = IOS ? 'hermesapp://session/' + encodeURIComponent(id) : 'hermes://open/' + encodeURIComponent(id);
}

// ---------------------------------------------------------------- the bus + OneGuy
const listeners = new Map();
const kinds = {};
const hooks = {};   // filled by main.js: focus, unfocus, park, summon, refresh, applyLocal

function on(evt, fn) {
  if (!listeners.has(evt)) listeners.set(evt, new Set());
  listeners.get(evt).add(fn);
  return () => listeners.get(evt).delete(fn);
}
function emit(evt, payload) {
  for (const fn of [...(listeners.get(evt) || [])]) {
    try { fn(payload || {}); } catch (err) { console.warn(`[home] '${evt}' listener failed`, err); }
  }
}

// Fixture mode: the fixture's ids look real, so actions are SIMULATED, never posted.
const SIM_REMOVE = new Set(['dismiss', 'reject', 'delete', 'keep', 'approve', 'not-now', 'notnow']);
function simulate(block, action, note) {
  const b = OneGuy.board.byId[block.id];
  if (!b) return { ok: true, simulated: true };
  if (action.id === 'done' || /^done|toggle$/.test(action.body?.op || '')) {
    b.status = b.status === 'done' ? 'active' : 'done';
  } else if (SIM_REMOVE.has(action.id)) {
    OneGuy.board.blocks = OneGuy.board.blocks.filter((x) => x.id !== b.id);
    delete OneGuy.board.byId[b.id];
  } else if (action.id === 'edit' && note) {
    b.title = b.summary = note;
    if (b.detail) b.detail.text = note;
  } else if (action.id === 'run' || action.id === 'steer') {
    b.status = 'active';
  }
  return { ok: true, simulated: true };
}

const PLEASED = { done: 'nice. one less.', keep: 'kept. it’s on your list.', approve: 'approved.', dismiss: 'gone.', run: 'running it.', steer: 'steered.', reject: 'sent back.', delete: 'deleted.' };

async function runAction(block, action) {
  if (!block || !action) return null;
  try {
    if (action.surface) {
      if (OneGuy.surface && OneGuy.surface.open && !OneGuy.surface._stub) return await OneGuy.surface.open(block);
      toast('work surfaces aren’t loaded', true);
      return null;
    }
    if (action.href && !action.post && !action.get) { openUrl(action.href); return { ok: true }; }
    let note;
    if (action.note || action.confirm) {
      const res = await ask({
        title: action.label || action.id,
        text: action.confirm || (action.note ? `${action.label || action.id}: ${block.title}` : ''),
        note: !!action.note,
        okLabel: action.label || 'ok',
        placeholder: action.id === 'edit' ? (block.detail?.text || block.title || '') : '',
      });
      if (!res) return null;
      note = res.note;
    }
    let result;
    if (FIXTURE && (action.post || action.get)) {
      OneGuy.orb.mood('think');
      await sleep(450);
      result = simulate(block, action, note);
      toast(`simulated · ${action.label || action.id} (fixture)`);
    } else if (action.post) {
      const body = { ...(action.body || {}) };
      if (note !== undefined) { body.note = note; if (action.id === 'edit' && body.text === undefined) body.text = note; }
      OneGuy.orb.mood('think');
      result = await api.post(action.post, body);
    } else if (action.get) {
      result = await api.get(action.get);
    } else {
      return null;
    }
    if (result && result.ok === false) throw new Error(result.error || 'it didn’t take');
    OneGuy.orb.mood('pleased');
    OneGuy.orb.say(PLEASED[action.id] || (action.label || 'done') + '.', { ms: 1800 });
    if (!FIXTURE) toast(`${action.label || action.id} ✓`);
    emit('action-done', { block, action, result });
    if (FIXTURE) hooks.applyLocal && hooks.applyLocal();
    else await OneGuy.refresh();
    return result;
  } catch (err) {
    OneGuy.orb.mood('idle');
    toast(`${action.label || action.id}: ${err.message || err}`, true);
    return null;
  }
}

// ---------------------------------------------------------------- decide + work-on (v1.1)
// K's decision buttons call OneGuy.decide(block, option). The pick is said to the block's
// surface (T's OneGuy.surface.say, e.g. "the user picked: merge it"), then the block's own action runs
// (option.action names it; default 'done'), and he acknowledges it. Fixture: simulated, nothing sent.
async function surfaceSay(block, text, opts) {
  const S = OneGuy.surface;
  if (!S || S._stub || !text) return null;
  if (typeof S.say === 'function') return S.say(block, text, opts || {});
  if (typeof S.controller === 'function') return S.controller(block).send(text, 'say');
  return null;
}
async function decide(block, option) {
  if (!block || !option) return null;
  const b = OneGuy.board.byId[block.id] || block;
  const acts = b.actions || [];
  const norm = (x) => String(x || '').trim().toLowerCase();
  // an option that names one of the block's own verbs (crew: approve / steer / reject) runs that verb
  const act = (option.action && acts.find((a) => a.id === option.action))
    || acts.find((a) => ['approve', 'reject', 'steer', 'keep', 'dismiss'].includes(a.id) && (norm(a.id) === norm(option.label) || norm(a.label) === norm(option.label)))
    || acts.find((a) => a.id === 'done') || null;
  if (act && act.note && !FIXTURE) {   // steer / reject need the user's reason: the shell's note box, then the post
    if (option.say) await surfaceSay(b, option.say, { picked: true });
    return OneGuy.runAction(b, act);
  }
  const label = option.label || option.say || 'that';
  OneGuy.orb.mood('think');
  try {
    if (FIXTURE) {
      await sleep(450);
      if (act) simulate(b, act);
      else if (OneGuy.board.byId[b.id]) OneGuy.board.byId[b.id].status = 'done';
      toast(`simulated · you picked “${label}” (fixture)`);
    } else {
      if (option.say) await surfaceSay(b, option.say, { picked: true });
      if (act && (act.post || act.get)) {
        const res = act.post ? await api.post(act.post, { ...(act.body || {}) }) : await api.get(act.get);
        if (res && res.ok === false) throw new Error(res.error || 'it didn’t take');
      }
      toast(`picked: ${label}`);
    }
    OneGuy.orb.mood('pleased');
    OneGuy.orb.say('got it.', { ms: 1800 });
    emit('decided', { block: b, option });
    if (act) emit('action-done', { block: b, action: act, result: { ok: true, decided: label } });
    if (OneGuy.focused && OneGuy.focused.id === b.id) setTimeout(() => OneGuy.unfocus(), 700);
    if (FIXTURE) hooks.applyLocal && hooks.applyLocal();
    else await OneGuy.refresh();
    return { ok: true };
  } catch (err) {
    OneGuy.orb.mood('idle');
    toast(`${label}: ${err.message || err}`, true);
    return null;
  }
}
// 'work on this with me': open the block's surface, and if its thread is empty send the
// one guy's opener (headline.first_message). The one talk bar then targets this block.
async function workOn(block) {
  const b = OneGuy.board.byId[block.id] || block;
  const S = OneGuy.surface;
  if (!S || S._stub) { toast('work surfaces aren’t loaded', true); return null; }
  if (!FIXTURE && typeof S.workOn === 'function') {   // T's: opens, sends first_message only if empty
    try { await S.workOn(b); } catch (e) { toast('couldn’t start it: ' + (e.message || e), true); }
    try { OneGuy.talk && OneGuy.talk.focus && OneGuy.talk.focus(); } catch (e) { /* ignore */ }
    emit('work-on', { block: b });
    return { ok: true };
  }
  const h = headlineOf(b);
  let empty = true;
  try {
    await S.open(b);
    const c = typeof S.controller === 'function' ? S.controller(b) : null;
    if (c && Array.isArray(c.turns)) empty = c.turns.length === 0;
  } catch (e) { console.warn('[home] surface open failed', e); }
  if (empty && h.first_message) {
    if (FIXTURE) { OneGuy.orb.say(h.first_message, { ms: 6000 }); toast('simulated · started the thread (fixture)'); }
    else { try { await surfaceSay(b, h.first_message); } catch (e) { toast('couldn’t start it: ' + (e.message || e), true); } }
  } else if (empty) OneGuy.orb.say('go ahead. what do you want to do with it?', { ms: 3200 });
  try { OneGuy.talk && OneGuy.talk.focus && OneGuy.talk.focus(); } catch (e) { /* ignore */ }
  emit('work-on', { block: b });
  return { ok: true };
}

const noop = () => {};
const OneGuy = {
  version: 'home-v1.1/S',
  fixture: FIXTURE, sample: SAMPLE, pane: PANE, ns: NS,
  board: { blocks: [], byId: {}, projects: [], sources: {} },
  on, emit,
  kinds,
  registerKind(kind, def) {
    if (!kind || !def) return;
    kinds[kind] = def;
    emit('kind', { kind });
  },
  focus: (id, o) => hooks.focus && hooks.focus(id, o),
  unfocus: () => hooks.unfocus && hooks.unfocus(),
  park: (id) => hooks.park && hooks.park(id),
  summon: (id, rect, o) => hooks.summon && hooks.summon(id, rect, o),
  refresh: () => (hooks.refresh ? hooks.refresh() : Promise.resolve()),
  zoom: (level) => hooks.zoom && hooks.zoom(level),
  view: (mode, o) => (hooks.view ? hooks.view(mode, o) : null),   // 'briefing' | 'canvas'
  runAction,
  decide,
  workOn,
  headline: headlineOf,
  briefing: (board) => briefingOf(board || OneGuy.board, { parked: (id) => !!(store.get(id) && store.get(id).parked) }),
  api,
  toast,
  ask,
  openSession,
  openUrl,
  // stubs until orb.js / rail.js / surface.js arrive
  orb: { say: noop, mood: noop, pointAt: noop },
  caption: { j: noop },
  rail: { addChip: noop, removeChip: noop },
  surface: { open: () => toast('work surfaces aren’t loaded yet', true), _stub: true },
  focused: null,
  _hooks: hooks,
};
on('action-done', ({ block, action, result } = {}) => {
  if (!block || !action) return;
  if (result && result.decided) return;      // logged by 'decided' below, once
  const k = VERB_KIND[action.id];
  if (k) judge(k, block, k === 'verdict' ? action.id : null, action.id === 'done' && block.status === 'done' ? 'reopen' : null);
});
on('decided', ({ block, option } = {}) => { if (block && option) judge('decide', block, String(option.label || option.say || '')); });
on('park', ({ block } = {}) => { if (block) judge('park', block); });
on('summon', ({ block, wasParked } = {}) => { if (block && wasParked) judge('unpark', block); });
on('steer', ({ block, from } = {}) => { if (block) judge('steer', block, from || null); });
on('next', ({ block, answer } = {}) => { if (block) judge('next', block, null, answer); });
OneGuy.judge = judge;

// ------------------------------------------------------------------ the view: what the user is looking at
// Built HERE and nowhere else; it rides with every talk turn (/api/talk, /api/surface/say) so the
// one guy can resolve "this", "these", "the ones on screen". Ids only, no pixels: the page measures
// the screen and says what it means.
//   {lens, level, screen, focused, selected[], visible[] (≤40, most attention first), trail[], issues[] (≤5)}
// issues = what the page can cheaply see is wrong with its own layout: a card whose headline is cut
// off ("clipped"), two cards sitting on each other ("overlap"). The server keeps the last 200.
const VIEW_VISIBLE = 40, VIEW_ISSUES = 5;
// the element that actually cuts the headline off (kinds.css clamps .kf-tt, not its .kf-title row)
const headlineEl = (node) => node.querySelector('.kf-tt') || node.querySelector('.kf-title, .og-tile-t, .og-made-t');
const seen = (n) => !n.checkVisibility || n.checkVisibility({ opacityProperty: true, visibilityProperty: true });
export function viewContext() {
  const byId = (OneGuy.board && OneGuy.board.byId) || {};
  const vw = window.innerWidth, vh = window.innerHeight;
  const on = new Map();                 // id → { node, r, canvas }
  for (const node of document.querySelectorAll('.og-card[data-id], #og-brief [data-id]')) {
    const id = node.dataset.id;
    if (!id || on.has(id) || !byId[id] || !seen(node)) continue;
    const r = node.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || r.right <= 0 || r.bottom <= 0 || r.left >= vw || r.top >= vh) continue;
    on.set(id, { node, r, canvas: node.classList.contains('og-card') });
  }
  const weight = (id) => (byId[id].attention || 0) + (HOT.has(byId[id].status) ? 0.08 : 0);
  const visible = [...on.keys()].sort((a, b) => weight(b) - weight(a)).slice(0, VIEW_VISIBLE);
  const issues = [];
  for (const id of visible) {
    if (issues.length >= VIEW_ISSUES) break;
    const h = headlineEl(on.get(id).node);
    if (h && seen(h) && (h.scrollWidth > h.clientWidth + 1 || h.scrollHeight > h.clientHeight + 1)) issues.push({ id, kind: 'clipped' });
  }
  // a card in the user's hand is over others on purpose: no overlap readings mid-drag
  const cards = document.body.classList.contains('og-dragging') ? [] : visible.filter((id) => on.get(id).canvas);
  pairs: for (let i = 0; i < cards.length; i++) {
    for (let j = i + 1; j < cards.length; j++) {
      if (issues.length >= VIEW_ISSUES) break pairs;
      const a = on.get(cards[i]).r, b = on.get(cards[j]).r;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 6 && h > 6) issues.push({ id: cards[j], kind: 'overlap', with: cards[i] });
    }
  }
  const ids = (v) => (Array.isArray(v) ? v : []).map((x) => (x && x.id) || x).filter((x) => typeof x === 'string');
  return {
    lens: document.body.dataset.ogLens || 'today',
    level: document.body.dataset.level || 'blocks',
    screen: vw <= 700 ? 'phone' : 'desktop',
    focused: (OneGuy.focused && OneGuy.focused.id) || null,
    selected: ids(OneGuy.selected).slice(0, VIEW_VISIBLE),      // empty until the lasso lands; it sets OneGuy.selected
    visible,
    trail: ids(OneGuy.trail ? OneGuy.trail() : []),
    issues,
  };
}
OneGuy.selected = [];
// ------------------------------------------------------------------ the selection (lasso to ask)
// The cards the user drew a box round (gestures.js), ids only, for this visit only: never stored, never
// pixels. Every change goes through here and is announced once ('select'): the canvas outlines the
// cards and shows the one-row menu, the talk bar's chip counts them, viewContext sends them.
const sameIds = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
OneGuy.select = {
  set(ids) {
    const byId = (OneGuy.board && OneGuy.board.byId) || {};
    const next = [...new Set(ids || [])].filter((id) => typeof id === 'string' && byId[id] && byId[id].kind !== 'project').slice(0, VIEW_VISIBLE);
    if (sameIds(next, OneGuy.selected)) return next;
    OneGuy.selected = next;
    emit('select', { ids: next.slice() });
    return next;
  },
  toggle(id) { return this.set(OneGuy.selected.includes(id) ? OneGuy.selected.filter((x) => x !== id) : [...OneGuy.selected, id]); },
  clear() { return this.set([]); },
};
on('focus', ({ block } = {}) => { if (block) OneGuy.select.clear(); });     // one "about" at a time: a card in focus is it
on('lens', () => OneGuy.select.clear());
on('view', () => OneGuy.select.clear());
on('board', () => { if (OneGuy.selected.length) OneGuy.select.set(OneGuy.selected); });   // a card that left the desk leaves the selection
OneGuy.viewContext = () => { try { return viewContext(); } catch (err) { console.warn('[home] viewContext failed', err); return undefined; } };
window.OneGuy = OneGuy;
export default OneGuy;
