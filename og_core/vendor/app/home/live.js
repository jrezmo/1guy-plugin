// 1guy home · live work you can see (phase3). Polls the one activity feed (/api/feed?live=1)
// every ~5s and makes any card whose item key has a live event breathe: a teal edge plus one
// plain line ('working: replying · 2m') and an 'open session ↗' link. No board refetch for
// this; when a card's live work ENDS the board is re-pulled once, quietly, so the finished
// state shows. The header's calm 'N working' links to the runs page (no ticker: A4 L4).
import OneGuy, { FIXTURE, $ } from './core.js';
import { html, since } from './kinds/util.js';

const POLL_LIVE_MS = 5000, POLL_IDLE_MS = 10000;
const CARD_SEL = '.og-card[data-id], .og-fy[data-id]';
let live = new Map();      // board id -> event
let working = 0;
let timer = 0, raf = 0;

function keyMap(d) {
  const m = new Map();
  for (const [k, ev] of Object.entries(d.live || {})) m.set(k, ev);
  // a session block on the board is the same work as its session's live event
  for (const ev of d.events || []) if (ev.live && ev.session_id && !m.has('session:' + ev.session_id)) m.set('session:' + ev.session_id, ev);
  return m;
}

// What's moving on an item, as ONE plain line (board/world.py; card-grammar §6). The board's
// `world` line knows the most (a bot asking Priya, a watcher's conflict); the feed's live event is
// fresher for "he's on it", so between board polls it can start, end or re-time that line.
const WHO = { subagent: 'a helper', surface: 'he', run: 'a tasklist run', cron: 'the routine' };
function worldOf(id, ev, now, blk) {
  const w = (blk || OneGuy.board?.byId?.[id])?.world || null;
  if (w && w.src === 'item') return w;
  if (ev) {
    const who = WHO[ev.actor] || 'he';
    if (ev.status === 'quiet') return { line: `${who} went quiet`, kind: 'quiet', since: ev.last_move || ev.started, session_id: ev.session_id };
    return { line: who === 'he' ? "he's on it" : `${who} is on it`, kind: 'running', since: ev.started, session_id: ev.session_id };
  }
  return w && w.src === 'feed' ? null : w;     // the feed says that work ended: drop the stale line
}
export function worldText(w, now = Date.now() / 1000) {
  if (!w || !w.line) return '';
  const aged = w.since && ['running', 'quiet', 'waiting', 'waiting-on-you'].includes(w.kind);
  return aged ? `${w.line} · ${since(now - w.since)}` : w.line;
}

// canvas.js calls this while it renders a face, so the line is in flow before the layout measures
// the card (it can wrap in a narrow day column); polls then only re-time it in place.
export function worldMark(b, node) {
  decorate(node, live.get(b.id), Date.now() / 1000, b);
  node.querySelector(':scope > .og-world')?.classList.add('og-world-flow');
}

function decorate(node, ev, now, blk) {
  const w = worldOf(node.dataset.id || blk?.id, ev, now, blk);
  let line = node.querySelector(':scope > .og-live-line');
  const running = !!w && (w.kind === 'running' || w.kind === 'quiet') && !!ev;
  node.classList.toggle('og-live', running);
  node.classList.toggle('og-live-quiet', running && w.kind === 'quiet');
  if (!running) node.querySelector(':scope > .og-live-edge')?.remove();
  else if (!node.querySelector(':scope > .og-live-edge')) {
    const edge = document.createElement('i');
    edge.className = 'og-live-edge';
    edge.setAttribute('aria-hidden', 'true');
    node.prepend(edge);
  }
  if (!w) { line?.remove(); delete node.dataset.world; return; }
  node.dataset.world = w.kind || 'running';
  const sig = worldText(w, now) + '|' + (w.session_id || '');
  if (line && line.dataset.sig === sig) return;
  if (!line) {
    line = document.createElement('div');
    line.className = 'og-live-line og-world';
    line.setAttribute('role', 'status');
    node.append(line);
  }
  line.dataset.sig = sig;
  line.dataset.kind = w.kind || 'running';
  line.title = w.who ? `${w.line} (${w.who})` : w.line;
  // a face has ONE button (card grammar): the session is a connection chip in focus, not a link here
  line.innerHTML = String(html`<span class="og-live-dot" aria-hidden="true"></span><span class="og-live-t">${worldText(w, now)}</span>`);
}

// The claim frame (frontier 4): a quiet outline and one small -ing word on the cards being worked
// on. A turn the user just sent wins (surface.js claim: its verb, × to stop). Otherwise a card that is
// running wears its job's word (board/claim.py puts it on the block) when that word says more than
// the status line under it already does; plain 'working' is left to that line.
function tagOn(node, label, state, x) {
  let tag = node.querySelector(':scope > .og-claim-tag');
  node.classList.toggle('og-claim', !!label);
  if (!label) { tag?.remove(); return; }
  const sig = `${label}|${state}|${x ? 1 : 0}`;
  if (tag && tag.dataset.sig === sig) return;
  if (!tag) {
    tag = document.createElement('span');
    tag.className = 'og-claim-tag';
    tag.setAttribute('role', 'status');
    node.append(tag);
  }
  tag.dataset.sig = sig;
  tag.dataset.state = state;
  const t = document.createElement('span');
  t.className = 'og-claim-t';
  t.textContent = label;
  tag.replaceChildren(t);
  if (x) {
    const b = document.createElement('span');     // a span, not a button: a fold is itself a button
    b.className = 'og-claim-x';
    b.setAttribute('role', 'button');
    b.setAttribute('aria-label', 'stop');
    b.title = 'stop (Esc)';
    b.textContent = '×';
    tag.append(b);
  }
}
function claimMark(node, onPile) {
  const id = node.dataset.id;
  const c = onPile ? null : OneGuy.surface?.claim?.of(id);
  if (c) { tagOn(node, c.label, c.state, c.state === 'open'); return; }
  const job = node.classList.contains('og-live') && !node.classList.contains('og-live-quiet') ? OneGuy.board?.byId?.[id]?.claim : null;
  tagOn(node, job && job.verb !== 'working' ? job.label : '', 'job', false);
}
const FRAME_SEL = CARD_SEL + ', .og-bloom[data-id]';
const PILE_SEL = '.og-fold[data-cluster], .og-place[data-cluster]';
const shown = (n) => !!n && !n.classList.contains('og-src-hidden') && n.getClientRects().length > 0
  && (!n.checkVisibility || n.checkVisibility({ visibilityProperty: true }));
function claims() {
  const c = OneGuy.surface?.claim?.now();
  const on = new Set();
  // work on a whole pile (a brief of it): one frame round the pile, not one on every card in it
  const reg = c && !c.over && c.pile ? document.querySelector(`.og-region[data-cluster="${CSS.escape(c.pile)}"]`) : null;
  const whole = shown(reg) ? reg : null;
  for (const r of document.querySelectorAll('.og-region[data-cluster]')) tagOn(r, r === whole ? c.label : '', r === whole ? c.state : '', r === whole && c.state === 'open');
  for (const node of document.querySelectorAll(FRAME_SEL)) {
    claimMark(node, !!whole);
    if (node.classList.contains('og-claim') && shown(node)) on.add(node.dataset.id);
  }
  // a claimed card that isn't on screen itself (tucked in a fold, or inside a place on today):
  // the frame sits on its pile instead
  const piles = new Set();
  if (c && !c.over && !whole && OneGuy.pileOf) for (const id of c.ids) { if (!on.has(id)) { const p = OneGuy.pileOf(id); if (p) piles.add(p); } }
  for (const fold of document.querySelectorAll(PILE_SEL)) {
    const mine = piles.has(fold.dataset.cluster);
    tagOn(fold, mine ? c.label : '', mine ? c.state : '', mine && c.state === 'open');
  }
}

function paint() {
  raf = 0;
  const now = Date.now() / 1000;
  for (const node of document.querySelectorAll(CARD_SEL)) decorate(node, live.get(node.dataset.id), now);
  claims();
  header();
}
const schedule = () => { if (!raf) raf = requestAnimationFrame(paint); };

function header() {
  const box = $('#og-sum');
  if (!box) return;
  let a = box.querySelector('a.og-working');
  if (!working) { a?.remove(); return; }
  if (!a) {
    a = document.createElement('a');
    a.className = 'og-working';
    a.href = OneGuy.api.url('/app/runs.html');
    a.title = 'see everything being worked on';
    box.append(a);
  }
  const t = `${working} working ↗`;
  if (a.textContent !== t) a.textContent = t;
}

async function poll() {
  clearTimeout(timer);
  if (document.hidden) { timer = setTimeout(poll, POLL_IDLE_MS); return; }
  try {
    const r = await OneGuy.api('/api/feed?live=1', { cache: 'no-store' });
    if (r.ok) {
      const d = await r.json();
      const next = keyMap(d);
      const ended = [...live.keys()].filter((k) => !next.has(k) && OneGuy.board?.byId?.[k]);
      live = next;
      working = d.working || 0;
      schedule();
      // a card's work just finished: look again once, quietly, so its new state shows
      if (ended.length) (OneGuy._hooks?.refresh || OneGuy.refresh)?.({ quiet: true });
      OneGuy.emit?.('live', { live, working });
    }
  } catch (e) { /* the feed is best-effort; the board still works without it */ }
  timer = setTimeout(poll, live.size ? POLL_LIVE_MS : POLL_IDLE_MS);
}

// open session ↗ goes through the door's own opener (pane bridge / iOS / hermes://) and never
// counts as a tap on the card underneath
for (const t of ['pointerdown', 'pointerup', 'mousedown', 'touchstart']) {
  document.addEventListener(t, (e) => { if (e.target.closest?.('.og-live-open, .og-claim-x')) e.stopPropagation(); }, true);
}
// the frame's × stops the turn (same as Esc), and is never a tap on the card or pile under it
document.addEventListener('click', (e) => {
  if (!e.target.closest?.('.og-claim-x')) return;
  e.preventDefault();
  e.stopPropagation();
  OneGuy.surface?.claim?.now()?.cancel();
}, true);
// the frame follows its card into focus and across lenses (the bloom mounts a beat after 'focus')
for (const ev of ['focus', 'bloom-open', 'bloom-close', 'lens', 'view', 'level']) OneGuy.on?.(ev, () => { schedule(); setTimeout(schedule, 420); });
// a claim changed: redraw; when its turn landed, the framed cards take the land ring
OneGuy.on?.('claim', ({ landed } = {}) => {
  schedule();
  for (const id of landed || []) {
    for (const node of document.querySelectorAll(`.og-card[data-id="${CSS.escape(id)}"], .og-fy[data-id="${CSS.escape(id)}"]`)) {
      node.classList.remove('og-landed'); void node.offsetWidth; node.classList.add('og-landed');
      setTimeout(() => node.classList.remove('og-landed'), 1400);
    }
  }
});
document.addEventListener('click', (e) => {
  const a = e.target.closest?.('.og-live-open');
  if (!a) return;
  e.preventDefault();
  e.stopPropagation();
  OneGuy.openSession?.(a.dataset.sid);
}, true);

// cards are re-rendered by the canvas and the briefing: re-apply after any change to them
const mo = new MutationObserver((muts) => {
  if (muts.some((m) => [...m.addedNodes, ...m.removedNodes].some((n) => n.nodeType === 1 && !n.closest?.('.og-claim-tag') && !n.classList?.contains('og-live-line') && !n.classList?.contains('og-live-edge') && !(n.parentElement?.classList?.contains('og-live-line'))))) schedule();
});
for (const sel of ['#og-cards', '#og-brief', '#og-sum']) { const n = $(sel); if (n) mo.observe(n, { childList: true, subtree: true }); }
OneGuy.on?.('board', schedule);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
OneGuy.live = { get: (id) => live.get(id) || null, poll };

if (!FIXTURE) poll();
