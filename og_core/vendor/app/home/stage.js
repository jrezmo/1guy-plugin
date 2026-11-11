// 1guy home · the stage (Phase 4, "show me"; docs/unified-audit.md §5.2, A4 L6).
//
//   OG.stage.open({ kind: 'url'|'article'|'html'|'chart'|'image', src, title, why?, urgent?, spec? }, { asked? })
//   OG.stage.close
//
// Renderers: own/tailnet pages → iframe (allowlist; anything else falls through to the reader);
// article → GET /api/reader (server-side reader view, plain text parts, rendered with textContent);
// html → sandboxed srcdoc iframe (allow-scripts only, no same-origin, CSP connect-src 'none');
// chart → a tiny {type, labels, series} spec drawn as SVG (no libraries); image → <img>.
//
// Rules (L6): opens only when asked (default for a direct call) or urgent, and every open is
// logged; an unasked, non-urgent show (or any show while the user is typing, unless asked) is held in
// the "shown" list instead. One at a time: the one it replaces goes to "shown". Esc, ✕, the
// dimmed backdrop and a swipe down all close it. The talk bar retarget is just an event:
// OG.emit('stage', { open, title, kind, src, about }) (talk.js consumes it in Phase 2).
import { api } from './core.js';

const BUS = window.OneGuy || window.OG || (() => {
  const L = new Map();
  return {
    on(e, f) { if (!L.has(e)) L.set(e, new Set()); L.get(e).add(f); return () => L.get(e).delete(f); },
    emit(e, p) { for (const f of [...(L.get(e) || [])]) { try { f(p || {}); } catch (err) { console.warn(err); } } },
  };
})();
const OG = window.OG = window.OG || BUS;

const KINDS = ['url', 'article', 'html', 'chart', 'image'];
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const SPRING = 'cubic-bezier(.2,.9,.25,1.08)', EASE = 'cubic-bezier(.22,.8,.24,1)';
const TAILNET = /^https:\/\/oneguy\.invalid(:\d+)?$/;
const SVGNS = 'http://www.w3.org/2000/svg';
const MAX_SHOWN = 8;
// a generated widget gets no network and no way out (sandbox already removes same-origin)
const WIDGET_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'";

const isMobile = () => window.innerWidth <= 700;
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  if (text != null) n.textContent = text;
  return n;
}

// ------------------------------------------------------------------ styles + DOM
(() => {
  if (document.querySelector('link[data-og-stage]')) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = new URL('./stage.css', import.meta.url).href;
  l.dataset.ogStage = '1';
  document.head.append(l);
})();
const scrim = el('div', 'og-stage-scrim');
const panel = el('section', 'og-stage');
panel.setAttribute('role', 'dialog');
panel.setAttribute('aria-modal', 'false');
panel.setAttribute('aria-label', 'shown');
panel.tabIndex = -1;
panel.style.outline = 'none';
const hd = el('header', 'og-stage-hd');
const kEl = el('span', 'og-stage-kind'), tEl = el('span', 'og-stage-ttl'), wEl = el('span', 'og-stage-why');
const shownBtn = el('button', 'og-stage-shown-b', 'earlier');
shownBtn.type = 'button';
shownBtn.setAttribute('aria-haspopup', 'menu');
const orig = el('a', 'og-stage-orig', 'original ↗');
orig.target = '_blank';
orig.rel = 'noopener noreferrer';
const xBtn = el('button', 'og-stage-x', '×');
xBtn.type = 'button';
xBtn.setAttribute('aria-label', 'close');
const shownMenu = el('div', 'og-stage-shown-m');
shownMenu.hidden = true;
shownMenu.setAttribute('role', 'menu');
hd.append(kEl, tEl, wEl, shownBtn, orig, xBtn);
const bd = el('div', 'og-stage-bd');
panel.append(hd, shownMenu, bd);
document.body.append(scrim, panel);

// ------------------------------------------------------------------ helpers
function ownUrl(src) {
  let u;
  try { u = new URL(api.url(String(src)), location.href); } catch (e) { return null; }
  if (!/^https?:$/.test(u.protocol)) return null;
  if (u.origin === location.origin || TAILNET.test(u.origin)) return u;
  return null;
}
function safeImg(src) {
  const s = String(src || '');
  if (/^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(s)) return s;
  try {
    const u = new URL(api.url(s), location.href);
    return /^https?:$/.test(u.protocol) ? u.href : null;
  } catch (e) { return null; }
}
function niceDate(iso) {
  const t = Date.parse(iso || '');
  if (!t) return '';
  return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: new Date(t).getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
}
function msg(text, link) {
  const m = el('div', 'og-stage-msg');
  m.append(text);
  if (link) { m.append(' '); const a = el('a', null, link.label); a.href = link.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; m.append(a); }
  return m;
}
function loading(text) {
  const m = el('div', 'og-stage-msg');
  m.append(el('span', 'dot'), text);
  return m;
}
function typing() {
  const a = document.activeElement;
  if (!a || panel.contains(a)) return false;
  return a.isContentEditable || (/^(INPUT|TEXTAREA)$/.test(a.tagName) && String(a.value || '').trim().length > 0);
}
function log(entry) {
  const row = { at: new Date().toISOString(), ...entry };
  if (entry.reason === 'urgent') console.info('[stage] urgent show opened unasked', row);
  try {
    api('/api/stage/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(row), keepalive: true }).catch(() => {});
  } catch (e) { /* no server: fine */ }
}

// ------------------------------------------------------------------ renderers
const R = {
  url(show) {
    const u = ownUrl(show.src);
    if (!u) return R.article({ ...show, kind: 'article' });   // outside pages refuse framing: read them instead
    const f = el('iframe');
    f.src = u.href;
    f.title = show.title || u.pathname;
    f.referrerPolicy = 'same-origin';
    f.setAttribute('loading', 'eager');
    return f;
  },
  article(show) {
    const wrap = el('div');
    wrap.append(loading('reading it for you…'));
    const src = String(show.src || '');
    api('/api/reader?url=' + encodeURIComponent(src)).then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || ('http ' + r.status));
      return d;
    }).then((d) => {
      if (current && current.show !== show) return;
      wrap.replaceChildren(reader(d, show));
      // the reader's own headline + deck carry the title and why; the header just names the source
      if (d.site) tEl.textContent = d.site;
      if (!show.title && d.title) { show.title = d.title; announce(); }
    }).catch((e) => {
      wrap.replaceChildren(msg(`couldn’t read that one (${e.message || e}).`, { label: 'open the original ↗', href: src }));
    });
    return wrap;
  },
  html(show) {
    const f = el('iframe');
    f.setAttribute('sandbox', 'allow-scripts');
    f.referrerPolicy = 'no-referrer';
    f.title = show.title || 'generated';
    const doc = String(show.src || '');
    f.srcdoc = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${WIDGET_CSP}">` + doc;
    return f;
  },
  chart(show) { return chart(show.spec || show.src, show); },
  image(show) {
    const w = el('div', 'og-image');
    const s = safeImg(show.src);
    if (!s) { w.append(msg('that image isn’t somewhere i can show.')); return w; }
    const i = el('img');
    i.alt = show.title || '';
    i.referrerPolicy = 'no-referrer';
    i.src = s;
    i.onerror = () => i.replaceWith(msg('the image didn’t load.'));
    w.append(i);
    return w;
  },
};

function reader(d, show) {
  const a = el('article', 'og-reader');
  const src = el('div', 'src', d.site || '');
  const meta = [d.byline, niceDate(d.date), d.minutes ? d.minutes + ' min' : ''].filter(Boolean).join(' · ');
  if (meta) src.append(el('span', null, (d.site ? ' · ' : '') + meta));
  a.append(src, el('h1', null, d.title || show.title || ''));
  if (show.why) a.append(el('div', 'gw', show.why));
  const img = safeImg(d.image);
  if (img) { const i = el('img', 'hero'); i.alt = ''; i.referrerPolicy = 'no-referrer'; i.src = img; i.onerror = () => i.remove(); a.append(i); }
  let ul = null;
  for (const b of d.blocks || []) {
    if (b.t === 'li') { if (!ul) { ul = el('ul'); a.append(ul); } ul.append(el('li', null, b.text)); continue; }
    ul = null;
    a.append(el(b.t === 'h' ? 'h2' : b.t === 'quote' ? 'blockquote' : b.t === 'pre' ? 'pre' : 'p', null, b.text));
  }
  if (!(d.blocks || []).length) a.append(el('p', null, 'the page didn’t give me much to read. the original is one tap away.'));
  const o = el('a', 'orig', `keep reading at ${d.site || 'the source'} ↗`);
  o.href = d.final_url || d.url || show.src;
  o.target = '_blank';
  o.rel = 'noopener noreferrer';
  a.append(o);
  return a;
}

// chart spec: { type: 'bar'|'line', labels: [..], series: [{ name, values: [..], color? }],
//               big?, note?, unit? }. Grouped bars or lines, one y axis from 0.
const PALETTE = ['#5ad1c3', '#ffb547', '#7fb3ff', '#ef6b6b', '#c3a6ff'];
export function chart(spec, show = {}) {
  let s = spec;
  if (typeof s === 'string') { try { s = JSON.parse(s); } catch (e) { s = null; } }
  const w = el('div', 'og-chart');
  if (!s || !Array.isArray(s.series) || !s.series.length) { w.append(msg('no chart in that one.')); return w; }
  const series = s.series.map((x, i) => ({ name: String(x.name || ''), color: /^#[0-9a-f]{3,8}$/i.test(x.color || '') ? x.color : PALETTE[i % PALETTE.length],
    values: (x.values || []).map((v) => (Number.isFinite(+v) ? +v : 0)) }));
  const n = Math.max(...series.map((x) => x.values.length));
  const labels = Array.from({ length: n }, (_, i) => String((s.labels || [])[i] ?? ''));
  if (s.big || show.big) w.append(el('div', 'big', String(s.big || show.big)));
  if (s.note) w.append(el('div', 'note', String(s.note)));
  const W = 720, H = 280, L = 40, B = 30, T = 14, Rt = 10;
  const max = Math.max(1, ...series.flatMap((x) => x.values));
  const nice = (() => { const p = 10 ** Math.floor(Math.log10(max)); return Math.ceil(max / p) * p; })();
  const y = (v) => T + (H - T - B) * (1 - v / nice);
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', role: 'img', 'aria-label': show.title || 'chart' });
  for (let k = 0; k <= 4; k++) {
    const v = nice * k / 4, yy = y(v);
    root.append(svg('line', { x1: L, x2: W - Rt, y1: yy, y2: yy, stroke: '#2a2d34', 'stroke-width': 1 }));
    root.append(svg('text', { x: L - 8, y: yy + 4, 'text-anchor': 'end', fill: '#80838b', 'font-size': 11, 'font-family': 'Inter,sans-serif' },
      (Math.round(v * 10) / 10) + (s.unit && k === 4 ? s.unit : '')));
  }
  const band = (W - L - Rt) / Math.max(1, n);
  labels.forEach((lab, i) => root.append(svg('text', { x: L + band * (i + 0.5), y: H - 8, 'text-anchor': 'middle', fill: '#80838b', 'font-size': 12, 'font-family': 'Inter,sans-serif' }, lab)));
  if (s.type === 'line') {
    series.forEach((se) => {
      const pts = se.values.map((v, i) => `${L + band * (i + 0.5)},${y(v)}`).join(' ');
      root.append(svg('polyline', { class: 'line', points: pts, fill: 'none', stroke: se.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      se.values.forEach((v, i) => root.append(svg('circle', { cx: L + band * (i + 0.5), cy: y(v), r: 3.5, fill: se.color })));
    });
  } else {
    const gw = band * 0.64, bw = gw / series.length;
    series.forEach((se, j) => se.values.forEach((v, i) => {
      const x = L + band * i + (band - gw) / 2 + bw * j, yy = y(v);
      const r = svg('rect', { class: 'bar', x: x + 1, y: yy, width: Math.max(1, bw - 2), height: Math.max(0, H - B - yy), rx: Math.min(6, bw / 4), fill: se.color });
      r.style.animationDelay = (i * 50 + j * 120) + 'ms';
      r.append(svg('title', {}, `${se.name ? se.name + ' · ' : ''}${labels[i]}: ${v}${s.unit || ''}`));
      root.append(r);
    }));
  }
  w.append(root);
  if (series.length > 1 || series[0].name) {
    const lg = el('div', 'legend');
    series.forEach((se) => { const it = el('span'); const sw = el('i'); sw.style.background = se.color; it.append(sw, se.name); lg.append(it); });
    w.append(lg);
  }
  return w;
}

// ------------------------------------------------------------------ open / close
let current = null;          // { show, id }
const shown = [];            // older + held shows, newest first: { show, held, at }
let seq = 0;

function remember(show, held = false) {
  const i = shown.findIndex((x) => x.show.kind === show.kind && x.show.src === show.src && x.show.title === show.title);
  if (i >= 0) shown.splice(i, 1);
  shown.unshift({ show, held, at: Date.now() });
  shown.length = Math.min(shown.length, MAX_SHOWN);
  renderShown();
  OG.emit('stage-shown', { list: shown.map((x) => ({ ...x.show, held: x.held })) });
}
function renderShown() {
  shownMenu.replaceChildren(el('b', null, 'shown earlier'));
  shownBtn.hidden = !shown.length;
  shownBtn.textContent = (shown.some((x) => x.held) ? 'earlier • ' : 'earlier ') + '▾';
  shown.forEach((x, i) => {
    const b = el('button', x.held ? 'held' : '');
    b.type = 'button';
    b.setAttribute('role', 'menuitem');
    b.append(el('small', null, x.held ? 'waiting' : x.show.kind), x.show.title || x.show.src || x.show.kind);
    b.onclick = () => { shownMenu.hidden = true; shown.splice(i, 1); open(x.show, { asked: true }); };
    shownMenu.append(b);
  });
}
function announce() {
  const title = current.show.title || tEl.textContent;
  OG.emit('stage', { open: true, title, kind: current.show.kind, src: current.show.src || null, about: 'about: ' + title, id: current.id });
}

function normalize(show) {
  if (!show || typeof show !== 'object') throw new Error('stage.open needs a show {kind, src, title}');
  const kind = String(show.kind || '').toLowerCase();
  if (!KINDS.includes(kind)) throw new Error('unknown show kind: ' + show.kind);
  return { ...show, kind, title: String(show.title || '').slice(0, 140), why: show.why ? String(show.why).slice(0, 160) : '' };
}

export function open(raw, { asked = true } = {}) {
  const show = normalize(raw);
  const urgent = !!show.urgent;
  // L6: asked, or urgent. Never over a line the user is typing unless he asked for it in that line.
  if ((!asked && !urgent) || (!asked && typing())) {
    remember(show, true);
    log({ op: 'held', kind: show.kind, title: show.title, urgent });
    OG.emit('stage-held', { show });
    return false;
  }
  const wasOpen = !!current;
  if (current) remember(current.show);
  current = { show, id: ++seq };
  const ttl = show.title || (show.kind === 'article' ? 'reading…' : show.kind);
  kEl.textContent = show.kind === 'url' && !ownUrl(show.src) ? 'article' : show.kind;
  tEl.textContent = ttl;
  wEl.textContent = show.kind === 'article' || (show.kind === 'url' && !ownUrl(show.src)) ? '' : (show.why || '');
  const link = show.kind === 'url' || show.kind === 'article' ? String(show.src || '') : show.kind === 'image' ? safeImg(show.src) : null;
  orig.hidden = !link;
  if (link) { orig.href = link; orig.textContent = show.kind === 'url' && ownUrl(show.src) ? 'open ↗' : 'original ↗'; }
  shownMenu.hidden = true;
  let node;
  try { node = R[show.kind](show); } catch (e) { node = msg('that one didn’t render: ' + (e.message || e)); }
  bd.replaceChildren(node);
  bd.scrollTop = 0;
  panel.classList.add('on');
  scrim.classList.add('on');
  document.body.classList.add('og-staged');
  log({ op: 'open', kind: show.kind, title: show.title, src: show.kind === 'html' ? '(inline)' : String(show.src || '').slice(0, 300), reason: asked ? 'asked' : 'urgent' });
  announce();
  if (REDUCED) panel.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 250 });
  else if (!wasOpen) {
    const base = isMobile() ? '' : 'translateX(-50%) ';
    panel.animate([{ transform: base + 'translateY(70px) scale(.94)', opacity: 0 }, { transform: base + 'none', opacity: 1 }], { duration: 520, easing: SPRING });
  } else bd.animate([{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 380, easing: SPRING });
  setTimeout(() => { try { panel.focus({ preventScroll: true }); } catch (e) { /* ignore */ } }, 30);
  return true;
}

export async function close() {
  if (!current) return false;
  const was = current;
  remember(was.show);
  current = null;
  scrim.classList.remove('on');
  document.body.classList.remove('og-staged');
  shownMenu.hidden = true;
  if (!REDUCED) {
    const base = isMobile() ? '' : 'translateX(-50%) ';
    try { await panel.animate([{ transform: base + 'none', opacity: 1 }, { transform: base + 'translateY(60px) scale(.96)', opacity: 0 }], { duration: 300, easing: EASE }).finished; } catch (e) { /* ignore */ }
  }
  if (!current) { panel.classList.remove('on'); bd.replaceChildren(); }
  OG.emit('stage', { open: false, title: null, about: null, id: was.id });
  return true;
}

// ------------------------------------------------------------------ dismiss: Esc, ✕, backdrop, swipe
xBtn.onclick = () => close();
scrim.addEventListener('pointerdown', (e) => { e.preventDefault(); close(); });
shownBtn.onclick = (e) => { e.stopPropagation(); shownMenu.hidden = !shownMenu.hidden; };
document.addEventListener('pointerdown', (e) => { if (!shownMenu.hidden && !e.target.closest('.og-stage-shown-m, .og-stage-shown-b')) shownMenu.hidden = true; });
// capture phase, so the canvas's own Esc (close bloom, leave the canvas) waits for the stage
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !current) return;
  if (document.querySelector('.og-ask-scrim')) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  if (!shownMenu.hidden) { shownMenu.hidden = true; return; }
  close();
}, true);
let sy = null, st = 0;
panel.addEventListener('touchstart', (e) => {
  const inBody = bd.contains(e.target);
  sy = (!inBody || bd.scrollTop <= 0) ? e.touches[0].clientY : null;
  st = Date.now();
}, { passive: true });
panel.addEventListener('touchmove', (e) => {
  if (sy == null) return;
  const dy = e.touches[0].clientY - sy;
  if (dy > 0) panel.style.translate = `0 ${Math.min(dy, 400) * 0.6}px`;
}, { passive: true });
panel.addEventListener('touchend', (e) => {
  if (sy == null) return;
  const dy = e.changedTouches[0].clientY - sy;
  panel.style.translate = '';
  if (dy > 110 || (dy > 50 && Date.now() - st < 250)) close();
  sy = null;
}, { passive: true });

const stage = {
  open, close, chart,
  get current() { return current ? { ...current.show } : null; },
  get shown() { return shown.map((x) => ({ ...x.show, held: x.held })); },
  isOpen: () => !!current,
};
OG.stage = stage;
if (window.OneGuy && window.OneGuy !== OG) window.OneGuy.stage = stage;
export default stage;
