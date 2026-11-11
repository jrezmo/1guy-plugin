// 1guy home · main (WS-A1; v1.1 WS-S). Wires the shell: OneGuy (core) → orb, rail, canvas,
// gestures, the briefing; loads A2/K's kinds, F/T's surface + talk (each wrapped so a failure
// never breaks the page); loads the board (fixture or live), plays the entrance, polls.
// Two views: 'briefing' (the default: his line, ≤3 for-you cards, project tiles) and
// 'canvas' (show everything: the v1 workbench). Once the user has placed cards on the canvas, the
// view he was last in is remembered.
import OneGuy, { FIXTURE, FIXTURE_URL, PANE, NS, params, store, api, el, $, sleep, words, SENT, REDUCED, EASE_OUT } from './core.js';
import { sampleChip } from './doors.js';
import { createBriefing } from './briefing.js';
import { createOrb } from './orb.js';
import { createRail } from './rail.js';
import { createCanvas, score } from './canvas.js';
import { attachGestures } from './gestures.js';

const isMobile = () => window.innerWidth <= 700;
let railCollapsed = true;
const railW = () => (isMobile() || railCollapsed ? 0 : 260);
const probe = el('div', 'og-safe-probe');
document.body.append(probe);
const safe = () => {
  const cs = getComputedStyle(probe);
  return { top: parseFloat(cs.paddingTop) || 0, bottom: parseFloat(cs.paddingBottom) || 0 };
};
const topInset = () => (isMobile() ? 112 : 76) + safe().top;   // phone: the lens tabs sit on a second header row

function setVars() {
  const s = safe();
  const r = document.documentElement.style;
  r.setProperty('--og-rail-w', railW() + 'px');
  r.setProperty('--og-sat', s.top + 'px');
  r.setProperty('--og-sab', s.bottom + 'px');
  document.body.classList.toggle('og-mobile', isMobile());
}
setVars();
if (PANE) document.body.classList.add('og-pane');
if (FIXTURE) document.body.classList.add('og-fixture');
sampleChip();   // the sample desk says so on every view, and leaves with one tap (doors.js)
if (api.url('/api') !== '/api') $('[data-act=reel]')?.remove();   // mounted in Hermes: the old reel pane isn't there

// ------------------------------------------------------------ orb + caption
const layer = $('#og-orb-layer');
let canvas = null;
const orb = createOrb(layer, {
  rightLimit: () => window.innerWidth - railW() - 12,
  lowZone: () => window.innerHeight - 74 - safe().bottom,
  topInset: () => (isMobile() ? safe().top : 0),
  pointOf: (id) => (canvas ? canvas.screenCenter(id) : null),
});
let brief = null;
let mode = 'briefing';
OneGuy.orb = {
  // in the briefing, while he's sitting by his line, what he says replaces the line for a beat
  say: (text, o) => {
    if (brief && mode === 'briefing' && !canvas.bloom && text && String(text).length <= 90) {
      orb.say('');
      brief.speak(text, (o && o.ms) || 2600);
    } else orb.say(text, o);
    OneGuy.emit('orb-say', { text });
  },
  mood: (m) => orb.mood(m),
  pointAt: (id) => orb.pointAt(id),
  hop: () => orb.hop(),
  el: orb.el,
};
const jline = $('#og-jline');
let jT = null;
OneGuy.caption = {
  j(text) {
    if (!text) { jline.classList.remove('on'); return; }
    jline.replaceChildren(el('i', 'og-mic'), el('span', '', String(text).slice(0, 200)));
    jline.classList.remove('on');
    void jline.offsetWidth;
    jline.classList.add('on');
    clearTimeout(jT);
    jT = setTimeout(() => jline.classList.remove('on'), 3800);
  },
};

// ------------------------------------------------------------ rail
let railReady = false;
// THE rail → workable-surface hook (audit §5.4), the only place a rail tap is decided. It puts
// the card back quietly, focuses + blooms it (the face and actions), AND opens the item's chat
// column — the conversation lives there now (Phase 2), not in the bloom's thread slot.
function openFromRail(id, rect) {
  canvas.summon(id, rect, { quiet: true });
  return OneGuy.focus(id, { fromRect: rect && rect.width ? rect : null });   // focus opens his thread itself (focus.js grammar)
}
const rail = createRail($('#og-rail'), {
  isMobile,
  onSummon: (id, rect) => openFromRail(id, rect),
  onCollapse: (c) => {
    railCollapsed = c;
    setVars();
    if (!railReady) return;
    if (OneGuy.board.blocks.length) { canvas.layout(OneGuy.board, { animate: true }); canvas.clampView(); canvas.applyView(false); }
    canvas.fitBloom && canvas.fitBloom();
  },
});
railReady = true;
// one focus, three places: the bloom, its rail entry (if it has one), the talk bar's 'about:' chip
OneGuy.on('focus', ({ block } = {}) => {
  rail.setFocus(block ? block.id : null);
  // phase35 (A6): The user has seen a pick — tell the selector so it drops down the pool
  if (block && block.fy_why) api.post('/api/briefing/seen', { id: block.id })
    .catch((e) => console.warn('[home] seen failed', e));
});
// The parked chip (lab/chip, graduated): what an interruption leaves behind. ONE chip per cut,
// right above the talk bar where the user's eyes already are — his real next sentence, "go on" to pick
// it up from there, × to let it go. It used to hide in the put-away rail as "he didn't finish".
let parkedChip = null;
function placeParked() {
  if (!parkedChip) return;
  const bar = document.querySelector('.tk-bar'), r = bar && bar.getBoundingClientRect();
  if (r && r.width) Object.assign(parkedChip.el.style, { left: r.left + 'px', width: r.width + 'px', bottom: window.innerHeight - r.top + 52 + 'px' });   // one row up: The user's own line echoes right above the bar for a moment
}
function dropParked() {
  const p = parkedChip;
  parkedChip = null;
  if (!p) return;
  p.el.classList.remove('on');
  setTimeout(() => p.el.remove(), 320);
}
function parkChip(c) {
  if (parkedChip) parkedChip.el.remove();      // one chip, ever: a new cut replaces the last
  const wrap = el('div', 'og-parked');
  const chip = el('div', 'og-parked-chip');
  const go = el('button', 'og-parked-go', 'go on');
  go.type = 'button';
  go.onclick = () => { dropParked(); try { c.onSummon && c.onSummon(); } catch (e) { console.warn('[home] go on', e); } };
  const x = el('button', 'og-parked-x', '×');
  x.type = 'button';
  x.setAttribute('aria-label', 'let it go');
  x.onclick = () => { dropParked(); try { c.onDismiss && c.onDismiss(); } catch (e) { /* ignore */ } };
  chip.append(el('span', 'og-parked-k', 'parked'), el('span', 'og-parked-t', String(c.label || '')), go, x);
  wrap.append(chip);
  document.body.append(wrap);
  parkedChip = { id: c.id, el: wrap };
  placeParked();
  requestAnimationFrame(() => wrap.classList.add('on'));
  if (OneGuy.sounds) OneGuy.sounds.play('land');
}
window.addEventListener('resize', placeParked);
OneGuy.on('column', () => setTimeout(placeParked, 380));
OneGuy.rail = {
  addChip: (c) => (c && c.tag === 'didnt-land' ? parkChip(c) : rail.addChip(c)),
  removeChip: (id) => { if (parkedChip && parkedChip.id === id) dropParked(); return rail.removeChip(id); },
  has: (id) => rail.has(id),
  open: () => rail.setDrawer(true),
};

// ------------------------------------------------------------ canvas
canvas = createCanvas({
  stage: $('#og-stage'), world: $('#og-world'), cardsLayer: $('#og-cards'), regionsLayer: $('#og-regions'),
  projectsEl: $('#og-projects'), tendrils: $('#og-tendrils'), backdrop: $('#og-backdrop'),
  rail, orb, isMobile, railW, topInset,
});
orb.setHome(() => (mode === 'briefing' && brief && brief.orbHome()) || canvas.orbHomePoint());
brief = createBriefing({
  root: $('#og-brief'), canvas, rail, orb, isMobile,
  onShowAll: () => setView('canvas'),
  onTile: (cid) => setView('canvas', { cluster: cid }),
});
attachGestures({ stage: $('#og-stage'), canvas, railW, isMobile });

// A thing the user just dealt with (it flew off) stays gone for a little while even if a slow source
// (a cache, GitHub) still lists it in its old state — otherwise the card pops back until the next poll.
const sent = new Map();   // block id → {at, status}
OneGuy.on('action-done', ({ block, action, result } = {}) => {
  if (block && action && ((result && result.decided) || SENT[action.id]) && !(action.id === 'done' && block.status === 'done')) sent.set(block.id, { at: Date.now(), status: block.status });
});
OneGuy.on('undone', ({ block } = {}) => { if (block) sent.delete(block.id); });   // he took it back: the card may return
function indexBoard(b) {
  b.blocks = Array.isArray(b.blocks) ? b.blocks.filter((x) => x && x.id && x.kind) : [];
  for (const [id, s] of sent) if (Date.now() - s.at > 90000) sent.delete(id);
  if (sent.size) b.blocks = b.blocks.filter((x) => !(sent.has(x.id) && x.status === sent.get(x.id).status));
  b.byId = Object.fromEntries(b.blocks.map((x) => [x.id, x]));
  b.projects = b.projects || [];
  b.sources = b.sources || {};
  return b;
}
// here=1: he has been at the desk since the last load, so what turns up now happened in front of him and
// is not marked as having moved while he was away (board/briefing.py). A first load or a return is not here.
let atDesk = false;
document.addEventListener('visibilitychange', () => { if (document.hidden) atDesk = false; });
async function fetchBoard(fresh) {
  if (FIXTURE) return indexBoard(await api.get(FIXTURE_URL));
  const q = [fresh && 'fresh=1', atDesk && 'here=1'].filter(Boolean).join('&');
  const b = indexBoard(await api.get('/api/board' + (q ? '?' + q : '')));
  atDesk = !document.hidden;
  return b;
}

// waiting-on-you blocks the tiles count beyond the cards already on the briefing (the tiles
// exclude those cards, so header + tiles add up)
function moreWaiting(b) {
  return canvas.layoutState && canvas.layoutState.plan ? brief.moreWaiting(b) : 0;
}

// the header: calm numbers only. "3 for you today · 4 more waiting · 1 thing broke"
function summary(b) {
  const sh = OneGuy.briefing(b);
  const box = $('#og-sum');
  box.replaceChildren();
  const part = (cls, t) => box.append(el('span', cls, t));
  const n = sh.forYou.length;
  const brokeN = sh.broke.length;
  // phone: the row is short, the count wins — and when the only news is that something broke, that IS the row
  if (n || !(isMobile() && brokeN)) part('fy', n ? `${n} for you${isMobile() ? '' : ' today'}` : (isMobile() ? 'nothing for you' : 'nothing for you today'));
  // the tiles below say "N waiting on you"; the header has to add up to the same total, or a
  // first look reads "3 for you" against tiles summing to 7 (the audit's count mismatch)
  const more = moreWaiting(b);
  if (more) part('more', `${more} more waiting`);
  if (brokeN) part('hot br', `${brokeN} thing${brokeN === 1 ? '' : 's'} broke`);
  const off = Object.entries(b.sources || {}).filter(([, s]) => s && s.ok === false).map(([k]) => k);
  if (off.length) box.title = `can’t see: ${off.join(', ')}`;
  document.body.classList.toggle('og-fixture', FIXTURE);
}

let lastLoad = 0;
async function refresh({ fresh = false, quiet = false } = {}) {
  // never re-lay the board under the user's hand
  if (document.body.classList.contains('og-dragging')) { setTimeout(() => refresh({ fresh, quiet }), 1500); return; }
  let b;
  try {
    b = await fetchBoard(fresh);
  } catch (err) {
    if (!quiet) OneGuy.toast('couldn’t load the board: ' + (err.message || err), true);
    orb.say('can’t reach the board right now.', { ms: 3000 });
    return;
  }
  lastLoad = Date.now();
  if (document.body.classList.contains('og-dragging')) { setTimeout(() => refresh({ quiet: true }), 1500); return; }
  const prev = new Set(OneGuy.board.blocks.map((x) => x.id));
  OneGuy.board = b;
  const { born } = canvas.layout(b, { animate: true });
  const fresh2 = born.filter((id) => !prev.has(id));
  if (fresh2.length && prev.size && mode === 'canvas') canvas.placeNew(fresh2.sort((x, y) => score(b.byId[y]) - score(b.byId[x])));
  else born.forEach((id) => canvas.els.get(id)?.classList.remove('og-pre'));
  afterBoard();
  OneGuy.emit('board', { board: b });
}
function afterBoard() {
  summary(OneGuy.board);
  brief.render(OneGuy.board);
  updateTidy();
  const f = canvas.focusId;
  if (f) {
    const b = OneGuy.board.byId[f];
    // gone from the board: it may only have changed its id (a task gets its permanent key when its
    // thread opens) — follow it before deciding it's gone
    if (!b) OneGuy.repoint(f).then((k) => { if (!k && canvas.focusId === f) OneGuy.unfocus(); });
    else if (b.status === 'done' && !canvas.els.has(f)) OneGuy.unfocus();
  }
}

// ------------------------------------------------------------ hooks behind OneGuy.*
const H = OneGuy._hooks;
H.focus = (id, o = {}) => {
  if (!id) return H.unfocus();
  const srcEl = o.srcEl || (mode === 'briefing' ? brief.cardEl(id) : null);
  return canvas.openBloom(id, { srcEl, fromRect: o.fromRect || null });
};
H.view = (m, o) => setView(m, o);
H.unfocus = () => canvas.closeBloom();
H.park = (id) => {
  if (canvas.focusId === id) canvas.closeBloom({ instant: true });
  if (mode === 'briefing' && brief.cardEl(id)) return brief.putAway(id);
  if (canvas.els.has(id)) return canvas.flickToRail(id, { x: 1.1, y: -0.1 });
  store.set(id, { parked: true });
  canvas.layout(OneGuy.board, { animate: true });
  const b = OneGuy.board.byId[id];
  if (b) OneGuy.emit('park', { block: b });
};
H.summon = (id, rect, o) => canvas.summon(id, rect, o);
H.refresh = (o) => refresh(o);
H.zoom = (level) => setLevel(level);
H.applyLocal = () => {
  indexBoard(OneGuy.board);
  canvas.layout(OneGuy.board, { animate: true });
  afterBoard();
  OneGuy.emit('board', { board: OneGuy.board });
};

// ------------------------------------------------------------ tidy the table (Nov 9)
// the antidote to the mess: one tap re-flows everything from the system layout.
// Visible whenever there's something to tidy (nudges or stored placements).
const tidyBtn = $('#og-tidy');
tidyBtn.onclick = () => {
  tidyBtn.hidden = true;
  canvas.reset();
};
function updateTidy() {
  if (!canvas) return;
  tidyBtn.hidden = !(canvas.messy && canvas.messy());
}
// he offers once per visit when the canvas is carrying a mess
function offerTidy() {
  if (sessionStorage.ogTidyOffered || !(canvas.messy && canvas.messy())) return;
  sessionStorage.ogTidyOffered = '1';
  OneGuy.orb.say('the table’s a bit of a mess — tidy it?', { ms: 4200 });
  tidyBtn.hidden = false;
  tidyBtn.classList.add('og-tidy-hint');
  setTimeout(() => tidyBtn.classList.remove('og-tidy-hint'), 6000);
}

// ------------------------------------------------------------ top bar: wordmark menu + zoom
const mark = $('#og-mark'), menu = $('#og-menu');
function setMenu(on) {
  menu.hidden = !on;
  mark.setAttribute('aria-expanded', String(on));
}
mark.onclick = (e) => { e.stopPropagation(); setMenu(menu.hidden); };
document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !e.target.closest('#og-menu, #og-mark')) setMenu(false); });
menu.addEventListener('click', (e) => {
  const t = e.target.closest('[data-act]');
  if (!t) return;
  const act = t.dataset.act;
  if (act === 'reel') { e.preventDefault(); setMenu(false); OneGuy.openUrl('/reel'); return; }
  setMenu(false);
  if (act === 'refresh') { OneGuy.orb.say('looking again.', { ms: 1400 }); refresh({ fresh: true }); }
  if (act === 'everything') setView(mode === 'canvas' ? 'briefing' : 'canvas');
  if (act === 'zoom-projects') { setLens('board', { quiet: true }); setLevel('projects'); }
  if (act === 'zoom-blocks') { setLens('board', { quiet: true }); setLevel('blocks'); }
});
function setLevel(level) {
  canvas.setLevel(level);
}
$('#og-back').onclick = () => setView('briefing');

// ------------------------------------------------------------ views: briefing ⇄ canvas
// The lens switcher: today (the briefing) · board · time · inbox. One row of tabs, still ONE input.
// today is its own view; the other three are the same cards in three layouts (canvas.setLens).
const lensNav = $('#og-lens');
const LENS_SAY = {
  board: () => 'everything, by project.',
  time: (P) => {
    // count what's on screen below the now line (what's still ahead today), nothing else
    const d0 = P.clusters.find((c) => c.id === 'day:0');
    const t = d0 ? d0.shown.length - (d0.nowAt || 0) - (d0.open ? d0.folded.length : 0) : 0;
    return t > 0 ? `your week. ${words(t)} still ahead today.` : 'your week. nothing left on today.';
  },
  inbox: (P) => {
    // say what's actually here: a broken routine is not "waiting on you" (the header said "nothing for you" beside it)
    const count = (g) => { const c = P.clusters.find((x) => x.id === 'inbox:' + g); return c ? c.all.length : 0; };
    const d = count('decide'), k = count('broke'), i = count('idea'), n = d + k + i;
    if (!n) return 'nothing is waiting on you.';
    const parts = [d && (d === 1 ? 'one thing is waiting on you' : `${words(d)} things are waiting on you`),
      k && (k === 1 ? 'one thing broke' : `${words(k)} things broke`),
      i && (i === 1 ? 'one idea from me' : `${words(i)} ideas from me`)].filter(Boolean);
    // phone: one short row, so it never sits over a card's button for three lines
    if (isMobile() && parts.length > 1) return [d && `${d} waiting on you`, k && `${k} broke`, i && `${i} idea${i === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    // "answer" only when something is actually asking (a broken routine's button runs it again, it doesn't answer)
    return `${parts.join(', ')}.` + (d ? (n === 1 ? ' its button answers it.' : ' the buttons answer them.') : '');
  },
};
function markLens() {
  const cur = mode === 'briefing' ? 'today' : canvas.lens;
  document.body.dataset.ogLens = cur;   // not data-lens: only the four tabs answer to [data-lens]
  for (const b of lensNav.querySelectorAll('[data-lens]')) b.setAttribute('aria-selected', String(b.dataset.lens === cur));
  // the badge is what's waiting on a yes or no from the user — not every idea or broken routine in the lens
  // (on a real board that read "inbox 50")
  const n = OneGuy.board.blocks.filter((b) => b.inbox && (b.inbox.group || 'decide') === 'decide' && b.status !== 'done' && !(store.get(b.id) || {}).parked).length;
  const badge = $('#og-lens-n');
  badge.textContent = String(n);
  badge.hidden = !n;
}
function setLens(name, { quiet = false } = {}) {
  if (name === 'today') return setView('briefing', { quiet });
  const from = mode;
  if (from !== 'canvas') {          // arriving from today: lay the lens out unseen, then reveal it
    const rects = new Map();        // where the briefing's cards stand right now: they travel too
    for (const b of (brief.sheet ? brief.sheet.forYou : [])) {
      const n = brief.cardEl(b.id), r = n && n.getBoundingClientRect();
      if (r && r.width && r.right > 0 && r.left < window.innerWidth) rects.set(b.id, r);
    }
    canvas.setLens(name, { animate: false });
    setView('canvas', { quiet: true });
    canvas.flyFrom(rects);
  } else if (!canvas.setLens(name)) return true;
  store.setView({ mode: 'canvas', lens: canvas.lens });
  markLens();
  updateTidy();
  const say = LENS_SAY[canvas.lens];
  if (!quiet && say && !canvas.bloom) OneGuy.orb.say(say(canvas.layoutState.plan), { ms: 3000 });   // focused: he stays quiet, the panel is the subject
  return true;
}
lensNav.addEventListener('click', (e) => {
  const t = e.target.closest('[data-lens]');
  if (t) setLens(t.dataset.lens);
});
OneGuy.lens = (name, o) => setLens(name, o);
OneGuy.on('board', markLens);
// putting a card away changes the count: the header says the new number at once, like his headline does
OneGuy.on('park', () => { summary(OneGuy.board); markLens(); });
OneGuy.on('summon', () => summary(OneGuy.board));

function setView(m, { cluster = null, focus = null, srcEl = null, quiet = false } = {}) {
  if (focus) {                     // a tap on a briefing card / the broke line: bloom it right here
    if (canvas.bloom && canvas.focusId === focus) return true;
    brief.hideHint();
    return canvas.openBloom(focus, { srcEl });
  }
  const prev = mode;
  mode = m === 'canvas' ? 'canvas' : 'briefing';
  document.body.dataset.view = mode;
  const ev = $('[data-act=everything]');
  if (ev) ev.textContent = mode === 'canvas' ? 'back to today' : 'show everything';
  if (!quiet) store.setView({ mode, lens: mode === 'canvas' ? canvas.lens : 'board' });
  if (mode === 'canvas') {
    if (cluster) canvas.setLens('board', { animate: false });   // a project tile opens its pile on the board
    if (canvas.view.level !== 'blocks') canvas.setLevel('blocks');
    if (cluster) canvas.setLevel('blocks', { cluster });
    if (prev !== 'canvas' && !REDUCED) {
      const cards = [...canvas.els.values()];
      cards.forEach((n, i) => n.animate([{ opacity: 0, transform: n.style.transform + ' translateY(8px) scale(.97)' }, { opacity: 1, transform: n.style.transform }],
        { duration: 420, delay: Math.min(400, i * 14), easing: EASE_OUT, fill: 'backwards' }));
      [...$('#og-regions').children].forEach((r) => r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 420, easing: EASE_OUT }));
      if (!quiet) OneGuy.orb.say(cluster ? 'here’s that pile.' : 'everything, by project.', { ms: 2600 });
    }
  } else {
    canvas.setLens('board', { animate: false });   // the briefing's tiles read the board's piles
    canvas.setLevel('blocks');
    brief.render(OneGuy.board);
    if (prev !== 'briefing' && !REDUCED) $('#og-brief').animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: EASE_OUT });
  }
  // focus survives the change of view: the panel stays and re-links to where its card lives now
  if (canvas.bloom) canvas.relinkBloom(mode === 'briefing' ? brief.cardEl(canvas.focusId) : null); else orb.home();
  markLens();
  OneGuy.emit('view', { view: mode });
  return true;
}
// any open 'more' menu in a bloom closes on an outside tap
document.addEventListener('pointerdown', (e) => {
  for (const m of document.querySelectorAll('.og-more-m:not([hidden])')) {
    if (!m.parentElement.contains(e.target)) { m.hidden = true; m.parentElement.querySelector('.og-more-b')?.setAttribute('aria-expanded', 'false'); }
  }
});

// region labels focus their project; fold chips expand the cluster
$('#og-stage').addEventListener('click', (e) => {
  const lab = e.target.closest('.og-region-l');
  if (lab) {
    if (lab.dataset.toLens) document.querySelector(`#og-lens [data-lens="${lab.dataset.toLens}"]`)?.click();   // "on the calendar" → the week
    else if (lab.dataset.focus) OneGuy.focus(lab.dataset.focus);
    return;
  }
  const fold = e.target.closest('.og-fold');
  if (fold) canvas.toggleCluster(fold.dataset.cluster);
});
$('#og-backdrop').addEventListener('pointerdown', (e) => {
  e.preventDefault();
  // a tap on another card through the dimmed backdrop moves focus there in one tap
  const under = document.elementsFromPoint(e.clientX, e.clientY)
    .map((n) => n.closest && n.closest('.og-card[data-id], #og-brief [data-id]')).find(Boolean);
  const id = under && under.dataset.id;
  if (id && id !== canvas.focusId && OneGuy.board.byId[id] && !under.classList.contains('og-src-hidden')) {
    OneGuy.focus(id, { srcEl: under });
    return;
  }
  OneGuy.unfocus();
});

// keyboard: Esc closes, Enter/space on a focused card blooms it ('/' belongs to talk.js)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.querySelector('.og-ask-scrim')) return;
    if (!menu.hidden) { setMenu(false); return; }
    if (canvas.bloom) { e.preventDefault(); OneGuy.back(); return; }   // one step back along the trail (focus.js)
    if (rail.open) { rail.setDrawer(false); return; }
    if (canvas.view.level === 'projects') { setLevel('blocks'); return; }
    return;   // nothing focused: Esc never throws you out of the lens you're in (card grammar §3)
  }
  const card = mode === 'canvas' && document.activeElement && document.activeElement.closest && document.activeElement.closest('.og-card');
  if (card && (e.key === 'Enter' || e.key === ' ') && !canvas.bloom) { e.preventDefault(); OneGuy.focus(card.dataset.id); }
});

// the phone's keyboard: iOS lays the page out as if it weren't there and slides the visual viewport
// instead, so a fixed talk bar ends up under the keys. --og-kb is how much of the bottom the keyboard
// took (0 when it is down); the bar rides on it, and the field being typed in is kept in view.
const vv = window.visualViewport;
function keyboard() {
  const kb = vv && isMobile() ? Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)) : 0;
  const up = kb > 80;                                   // smaller than that is a toolbar or a zoom, not a keyboard
  document.documentElement.style.setProperty('--og-kb', (up ? kb : 0) + 'px');
  document.body.classList.toggle('og-kb', up);
  const a = document.activeElement;
  if (up && a && a.closest && a.closest('#og-brief') && /^(TEXTAREA|INPUT)$/.test(a.tagName)) a.scrollIntoView({ block: 'nearest' });
}
if (vv) { vv.addEventListener('resize', keyboard); vv.addEventListener('scroll', keyboard); }
OneGuy.keyboard = keyboard;

let rsT = null;
window.addEventListener('resize', () => {
  clearTimeout(rsT);
  rsT = setTimeout(() => {
    const wasMobile = document.body.classList.contains('og-mobile');
    setVars();
    if (wasMobile !== isMobile()) rail.setDrawer(false);
    if (OneGuy.board.blocks.length) { canvas.layout(OneGuy.board, { animate: true }); brief.render(OneGuy.board); }
    canvas.clampView();
    canvas.applyView(false);
    canvas.fitBloom && canvas.fitBloom();
  }, 160);
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && Date.now() - lastLoad > 60000) refresh({ quiet: true });
});
// a finished reply may have added or changed something (a task, a proposal): look again quietly,
// once, shortly after he stops talking (askflow #1, piece 3)
let replyRefreshT = 0;
OneGuy.on('say', (e) => {
  if (!e || e.from !== 'guy') return;
  clearTimeout(replyRefreshT);
  replyRefreshT = setTimeout(() => refresh({ quiet: true }), 1500);
});

// ------------------------------------------------------------ boot
async function boot() {
  await store.load();
  // A2's kinds first (card heights depend on them); F's surface + talk after the shell exists
  const kinds = import('./kinds/index.js').catch((e) => console.warn('[home] kinds failed to load', e));
  await Promise.race([kinds, sleep(4000)]);
  import('./surface.js').catch((e) => console.warn('[home] surface failed to load', e));
  import('./talk.js').catch((e) => console.warn('[home] talk failed to load', e));
  import('./live.js').catch((e) => console.warn('[home] live failed to load', e));   // phase3: card pulses + 'N working' → runs
  import('./stage.js').catch((e) => console.warn('[home] stage failed to load', e)); // phase4

  let b;
  try {
    b = await fetchBoard(false);
  } catch (err) {
    orb.jump(window.innerWidth / 2 - railW() / 2, window.innerHeight * 0.45);
    orb.appear();
    orb.say('can’t reach the board. i’ll keep trying.', { ms: 6000 });
    OneGuy.toast('board: ' + (err.message || err), true);
    setInterval(() => { if (!OneGuy.board.blocks.length) refresh({ quiet: true }).then(() => { if (OneGuy.board.blocks.length) location.reload(); }); }, 15000);
    return;
  }
  lastLoad = Date.now();
  OneGuy.board = b;
  const z = Number(store.data.view && store.data.view.zoom);
  if (!isMobile() && z >= 0.55 && z <= 1.4) canvas.view.z = z;
  canvas.view.x = isMobile() ? 16 : 32;
  canvas.view.y = topInset();
  canvas.applyView(false);
  canvas.layout(b);
  canvas.clampView();
  canvas.applyView(false);
  // which view: ?view= wins; else the briefing (the canvas is one tap away, never the landing view —
  // the old "the user placed things → open canvas" rule is gone with freeform placement)
  const wantLens = params.get('lens') || (store.data.view && store.data.view.lens) || 'board';
  const want = params.get('lens') ? 'canvas' : params.get('view') || (store.data.view && store.data.view.mode === 'canvas' ? 'canvas' : 'briefing');
  mode = want === 'canvas' ? 'canvas' : 'briefing';
  if (mode === 'canvas' && wantLens !== 'board') canvas.setLens(wantLens, { animate: false });
  document.body.dataset.view = mode;
  const ev = $('[data-act=everything]');
  if (ev) ev.textContent = mode === 'canvas' ? 'back to today' : 'show everything';
  summary(b);
  brief.render(b);
  markLens();
  OneGuy.emit('board', { board: b });

  const KEY = 'og.entered.' + NS;
  const last = Number(sessionStorage.getItem(KEY) || 0);
  const force = params.get('entrance');
  const quick = force === '0' || (force !== '1' && Date.now() - last < 10 * 60 * 1000);
  sessionStorage.setItem(KEY, String(Date.now()));
  document.body.classList.add('og-ready');
  if (mode === 'briefing') {
    canvas.entrance(b, { quick: true, silent: true });   // the canvas lays itself out quietly, out of sight
    await brief.entrance({ quick });
    orb.home();
  } else await canvas.entrance(b, { quick });
  document.body.classList.add('og-entered');
  OneGuy.emit('ready', {});
  await restoreFocus();
  offerTidy();
  setInterval(() => { if (!document.hidden) refresh({ quiet: true }); }, 60000);
  // the first board a cold server hands out is its quick one; slower sources (his piles, his headlines) ride the
  // next build. Look again early, three times, so the rail fills in within seconds instead of at the one-minute tick
  // (a stranger's first open sat on three cards while six were ready).
  // Only while he is still on the first screen: with a card open a refresh can re-key it mid-reply.
  for (const ms of [6000, 12000, 20000]) setTimeout(() => { if (!document.hidden && !canvas.focusId && !document.body.classList.contains('og-col-open')) refresh({ quiet: true }); }, ms);
}

// a reload (or a crash, or the tab sleeping) used to drop the conversation we were in and leave
// nothing on the canvas marking where the work happens (the user). Put him back in it: bloom +
// column, quietly, only if the block is still live.
async function restoreFocus() {
  let fid = store.data.view && store.data.view.focus;
  if (!fid) return;
  if (!OneGuy.board.byId[fid] && OneGuy.repoint) fid = (await OneGuy.repoint(fid)) || fid;   // same item, new id
  const fb = OneGuy.board.byId[fid];
  if (!fb || fb.status === 'done' || fb.status === 'parked') { store.setFocus(null); return; }
  for (let i = 0; i < 30 && !OneGuy.column; i++) await sleep(100);   // surface.js loads async
  if (!OneGuy.column) return;
  try {
    OneGuy.focus(fid, {});
    OneGuy.orb.say('picking up where we left off.', { ms: 2200 });
  } catch (e) { console.warn('[home] focus restore failed', e); }
}

boot().catch((err) => {
  console.error('[home] boot failed', err);
  OneGuy.toast('the home surface hit a snag: ' + (err.message || err), true);
});
