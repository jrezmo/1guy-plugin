// 1guy home · the Time lens (Lane D 2026). Time is a place: anything with a time
// lives here at its day (and hour), and dropping a card on a day schedules it
// (docs/card-grammar.md §6, adaptive-surfaces.md REFINEMENT 6).
// canvas.js only asks two things of it: plan(live) — which days, which cards in each — and
// mark(b, node) — the face's "when" line. Everything about days, hours and drops lives here.
import OneGuy, { el, headlineOf, judge } from './core.js';

// its own stylesheet (world lines on faces, the drop hint), so home.css stays B's
(() => {
  if (typeof document === 'undefined' || document.querySelector('link[data-og-time]')) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = new URL('./time.css', import.meta.url).href;
  l.dataset.ogTime = '1';
  document.head.append(l);
})();

const DAY = 86400000;
export const dayStart = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
export const whenTs = (b) => {
  const at = b.when && String(b.when.at || '');
  const t = at ? Date.parse(at.length === 10 ? at + 'T00:00' : at) : NaN;
  return Number.isFinite(t) ? t : null;
};
export const clock = (t) => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase().replace(':00', '');
const ago = (t) => {
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;
};

// Belongs to time, not to a project: it happens at a clock time, or it's a calendar event.
// On the Board these share one "on the calendar" pile whose label opens this lens.
export const isEvent = (b) => !!(b.when && b.kind !== 'project' && b.kind !== 'widget' &&
  (b.when.kind === 'event' || (b.when.kind === 'due' && !b.when.all_day)) && b.status !== 'done');

// ------------------------------------------------------------------ which week
// The lens shows one week at a time; doors at either end step it (this week ‹ › next week).
// Session-only, like a scroll position: switching lens comes back to this week.
let week = 0;
let dragging = null;     // a card being dragged stays on the table while the week flips under it
let bound = null;        // the canvas, so a door can re-lay the week (canvas calls bind once)
export const bind = (c) => { bound = c; };
export const getWeek = () => week;
// The week is a place you see whole: on the way in, a zoomed-in view (the user's board zoom carries over) is
// eased down just enough that every day fits the room. Only for this visit; his saved zoom is untouched.
function fitWeek() {
  if (!bound || document.body.dataset.ogLens !== 'time' || window.innerWidth <= 700) return;
  const v = bound.view, w = bound.layoutState.bounds.w;
  const rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--og-rail-w')) || 0;
  const room = window.innerWidth - rail - 24 * 2 - 36;     // margins + the next-week door
  if (!w || w * v.z <= room) return;
  const z = Math.max(0.7, room / w);
  if (!fitted) fitted = { from: v.z, to: z };
  else fitted.to = z;
  v.z = z;
  v.x = 24;
  bound.clampView();
  bound.applyView(true);
}
let fitted = null;      // {from, to}: the zoom the user had before the week was fitted, so leaving gives it back
function unfitWeek() {
  if (!fitted || !bound) return;
  const v = bound.view, f = fitted;
  fitted = null;
  if (Math.abs(v.z - f.to) > 0.001) return;      // he zoomed himself while in Time: keep his choice
  v.z = f.from;
  bound.clampView();
  bound.applyView(true);
}

export function setWeek(n) {
  n = Math.max(0, Math.min(12, n | 0));
  if (n === week) return false;
  week = n;
  if (bound) {
    bound.layout(OneGuy.board, { animate: true });
    if (dragging) bound.els.get(dragging)?.classList.remove('glide');   // the dragged card follows the hand, not the layout
    if (!dragging) requestAnimationFrame(fitWeek);
  }
  OneGuy.orb?.say?.(n === 0 ? 'this week.' : n === 1 ? 'next week.' : `${n} weeks out.`, { ms: 1500 });
  return true;
}
if (typeof document !== 'undefined') {
  document.addEventListener('click', (e) => {
    const reg = e.target.closest?.('.og-region[data-cluster="day:next"], .og-region[data-cluster="day:prev"]');
    if (!reg) return;
    e.stopPropagation();
    setWeek(week + (reg.dataset.cluster === 'day:next' ? 1 : -1));
  }, true);
  OneGuy.on?.('lens', ({ lens } = {}) => {
    week = 0;
    if (lens === 'time') requestAnimationFrame(fitWeek);
    else unfitWeek();
  });
}

// the doors: slim tabs on the screen's edges (they take no room from the week). Tap one, or hold a
// dragged card on it, and the week turns. Same ids as regions, so slotAt/hover treat them alike.
function door(id, side) {
  let d = document.querySelector(`button.og-door[data-cluster="${id}"]`);
  if (!d) {
    d = el('button', 'og-region og-door og-door-' + side);
    d.type = 'button';
    d.dataset.cluster = id;
    d.append(el('span', 'og-door-t'));
    document.body.append(d);
  }
  return d;
}
function doors(beyond, stacked) {
  if (typeof document === 'undefined') return;
  const next = door('day:next', 'r'), prev = door('day:prev', 'l');
  next.hidden = !!stacked;
  next.firstChild.textContent = (week === 0 ? 'next week' : 'the week after') + (beyond ? ` · ${beyond}` : '') + '  ›';
  next.setAttribute('aria-label', week === 0 ? 'next week' : 'the week after');
  prev.firstChild.textContent = '‹  ' + (week === 1 ? 'this week' : 'a week back');
  prev.setAttribute('aria-label', week === 1 ? 'back to this week' : 'a week back');
  prev.hidden = week === 0 || !!stacked;
}

// The week strip: one column per day of the shown week (overdue before it in this week, a door
// to the next week after it). This week's today carries what already happened (activity) above
// a "now" line; older history folds. Fills the caller's groups/tier.
export function plan(live, { put, groups, tier, mobile = false, room = Infinity }) {
  const defs = [];
  const now = Date.now(), d0 = dayStart(now), start = d0 + week * 7 * DAY;
  const past = [], ahead = [], anytime = [];
  const loose = [];                    // to-dos with no time yet: the tray you drag onto the days
  const carry = [];                    // the dragged card when the week flips away from where it lives
  const routines = [[], [], [], [], [], [], []];   // scheduled jobs per shown day (folded, never cards)
  let beyond = 0;
  for (const b of live) {
    const t = whenTs(b);
    if (t == null && b.kind === 'task' && b.status !== 'done' && b.status !== 'parked') { loose.push(b); continue; }
    if (t == null || b.kind === 'project' || b.kind === 'widget') continue;
    const k = b.when.kind;
    if (k === 'activity') { if (week === 0 && t >= d0) past.push(b); continue; }
    if (b.status === 'done' && b.kind !== 'job') continue;
    const i = Math.round((dayStart(t) - start) / DAY);
    // routines run on their own: they fold into one quiet line per day, never cards between the user's things
    if (b.kind === 'job' || k === 'scheduled') { if (i >= 0 && i <= 6) routines[i].push(b); continue; }
    if (i < 0) { if (week === 0) put('day:late', b); else if (b.id === dragging) carry.push(b); }
    else if (i > 6) { beyond++; if (b.id === dragging) carry.push(b); }
    else if (i > 0 || week > 0) put('day:' + i, b);
    else if (b.when.all_day) anytime.push(b);
    else (t <= now ? past : ahead).push(b);
  }
  const byT = (a, b) => whenTs(a) - whenTs(b);
  for (const xs of groups.values()) xs.sort((a, b) => (b.when.all_day ? 1 : 0) - (a.when.all_day ? 1 : 0) || byT(a, b));
  past.sort(byT); ahead.sort(byT);
  const RECENT = 2;                    // today's history stays short; the rest folds
  const older = past.slice(0, Math.max(0, past.length - RECENT));
  const recent = past.slice(older.length);

  if (groups.has('day:late')) defs.push({ id: 'day:late', title: 'overdue', color: '#b98a8a' });
  for (let i = 0; i < 7; i++) {
    const d = new Date(start + i * DAY + DAY / 2);
    const wd = d.toLocaleDateString([], { weekday: 'short' }).toLowerCase();
    const near = week === 0 && i < 2;
    defs.push({
      id: 'day:' + i, keep: true, color: week === 0 && i === 0 ? '#c9cacf' : '#6f727a', today: week === 0 && i === 0,
      title: !near ? wd : i === 0 ? 'today' : 'tomorrow',
      sub: near || (week > 0 && i === 0) ? `${wd} ${d.getDate()}` : String(d.getDate()),
    });
  }

  // the week stacks (today first, like the phone) when the room can't hold it side by side:
  // a narrow desktop pane, a half-screen window. A day needs ~176px; an empty one ~90.
  const DAYW = 176, EMPTYW = 90;
  const filled = [...Array(7).keys()].map((i) => (i === 0 && week === 0 ? recent.length + anytime.length + ahead.length + older.length : (groups.get('day:' + i) || []).length) + routines[i].length);
  const extra = (groups.has('day:late') ? 1 : 0) + (loose.length || carry.length ? 1 : 0);
  const stacked = mobile || room < filled.reduce((w, n) => w + (n ? DAYW : EMPTYW), 0) + extra * DAYW;
  // the doors: side by side they're slim tabs on the screen's edges (no room taken from the week);
  // stacked they're rows at either end of the week (vertical room is plentiful there)
  doors(beyond, stacked);
  if (stacked) {
    if (week > 0) defs.unshift({ id: 'day:prev', keep: true, door: -1, color: '#6f727a', title: week === 1 ? '‹ back to this week' : '‹ a week back' });
    const at = defs.findIndex((d) => d.id === 'day:6') + 1;
    defs.splice(at, 0, { id: 'day:next', keep: true, door: 1, color: '#6f727a', title: week === 0 ? 'next week ›' : 'the week after ›', sub: beyond ? `${beyond} there` : '' });
  }
  const SHOW = mobile ? 2 : 4;
  if (loose.length || carry.length) {
    const score = (b) => (b.attention || 0) + (b.status === 'needs-you' ? 0.08 : 0);
    loose.sort((a, b) => score(b) - score(a));
    groups.set('day:none', [...carry, ...loose.slice(0, SHOW)]);
    // side by side: beside the week, where the hand starts; stacked: after it, so today is the first thing you see
    // the rest of the pile says what it holds, by project ("+30 · 1guy 12 · chores 6"), not just a number
    const rest = loose.slice(SHOW), by = new Map();
    const proj = (b) => { const p = (OneGuy.board.projects || []).find((x) => x.id === b.project); return (p ? p.title : (b.tags || [])[0] || 'loose ends').replace(/\s*\([^)]*\)\s*$/, '').toLowerCase(); };
    for (const b of rest) by.set(proj(b), (by.get(proj(b)) || 0) + 1);
    const kinds = [...by].sort((a, c) => c[1] - a[1]).slice(0, stacked ? 3 : 1).map(([k, n]) => `${k} ${n}`).join(' · ');
    defs[stacked ? 'push' : 'unshift']({ id: 'day:none', title: 'no time yet', sub: mobile ? 'hold one, drag it onto a day' : stacked ? 'drag one onto a day' : '', color: '#6f727a', tray: true,
      folded: rest, foldWords: (n) => (stacked ? `${n} more without a time` : `+${n}`) + (kinds ? ' · ' + kinds : '') });
  }
  if (week === 0) {
    groups.set('day:0', [...recent, ...anytime, ...ahead]);
    const today = defs.find((x) => x.id === 'day:0');
    today.nowAt = recent.length;
    today.folded = older;
  }
  const many = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  for (let i = 0; i < 7; i++) {
    const r = routines[i];
    if (!r.length) continue;
    r.sort(byT);
    const d = defs.find((x) => x.id === 'day:' + i);
    const earlier = (d.folded || []).length;
    d.folded = [...(d.folded || []), ...r];
    d.foldWords = () => [earlier ? `${earlier} past` : '', many(r.length, 'routine')].filter(Boolean).join(' · ');
    for (const b of r) tier.set(b.id, 'fold');
  }
  for (const xs of groups.values()) for (const b of xs) tier.set(b.id, 'compact');
  for (const b of older) tier.set(b.id, 'fold');
  for (const b of loose.slice(SHOW)) tier.set(b.id, 'fold');
  return { defs, pack: stacked ? 'col' : 'row' };
}

// the face's "when" line in this lens
function whenWords(b) {
  const t = whenTs(b), k = b.when.kind;
  if (t == null) return '';
  const today = dayStart(t) === dayStart(Date.now());
  if (k === 'activity') return today ? ago(t) : new Date(t).toLocaleDateString([], { weekday: 'short' }).toLowerCase();
  if (k === 'scheduled') return 'runs ' + clock(t);
  if (b.when.all_day) return t < dayStart(Date.now()) ? 'was due ' + new Date(t).toLocaleDateString([], { weekday: 'short', day: 'numeric' }).toLowerCase() : (today ? 'any time today' : 'any time');
  const end = b.when.end ? Date.parse(b.when.end) : NaN;
  return clock(t) + (Number.isFinite(end) ? '–' + clock(end) : '');
}
export function mark(b, node) {
  if (b.when) node.prepend(el('div', 'og-when og-when-' + b.when.kind, whenWords(b)));
}


// put an item back where it was before the last drop (wasAt null = it had no time)
export async function undo(id, wasAt, canvas) {
  const b = OneGuy.board.byId[id];
  if (!b) return { ok: false, error: 'that item moved on' };
  const words = wasAt ? `${dayWords(Date.parse(wasAt.length === 10 ? wasAt + 'T12:00' : wasAt))}${wasAt.length > 10 ? ' · ' + clock(Date.parse(wasAt)) : ' · any time'}` : '';
  const res = await schedule(b, wasAt, canvas, words, { undoing: true });
  return res ? { ok: true } : { ok: false, error: "couldn't put it back" };
}

// ------------------------------------------------------------------ coordination (card-grammar §6)
// A time that involves other people isn't settled by writing it down. The drop opens (or
// continues) the item's thread with the one guy, and the first thing he says is a proposal for
// the next step. He doesn't act until the user says so.
const NOT_PEOPLE = new Set(('the a an my our your his her their this that it me you him them us everyone ' +
  'mon tue wed thu fri sat sun monday tuesday wednesday thursday friday saturday sunday today tomorrow ' +
  'jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october november december ' +
  'staging prod the pr review sync team standup planning').split(' '));
export function people(b) {
  const out = [];
  const add = (n) => { n = String(n || '').trim(); if (n && !NOT_PEOPLE.has(n.toLowerCase()) && !out.includes(n)) out.push(n); };
  const lw = b.live && b.live.who;
  if (lw && !/^(the one guy|he|him|you|the watcher|the routine|a helper)$/i.test(lw)) add(lw);
  const raw = String(b.raw_title || b.title || '');
  const re = /\b(?:[Ww]ith|[Aa]sk|[Aa]sking|[Cc]all|[Mm]eet|[Ee]mail|[Tt]ext|[Tt]ell|[Ii]nvite|[Rr]eply to|[Aa]nswer|[Ff]rom|[Ff]or|[Ss]ee)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)/g;
  for (let m; (m = re.exec(raw));) add(m[1].split(' ')[0]);
  for (const m of raw.matchAll(/\b([A-Z][a-z]+)'s\b/g)) add(m[1]);
  return out.slice(0, 3);
}
const list = (xs) => xs.length < 2 ? xs[0] : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1];
export async function coordinate(id, who, words) {
  const b = OneGuy.board.byId[id];
  if (!b || !OneGuy.surface || !OneGuy.surface.say) return false;
  const names = list(who);
  OneGuy.orb?.say?.(`${names} ${who.length > 1 ? 'are' : 'is'} in this. let's sort it out.`, { ms: 2600 });
  try { OneGuy.focus(id); } catch (e) { /* the thread still opens below */ }
  const when = words.replace(' · ', ' ');
  const ask = `put this on ${when}. ${names} ${who.length > 1 ? 'are' : 'is'} in it: what's the next step? ` +
    `propose it in one line (like "want me to ask ${who[0]} for ${when}?") and wait for my ok. don't contact anyone yet.`;
  // the slot is held, not settled, until the people in it say yes: the week shows it as a placeholder
  judge('schedule', b, b.when && b.when.at, 'held for ' + names);   // a hold is a schedule with a 'held for' note (world.holds)
  await OneGuy.surface.say(b, ask, { mode: 'say', kickoff: true });
  (OneGuy._hooks?.refresh || OneGuy.refresh)?.({ quiet: true });
  return true;
}

// ------------------------------------------------------------------ drop on time = schedule
// A drag over the week shows where it would land ("thu 12 · 2 pm"); the hour comes from where
// you let go between that day's timed cards, so it always matches what you see. The day's label
// band = any time that day. Letting go anywhere off the days unschedules it. A drop is a judgment
// (kind: schedule, item → slot) and writes the item's when through the tasklist (op: schedule).
const pad = (n) => String(n).padStart(2, '0');
const isoDay = (t) => { const d = new Date(t); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const dayWords = (t) => {
  const i = Math.round((dayStart(t) - dayStart(Date.now())) / DAY);
  const d = new Date(t);
  return i === 0 ? 'today' : i === 1 ? 'tomorrow' : d.toLocaleDateString([], { weekday: 'short' }).toLowerCase() + ' ' + d.getDate();
};
const minsOf = (t) => { const d = new Date(t); return d.getHours() * 60 + d.getMinutes(); };
const snap = (m) => clampN(Math.round(m / 30) * 30, 7 * 60, 22 * 60);
const clampN = (v, a, b) => Math.max(a, Math.min(b, v));
const schedulable = (b) => b && b.kind === 'task' && b.status !== 'done';

let hintEl = null, hotRegion = null;
function hint(text, x, y) {
  if (!text) { hintEl?.remove(); hintEl = null; return; }
  if (!hintEl) { hintEl = el('div', 'og-slot-hint'); hintEl.setAttribute('role', 'status'); document.body.append(hintEl); }
  hintEl.textContent = text;
  hintEl.style.transform = `translate3d(${Math.round(x + 18)}px, ${Math.round(y - 34)}px, 0)`;
}
function glow(region) {
  if (hotRegion === region) return;
  hotRegion?.classList.remove('og-drop-day');
  hotRegion = region;
  region?.classList.add('og-drop-day');
}

// where a drop at (sx, sy) lands: {at: 'YYYY-MM-DD' | 'YYYY-MM-DDTHH:MM' | null, words} or null (no-op)
export function slotAt(sx, sy, dragged, canvas) {
  let reg = null;
  for (const hit of document.elementsFromPoint(sx, sy)) {
    const r = hit.closest && hit.closest('.og-region[data-cluster]');
    if (r) { reg = r; break; }
  }
  const cid = reg ? reg.dataset.cluster : '';
  if (cid === 'day:next' || cid === 'day:prev') return { region: reg, at: null, door: cid === 'day:next' ? 1 : -1, words: cid === 'day:next' ? (week ? 'the week after ›' : 'next week ›') : '‹ ' + (week === 1 ? 'this week' : 'back') };
  const m = /^day:(\d)$/.exec(cid);
  if (!m) return { region: null, at: null, words: cid === 'day:late' ? '' : 'no time' };
  const day = dayStart(Date.now()) + (week * 7 + Number(m[1])) * DAY + DAY / 2;
  const box = reg.getBoundingClientRect();
  const label = reg.querySelector('.og-region-l');
  const top = label ? label.getBoundingClientRect().bottom : box.top + 36;
  if (sy < top + 6) return { region: reg, at: isoDay(day), words: `${dayWords(day)} · any time` };
  // the day's timed cards, top to bottom, as (screen y, minutes)
  const marks = [];
  for (const [id, node] of canvas.els) {
    if (node === dragged) continue;
    const it = canvas.itemOf(id), b = OneGuy.board.byId[id];
    if (!it || it.cluster !== cid || !b || !b.when || b.when.all_day || b.when.kind === 'activity') continue;
    const t = whenTs(b);
    if (t == null || dayStart(t) !== dayStart(day)) continue;
    const r = node.getBoundingClientRect();
    marks.push({ y: r.top + r.height / 2, top: r.top, bot: r.bottom, m: minsOf(t) });
  }
  marks.sort((a, b) => a.y - b.y);
  const prev = [...marks].reverse().find((k) => k.y <= sy), next = marks.find((k) => k.y > sy);
  const STEP = 44;   // px per half hour beyond the last/first card
  let mins;
  const on = marks.find((k) => sy >= k.top && sy <= k.bot);      // let go ON a timed card = at its time (he'll name the clash)
  if (on) mins = on.m;
  else if (prev && next) mins = prev.m + (next.m - prev.m) * ((sy - prev.y) / Math.max(1, next.y - prev.y));
  else if (prev) mins = prev.m + 60 + Math.max(0, Math.floor((sy - prev.bot) / STEP)) * 30;
  else if (next) mins = next.m - 60 - Math.max(0, Math.floor((next.top - sy) / STEP)) * 30;
  else mins = 9 * 60 + Math.floor((sy - top) / STEP) * 30;
  mins = snap(mins);
  if (week === 0 && Number(m[1]) === 0) {        // today: never in the past
    const nowM = minsOf(Date.now());
    if (mins <= nowM) mins = snap(nowM + 30);
  }
  const at = `${isoDay(day)}T${pad(Math.floor(mins / 60))}:${pad(mins % 60)}`;
  return { region: reg, at, words: `${dayWords(day)} · ${clock(Date.parse(at))}` };
}

// is the pointer over a day or the tray? (then a release there is a time drop, not a flick to the rail)
export function over(e) {
  return document.elementsFromPoint(e.clientX, e.clientY).some((n) => /^day:/.test(n.dataset?.cluster || '') && n.classList.contains('og-region'));
}

// held near an edge, the week scrolls under the card (the phone stacks days; a wide week pans).
// Each frame pans the view and replays the pointer where it rests, so the card and hint follow.
let edgeRaf = 0;
function edgeScroll(e, canvas) {
  const EDGE = 64, vw = window.innerWidth, vh = window.innerHeight;
  const phone = vw < 700, TOP = phone ? EDGE + 40 : 40;     // desktop: the day labels sit near the top; keep them droppable
  const dy = e.clientY < TOP ? 1 : e.clientY > vh - EDGE ? -1 : 0;
  const dx = e.clientX < EDGE ? 1 : e.clientX > vw - EDGE - 8 ? -1 : 0;
  cancelAnimationFrame(edgeRaf);
  if (!dx && !dy) return;
  const at = { clientX: e.clientX, clientY: e.clientY, pointerId: e.pointerId, pointerType: e.pointerType, isPrimary: true, bubbles: true };
  edgeRaf = requestAnimationFrame(() => {
    if (!document.body.classList.contains('og-dragging')) return;
    const v = canvas.view, x0 = v.x, y0 = v.y;
    v.x += dx * 9; v.y += dy * 11;
    canvas.clampView();
    if (v.x === x0 && v.y === y0) return;      // reached the end
    canvas.applyView(false);
    window.dispatchEvent(new PointerEvent('pointermove', at));
  });
}

// called by gestures while a card is dragged in the Time lens
let dwell = null;
export function hover(id, e, node, canvas) {
  edgeScroll(e, canvas);
  const b = OneGuy.board.byId[id];
  if (!schedulable(b)) { hint(''); glow(null); return; }
  dragging = id;
  const s = slotAt(e.clientX, e.clientY, node, canvas);
  // hold a card over a door and the week turns under it (calendar apps do the same at an edge)
  if (s.door) {
    if (!dwell || dwell.door !== s.door) { clearTimeout(dwell?.t); dwell = { door: s.door, t: setTimeout(() => { dwell = null; setWeek(week + s.door); }, 650) }; }
  } else if (dwell) { clearTimeout(dwell.t); dwell = null; }
  glow(s.region);
  hint(s.at ? s.words : b.when ? 'take it off the calendar' : '', e.clientX, e.clientY);
}

// called by gestures on release in the Time lens. true = handled (scheduled / moved / unscheduled)
export function drop(id, e, node, canvas) {
  hint(''); glow(null); cancelAnimationFrame(edgeRaf);
  clearTimeout(dwell?.t); dwell = null; dragging = null;
  const b = OneGuy.board.byId[id];
  if (!b) return false;
  if (!schedulable(b)) {
    if (b.kind !== 'task') OneGuy.orb?.say?.('only to-dos go on the calendar for now.', { ms: 2400 });
    return false;
  }
  const s = slotAt(e.clientX, e.clientY, node, canvas);
  const was = b.when && b.when.kind !== 'activity' ? String(b.when.at || '') : null;
  if (s.door) { canvas.layout(OneGuy.board, { animate: true }); return true; }   // let go on a door: it goes back to its place
  if (!s.at && !was) return false;                 // an unscheduled card let go off the days: nothing to do
  if ((s.at || null) === (was ? was.slice(0, s.at && s.at.length === 10 ? 10 : 16) : null)) return false;
  const hits = collisions(b, s.at);
  if (!hits.length) { schedule(b, s.at, canvas, s.words); return true; }
  // he names the collision before anything is written (the life-aware part): the card waits at the slot
  const prev = { when: b.when, due: b.due };
  b.when = { at: s.at, kind: b.when && b.when.kind === 'event' ? 'event' : 'due', all_day: false };
  canvas.layout(OneGuy.board, { animate: true });
  const other = (headlineOf(hits[0]).title || hits[0].title || 'something').toLowerCase().replace(/[.!?]+$/, '');
  const more = hits.length > 1 ? ` and ${hits.length - 1} more` : '';
  ask(`that's on top of ${/^(the|a|an|my|your)\b/.test(other) ? '' : 'the '}${other}${more}. still want ${clock(Date.parse(s.at))}?`, 'put it there', 'leave it').then((yes) => {
    b.when = prev.when; b.due = prev.due;
    if (!b.when) delete b.when;
    if (yes) schedule(b, s.at, canvas, s.words);
    else canvas.layout(OneGuy.board, { animate: true });
  });
  return true;
}

// ------------------------------------------------------------------ collisions
// What a slot would sit on top of: timed to-dos and events that day (routines don't count, they
// run on their own). An item with no end takes an hour.
const HOUR = 3600000;
export function collisions(b, at, blocks = OneGuy.board.blocks) {
  if (!at || at.length === 10) return [];
  const s0 = Date.parse(at);
  const own = b.when && b.when.end && whenTs(b) != null ? Date.parse(b.when.end) - whenTs(b) : 0;
  const s1 = s0 + (own > 0 ? own : HOUR);
  return (blocks || []).filter((x) => {
    if (x.id === b.id || !x.when || x.when.all_day || x.kind === 'job' || x.status === 'done') return false;
    if (x.when.kind !== 'due' && x.when.kind !== 'event') return false;
    const t = whenTs(x);
    if (t == null) return false;
    const e = x.when.end ? Date.parse(x.when.end) : t + HOUR;
    return t < s1 && e > s0;
  }).sort((a, c) => Math.abs(whenTs(a) - s0) - Math.abs(whenTs(c) - s0));   // the nearest one gets named
}

// a small yes/no above the bar, in his voice. Resolves false on 'no', Esc, or after a while.
function ask(text, yes, no, ms = 9000) {
  return new Promise((done) => {
    document.querySelector('.og-ask')?.dispatchEvent(new Event('og-ask-close'));
    const box = el('div', 'og-ask');
    box.setAttribute('role', 'alertdialog');
    const y = el('button', 'og-ask-yes', yes), n = el('button', 'og-ask-no', no);
    y.type = n.type = 'button';
    box.append(el('span', 'og-ask-t', text), y, n);
    document.body.append(box);
    let t = 0;
    const end = (v) => { clearTimeout(t); box.remove(); document.removeEventListener('keydown', key, true); done(v); };
    const key = (e) => { if (e.key === 'Escape') { e.stopPropagation(); end(false); } else if (e.key === 'Enter' && document.activeElement === y) { e.preventDefault(); end(true); } };
    y.onclick = () => end(true);
    n.onclick = () => end(false);
    box.addEventListener('og-ask-close', () => end(false));
    document.addEventListener('keydown', key, true);
    t = setTimeout(() => end(false), ms);
    y.focus({ preventScroll: true });
  });
}

export async function schedule(b, at, canvas, words, { undoing = false } = {}) {
  const prev = { when: b.when, due: b.due };
  const wasAt = b.when && b.when.kind !== 'activity' ? String(b.when.at || '') || null : null;
  // optimistic: the card goes to its slot now; the list catches up behind it
  if (at) { b.when = { at, kind: b.when && b.when.kind === 'event' ? 'event' : 'due', all_day: at.length === 10 }; b.due = at; }
  else { delete b.when; b.due = null; }
  canvas.layout(OneGuy.board, { animate: true });
  canvas.logJudgment('schedule', b, at || null);
  setTimeout(() => {
    const n = canvas.els.get(b.id);
    if (n && !n.classList.contains('og-gone')) { n.classList.add('og-landed'); setTimeout(() => n.classList.remove('og-landed'), 900); }
    OneGuy.emit('fly', { phase: 'land' });
  }, 520);
  if (undoing || !(OneGuy.talk?.showUndo || OneGuy.talk?.undo)) OneGuy.orb?.say?.(at ? `on for ${words}.` : 'off the calendar.', { ms: 2200 });   // else the undo pill says it, once
  try {
    const r = await OneGuy.api('/api/board/task', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: b.id, op: 'schedule', due: at || null }) });
    const res = await r.json().catch(() => ({}));
    if (!r.ok || !res.ok) throw new Error(res.error || 'the list said no');
    if (res.block_id && res.block_id !== b.id) canvas.rekey(b.id, res.block_id);
    const id = res.block_id || b.id;
    OneGuy.emit('scheduled', { block: b, id, at: at || null, words });
    const who = at && !undoing ? people(b) : [];
    await (OneGuy._hooks?.refresh || OneGuy.refresh)?.({ quiet: true });
    // one tap puts it back: a mis-drop (on the phone especially) never costs a re-drag
    // one tap puts it back, through the bar's own undo pill: its door when the bar has one (showUndo:
    // the same op:schedule, to the old slot), else the older pill that runs a function
    if (!undoing) {
      const summary = at ? `on for ${words}` : 'off the calendar';
      if (OneGuy.talk?.showUndo) OneGuy.talk.showUndo({ token: 'time', post: '/api/board/task', body: { op: 'schedule', id, due: wasAt || null } },
        summary, { onUndone: () => judge('schedule', id, wasAt || null, 'undo') });
      else OneGuy.talk?.undo?.(() => undo(id, wasAt, canvas), summary, 6000);
    }
    if (who.length) coordinate(id, who, words);
    return res;
  } catch (err) {
    b.when = prev.when; b.due = prev.due;
    if (!b.when) delete b.when;
    canvas.layout(OneGuy.board, { animate: true });
    OneGuy.orb?.say?.(`couldn't move it: ${String(err.message || err).slice(0, 60)}`, { ms: 3200 });
    return null;
  }
}
