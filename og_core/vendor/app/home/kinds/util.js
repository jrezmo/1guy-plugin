// Shared helpers for the kind modules (WS-A2). Nothing here touches globals
// except reading window.OneGuy (the contract API).

// ---------- safe HTML ----------
// html`...` escapes every interpolated value unless it is itself html`` / raw.
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s));

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '`': '&#96;' };
export const esc = (s) => String(s ?? '').replace(/[&<>"'`]/g, (c) => ESC[c]);

function fmt(v) {
  if (v == null || v === false) return '';
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(fmt).join('');
  return esc(v);
}
export function html(strings, ...vals) {
  let out = '';
  strings.forEach((s, i) => { out += s; if (i < vals.length) out += fmt(vals[i]); });
  return new Raw(out);
}
export const mount = (el, h) => { el.innerHTML = String(h); return el; };

// Only same-origin paths, fragments and http(s)/mailto URLs survive.
export function safeUrl(u) {
  const s = String(u ?? '').trim();
  if (!s) return '';
  if (/^(https?:\/\/|mailto:|\/(?!\/)|#|\.\.?\/)/i.test(s)) return s;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(s) && !s.startsWith('//')) return s; // plain relative
  return '';
}

// ---------- the API (contract only) ----------
export const OG = () => window.OneGuy;
export const boardBlock = (id) => {
  const b = OG()?.board;
  if (!b) return null;
  return (b.byId && b.byId[id]) || (b.blocks || []).find((x) => x.id === id) || null;
};
export const projectMeta = (pid) => (OG()?.board?.projects || []).find((p) => p.id === pid) || null;

// ---------- time ----------
// "now" for relative labels. On the fixture board use its generated_at, so the
// fixture always reads like it did when it was written.
export function nowMs() {
  const b = OG()?.board;
  if (b?.fixture && b.generated_at) { const t = Date.parse(b.generated_at); if (!isNaN(t)) return t; }
  return Date.now();
}
// Calendar "today" in the user's wall-clock: the date part of the board's generated_at
// (the server writes local offsets). Falls back to the browser's local date.
function todayYMD() {
  const b = OG()?.board;
  const g = b?.generated_at && /^(\d{4}-\d{2}-\d{2})/.exec(b.generated_at);
  if (g && b.fixture) return g[1];
  const d = new Date(nowMs());
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
const ymdNum = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s || ''); return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) / 864e5 : null; };
export function dayDiff(iso) {
  const a = ymdNum(iso), b = ymdNum(todayYMD());
  return a == null || b == null ? null : Math.round(a - b);
}
// wall-clock HH:MM exactly as written in the ISO string (the user's local offset)
export function clock(iso) {
  const m = /T(\d{2}):(\d{2})/.exec(iso || '');
  return m ? `${m[1]}:${m[2]}` : '';
}
const MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
export function dayLabel(iso) {
  const d = dayDiff(iso);
  if (d == null) return '';
  if (d === 0) return 'today';
  if (d === -1) return 'yesterday';
  if (d === 1) return 'tomorrow';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  const y = m[1] !== todayYMD().slice(0, 4) ? ` ${m[1]}` : '';
  return `${MON[+m[2] - 1]} ${+m[3]}${y}`;
}
export function ago(iso) {
  const t = Date.parse(iso || '');
  if (isNaN(t)) return '';
  const s = (nowMs() - t) / 1000;
  if (s < 60) return '<1m';
  if (s < 3600) return `${Math.round(s / 60)}\u00a0min ago`;
  if (s < 86400) return `${Math.round(s / 3600)}\u00a0h ago`;
  const d = Math.round(s / 86400);
  if (d === 1) return 'yesterday';
  if (d < 45) return `${d}\u00a0days ago`;
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  return m ? `${MON[+m[2] - 1]} ${m[1]}` : `${d} days ago`;
}
export function until(iso) {
  const t = Date.parse(iso || '');
  if (isNaN(t)) return '';
  const s = (t - nowMs()) / 1000;
  if (s < 0) return 'overdue';
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400 && dayDiff(iso) === 0) return `in ${Math.round(s / 3600)} h`;
  return `${dayLabel(iso)} ${clock(iso)}`.trim();
}
export const minutesSince = (iso) => { const t = Date.parse(iso || ''); return isNaN(t) ? Infinity : (nowMs() - t) / 60000; };
export function dueText(due) {
  const d = dayDiff(due);
  if (d == null) return null;
  if (d < -1) return { text: `${-d} days overdue`, tone: 'late' };
  if (d === -1) return { text: 'due yesterday', tone: 'late' };
  if (d === 0) return { text: 'due today', tone: 'soon' };
  if (d === 1) return { text: 'due tomorrow', tone: 'soon' };
  return { text: `due in ${d} days`, tone: d <= 3 ? 'soon' : 'far' };
}
export function duration(sec) {
  const s = Math.round(+sec || 0);
  if (!s) return '';
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// ---------- cron → words ----------
const DOW = { '1-5': 'weekdays', '0,6': 'weekends', '6,0': 'weekends', '0': 'sundays', '1': 'mondays', '2': 'tuesdays', '3': 'wednesdays', '4': 'thursdays', '5': 'fridays', '6': 'saturdays' };
export function cronWords(expr, short = false) {
  const f = String(expr || '').trim().split(/\s+/);
  if (f.length !== 5) return expr || '';
  const [mi, hr, dom, mon, dow] = f;
  const list = (x) => (/^\d+(,\d+)*$/.test(x) ? x.split(',').map(Number) : null);
  const mins = list(mi), hrs = list(hr);
  if (/^\*\/\d+$/.test(mi) && hr === '*') return `every ${mi.slice(2)} min`;
  if (mins && hr === '*') return `hourly at :${String(mins[0]).padStart(2, '0')}`;
  if (!mins || !hrs || dom !== '*' || mon !== '*') return expr;
  const times = [];
  hrs.forEach((h) => mins.forEach((m) => times.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`)));
  const when = dow === '*' ? 'daily' : (DOW[dow] || `days ${dow}`);
  return times.length === 1 ? `${when} ${times[0]}` : short ? `${times.length}× ${when}` : `${times.length}× ${when} · ${times.join(' · ')}`;
}

// ---------- status ----------
export const STATUS = {
  'needs-you': 'needs you', active: 'moving', waiting: 'waiting', idle: 'idle',
  done: 'done', parked: 'parked', proposed: 'suggested', failed: 'failed',
};
export const statusLabel = (s) => STATUS[s] || s || '';

const has = (v) => v != null && v !== false && String(v).trim() !== '';
// block data → a safe CSS class token
export const tok = (s, d = 'idle') => String(s ?? '').toLowerCase().replace(/[^a-z0-9-]/g, '') || d;

// ---------- shared face skeleton ----------
// One DOM for every size; kinds.css shows/hides parts by [data-size] on the card
// element, so the shell can resize a card without re-calling card.
export function face(block, el, o) {
  // mark the card element (classes only, prefixed kc-) so kinds.css can theme it
  [...el.classList].filter((c) => c.startsWith('kc-') || c === 'kc').forEach((c) => el.classList.remove(c));
  el.classList.add('kc', `kc-${tok(block.kind, 'x')}`, `kc-st-${tok(block.status)}`);
  if (!el.dataset.size) el.dataset.size = 'card';
  const st = o.status ?? html`<span class="kf-st st-${tok(block.status)}"><i class="sdot"></i><span>${statusLabel(block.status)}</span></span>`;
  return mount(el, html`<div class="kf kf-${block.kind} st-${tok(block.status)}" data-kf>
  <div class="kf-top"><span class="kf-kind"><i class="kf-ico" aria-hidden="true">${o.icon}</i><span class="kf-kl">${o.kindLabel ?? block.kind}</span></span>${st}</div>
  <div class="kf-main">${o.lead || ''}<div class="kf-text">
    <div class="kf-title"><i class="kf-tico" aria-hidden="true">${o.icon}</i><span class="kf-tt">${o.title ?? block.title}</span><i class="kf-cdot sdot" aria-hidden="true"></i></div>
    ${has(o.line) ? html`<div class="kf-line ${o.prose ? 'prose' : ''}">${o.line}</div>` : ''}
    ${has(o.extra) ? html`<div class="kf-extra">${o.extra}</div>` : ''}
  </div>${o.trail || ''}</div>
  ${has(o.foot) ? html`<div class="kf-foot">${o.foot}</div>` : ''}
</div>`);
}
// " · "-joined meta, skipping blanks; values may be html``.
export const dots = (...parts) => {
  const p = parts.flat().filter((x) => x != null && x !== '' && x !== false && x !== 0);
  return html`${p.map((x, i) => (i ? html`<span class="sep"> · </span>${x}` : x))}`;
};

// ---------- shared bloom bits ----------
export const sec = (label, body, cls = '') => html`<section class="kb-sec ${cls}"><h4 class="kb-h">${label}</h4>${body}</section>`;
export const kv = (rows) => html`<dl class="kb-kv">${rows.filter((r) => r && r[1] != null && r[1] !== '').map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>`;

// kind → glyph, filled by index.js at registration (module state, not a global)
export const ICONS = {};
const ICON = () => ICONS;
export function miniCard(id) {
  const b = boardBlock(id);
  if (!b) {
    // not a block (e.g. a project that only exists in board.projects): a quiet, non-clickable label
    const p = projectMeta(id);
    const c = /^#[0-9a-f]{3,8}$/i.test(p?.color || '') ? p.color : '';
    return html`<span class="kb-mini missing" title="${id}"><i class="kb-mini-ico" ${c ? html`style="color:${c}"` : ''}>◆</i><span class="kb-mini-t">${p?.title || String(id).replace(/^[a-z]+:/, '')}</span></span>`;
  }
  return html`<button type="button" class="kb-mini lane st-${tok(b.status)}" data-focus="${b.id}" title="open ${b.title}">
      <span class="kb-mini-k"><i class="kb-mini-ico" aria-hidden="true">${ICON()[b.kind] || '•'}</i>${b.kind} · ${statusLabel(b.status)}</span><span class="kb-mini-t">${b.title}</span></button>`;
}
// Wire [data-focus] buttons inside a bloom body.
export function wireFocus(root) {
  root.querySelectorAll('[data-focus]').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    OG()?.focus?.(b.dataset.focus);
  }));
}
export function bloomRoot(block, bodyEl, inner) {
  mount(bodyEl, html`<div class="kb kb-${block.kind} st-${tok(block.status)}">${inner}</div>`);
  const root = bodyEl.firstElementChild;
  // staggered unfold (CSS reads --i)
  [...root.querySelectorAll(':scope > .kb-sec, :scope > .kb-grid > *, :scope > .kb-lead')].forEach((n, i) => n.style.setProperty('--i', i));
  wireFocus(root);
  return root;
}
export function srcLine(block) {
  const s = block.source || {};
  const u = safeUrl(s.url);
  const label = [s.type, s.ref].filter(Boolean).join(' · ');
  return u ? html`<a class="kb-a" href="${u}" target="_blank" rel="noopener noreferrer">${label || u}</a>` : html`<span class="mono">${label}</span>`;
}
export const tagChips = (tags) => (tags || []).length ? html`<span class="kb-tags">${tags.map((t) => html`<span class="kb-tag">#${t}</span>`)}</span>` : '';

// =====================================================================
// v1.1 (WS-K): plain words. Faces and bloom tops read block.headline
// ({title, why, ask:{question, options:[{label, say}]}, first_message}),
// written by the one guy. Without one, we fall back to a cleaned, cut title.
// =====================================================================

// Strip the machine bits a person shouldn't have to read: urls, paths,
// provider/model ids, session ids, hashes, #tags, tasklist tokens.
export function plain(s) {
  let t = String(s ?? '');
  t = t.replace(/\(from:[^)]*\)/gi, ' ');
  t = t.replace(/\bhttps?:\/\/\S+/gi, ' ');
  t = t.replace(/\b\d{8}_\d{6}_[0-9a-f]{4,}\b/gi, ' ');                       // session ids
  t = t.replace(/\b(?:api|bg|cron|deleg|sess)?_?\d{6,13}_[0-9a-f]{4,}\b/gi, ' ');  // session ids, epoch form (api_1793805665_930712dc)
  t = t.replace(/(?:~|\.{1,2})?\/?(?:[\w.@-]+\/)+[\w.@-]*/g, ' ');              // paths, a/b ids, branches
  t = t.replace(/\b(?:claude|glm|gpt|gemini|llama|qwen|mistral|kimi|deepseek|opus|sonnet|haiku)-[\w.-]*\d[\w.-]*/gi, ' ');
  t = t.replace(/\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b/gi, ' ');   // hashes / job ids
  t = t.replace(/(^|[\s(])[#!@][\w-]+/g, '$1');                                // #tags, !p1, @dates
  t = t.replace(/\(\s*[,;:·]?\s*\)/g, ' ');
  t = t.replace(/\s+/g, ' ').replace(/\s+([,.;:?!)])/g, '$1').replace(/\(\s+/g, '(');
  t = t.replace(/([,;:])(?=[,;:.?!])/g, '').replace(/\s+(or|and|to|on|of)\s+(?=[,.;:?!]|$)/gi, ' ');
  return t.replace(/^[\s,;:·—–-]+|[\s,;:·—–-]+$/g, '').trim();
}
const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
export const same = (a, b) => !!norm(a) && norm(a) === norm(b);
export function cutWords(s, n) {
  const w = String(s ?? '').trim().split(/\s+/).filter(Boolean);
  if (w.length <= n) return w.join(' ');
  const keep = w.slice(0, n);
  while (keep.length > 2 && STOP.has(keep[keep.length - 1].toLowerCase().replace(/[^a-z]/g, ''))) keep.pop();
  return keep.join(' ').replace(/[\s,;:·—–(=+-]+$/, '') + '…';
}
const STOP = new Set(['on', 'of', 'to', 'the', 'a', 'an', 'and', 'or', 'as', 'in', 'for', 'with', 'at', 'by', 'from', 'into']);
const MODEL = /\b(?:claude|glm|gpt|gemini|llama|qwen|mistral|kimi|deepseek|opus|sonnet|haiku)-[\w.-]*\d/i;
// Sentences that read fine once cleaned: drop any that name a model id (they'd
// come out with holes in them), keep the rest.
function plainSentences(s) {
  return String(s ?? '').split(/(?<=[?.!])\s+|\s[—–]\s/).filter((c) => !MODEL.test(c)).map(plain)
    .filter(Boolean).filter((c) => !/\b[A-Z]{3,}\b/.test(c)).join(' ');   // all-caps fragments (DELETE <id>, POST …) are machine speech
}
// the first clause: up to a dash or the end of the first sentence
function clause(s) {
  const parts = String(s).split(/\s[—–]\s|\s-\s|(?<=[?.!])\s+(?=[A-Za-z])/);
  return parts[0].split(/\s+/).length >= 2 ? parts[0] : String(s);
}
// a trailing "…?" clause in a raw title ("… — keep or record?") as a plain question
export function questionOf(raw) {
  const m = /\s[—–]\s([^—–]+\?)\s*$/.exec(String(raw ?? ''));
  const q = m && !MODEL.test(m[1]) && plain(m[1]);
  return q && q.split(/\s+/).length >= 2 ? q : '';
}

export function headline(block) {
  const h = block?.headline && typeof block.headline === 'object' ? block.headline : {};
  const rawTitle = plain(block?.title) || String(block?.title ?? '').trim();
  const ht = plain(h.title);
  // a face never wants a parenthetical: "archive the session (title "subagent: ...") once…"
  let tClause = clause(rawTitle);
  const paren = tClause.indexOf('(');
  if (paren > 8) tClause = tClause.slice(0, paren);
  const title = ht || cutWords(tClause, 8) || rawTitle || '';
  let why = plain(h.why);
  let whyFull = '';
  if (!ht && !why) {
    // fallback: the summary, minus whatever of it the title already says
    const rt = String(block?.title ?? ''), rs = String(block?.summary ?? '');
    const c = clause(rt);
    let src = rs;
    if (norm(rs).startsWith(norm(rt))) src = rs.slice(rt.length);
    else if (norm(rs).startsWith(norm(c))) src = rs.slice(c.length);
    let w = plainSentences(src);
    if (!w && rt.length > c.length + 3) w = plainSentences(rt.slice(c.length));   // the rest of the title
    if (norm(w).startsWith(norm(title.replace(/…$/, '')))) w = '';
    why = cutWords(w, 18);
    whyFull = cutWords(w, 48);
  }
  if (same(why, title)) why = '';
  let ask = null;
  const opts = (h.ask && Array.isArray(h.ask.options) ? h.ask.options : [])
    .filter((o) => o && typeof o === 'object' && String(o.label ?? '').trim())
    .slice(0, 3);
  if (opts.length) {
    const q = plain(h.ask.question);
    // a title that's already a question doesn't need a second one
    ask = { question: same(q, title) || same(q, why) || /\?\s*$/.test(title) ? '' : q, options: opts };
  }
  // the bloom has room: without a headline it shows the whole cleaned title
  const full = ht || cutWords(rawTitle, 28) || title;
  return { title, why, ask, first_message: h.first_message || '', has: !!ht, rawTitle, full, whyFull: same(whyFull, title) ? '' : whyFull };
}

// ---------- the 'for you' face (el.dataset.view === 'briefing') ----------
export const isBriefing = (el) => el?.dataset?.view === 'briefing';
export function briefFace(block, el, o = {}) {
  [...el.classList].filter((c) => c.startsWith('kc-') || c === 'kc').forEach((c) => el.classList.remove(c));
  el.classList.add('kc', 'kc-brief', `kc-${tok(block.kind, 'x')}`, `kc-st-${tok(block.status)}`);
  if (!el.dataset.size) el.dataset.size = 'hero';
  const H = headline(block);
  const kicker = H.ask ? 'a quick call' : o.kicker;
  mount(el, html`<div class="kf kfb kf-${tok(block.kind, 'x')} st-${tok(block.status)}" data-kf>
  <div class="kfb-top"><span class="kfb-k"><i class="kf-ico" aria-hidden="true">${o.icon}</i><span>${kicker}</span></span>${o.touch || ''}</div>
  ${o.media || ''}
  <div class="kfb-title kf-tt">${H.title}</div>
  ${H.why ? html`<p class="kfb-why">${H.why}</p>` : ''}
</div>`);
  return el;
}

// ---------- the bloom ----------
// A kind's bloom is data: sections of content. focus.js owns the header, the connections
// strip and every control. `details` marks what goes under "behind this".
export function details(inner) {
  return html`<div class="kb-behind">${inner}</div>`;
}
export function bloomV11(block, bodyEl, inner) {
  const root = bloomRoot(block, bodyEl, inner);
  root.classList.add('kb-v11');
  return root;
}

// ---------- live work (phase3): one plain status line for a card being worked on ----------
// ev = an /api/feed event: {status, now, started, last_move, session_id, deep_link, ...}.
// 'working: replying · 2m'. Never an id, a path or a model name on the face.
export function since(sec) {
  const s = Math.max(0, Math.round(+sec || 0));
  if (s < 60) return '<1m';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
export function liveDoing(ev) {
  let d = plain(String(ev?.now ?? '')).replace(/\s*\(.*$/, '').replace(/[`*_]/g, '').trim();
  if (!d || d.length > 40 || /[\\/]|\b[0-9a-f]{8,}\b/i.test(d)) d = 'on it';
  return d;
}
export function liveText(ev, now = Date.now() / 1000) {
  if (!ev) return '';
  const t = since(now - (ev.started || now));
  if (ev.status === 'quiet') return `gone quiet · ${since(now - (ev.last_move || now))}`;
  return `working: ${liveDoing(ev)} · ${t}`;
}
// the line's DOM; live.js mounts it on any card whose key has a live event
export function liveLine(ev, now) {
  return html`<span class="og-live-dot" aria-hidden="true"></span><span class="og-live-t">${liveText(ev, now)}</span>${
    ev && ev.session_id ? html`<a class="og-live-open" href="${'hermes://open/' + encodeURIComponent(ev.session_id)}" data-sid="${ev.session_id}">open session ↗</a>` : ''}`;
}
