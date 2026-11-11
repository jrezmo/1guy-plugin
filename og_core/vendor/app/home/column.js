// 1guy home · the chat column (Phase 2, audit §5.2 / L2). ONE position: a fixed right
// column (phone: a full-height sheet), bound to ONE thread — the target item's thread from
// threads.json (resolve-or-start happens through the surface runner the bloom already uses).
// When it's about a card, that card docks at the column's left edge (the canvas shifts left).
//
//   OG.column.open(block, { question?, why? })   // question = the promote-morph's ask
//   OG.column.close
//   OG.column.current                          // the block it's bound to (talk.js routes sends here)
//
// The bloom keeps the face and actions; the column holds the conversation — the bloom's
// thread slot unmounts when the column opens for the same block (never two copies).
// The reply strip above the talk bar is RETIRED: his words land here now.
import { el, EASE_OUT, REDUCED, store } from './core.js';
import { controller, plainError } from './surface.js';
import './sounds.js';

const OG = window.OneGuy;
const isMobile = () => window.innerWidth <= 700;
const SHOW_LAST = 40;

const column = el('section', 'og-col');
column.setAttribute('role', 'complementary');
column.setAttribute('aria-label', 'conversation');
const hd = el('header', 'og-col-hd');
const dot = el('span', 'og-col-dot');
const ttl = el('span', 'og-col-ttl', '');
const why = el('span', 'og-col-why');
const stop = el('button', 'og-col-stop', 'stop');
stop.type = 'button';
stop.title = 'cut him off (Esc)';
// where this conversation lives — today › pile › card — and the Hermes session behind it. The chat
// crosses cards and sessions; its header is the way back to both (the user).
const crumbs = el('nav', 'og-col-crumbs');
crumbs.setAttribute('aria-label', 'where this conversation lives');
const note = el('div', 'og-col-note');
note.hidden = true;
const openHermes = el('a', 'og-col-open', 'hermes ↗');
openHermes.hidden = true;
const x = el('button', 'og-col-x', '×');
x.type = 'button';
x.setAttribute('aria-label', 'fold the conversation back into the bar');
// two rows: where it lives (+ close) on top; what he's doing, stop and the Hermes door underneath
const row1 = el('div', 'og-col-row1'), row2 = el('div', 'og-col-row2');
row1.append(dot, crumbs, ttl, x);
row2.append(why, stop, openHermes);
hd.append(row1, row2);
ttl.hidden = true;        // the breadcrumb's last crumb is the title now
const list = el('div', 'og-col-turns');
list.setAttribute('aria-live', 'polite');
const qEl = el('div', 'og-col-q');
qEl.hidden = true;
column.append(hd, note, qEl, list);
document.body.append(column);

let cur = null;         // the bound block
let surface = null;
let seen = new WeakSet();
let question = null;

const plainTitle = (b) => (b && ((b.headline && b.headline.title) || b.title || b.id)) || 'this';

function setBody() {
  document.body.classList.toggle('og-col-open', !!cur);
  document.documentElement.style.setProperty('--og-col-w', cur ? (isMobile() ? '100vw' : 'min(420px, 44vw)') : '0px');   // a tablet keeps room for the card beside it
  column.classList.toggle('og-mobile', isMobile());
}

function rowEl(t) {
  const row = el('div', 'sf-turn og-col-row sf-' + t.role + (t.live ? ' sf-live' : '') + (t.cut ? ' sf-cut' : ''));
  if (t.role === 'j') {
    row.append(el('span', 'sf-text', t.text));
    if (t.mode && t.mode !== 'say') row.append(el('span', 'sf-mtag sf-mtag-' + t.mode, t.mode));
    if (t.queued) row.append(el('span', 'sf-note', 'waits for him to finish'));
  } else {
    const txt = el('span', 'sf-text', t.text || (t.live ? '' : '…'));
    row.append(txt);
    if (t.live && !t.cut) row.append(el('span', 'sf-caret'));
    if (t.error) row.append(el('span', 'sf-err', t.error));
  }
  return row;
}

function paintCrumbs() {
  crumbs.replaceChildren();
  if (!cur) return;
  const w = OG.where ? OG.where(cur.id) : { view: 'today', place: null, title: null, onBoard: false };
  const name = String(w.title || plainTitle(cur)).replace(/^(?:1guy\s*·\s*[\w-]+:\s*)+/, '');   // a card's name, not a session title
  const sep = () => el('i', 'og-col-sep', '›');
  crumbs.append(el('span', 'og-col-crumb', w.view));
  if (w.place) crumbs.append(sep(), el('span', 'og-col-crumb', w.place));
  crumbs.append(sep());
  if (w.onBoard) {
    const b = el('button', 'og-col-crumb og-col-here', name);
    b.type = 'button';
    b.title = 'show this card';
    b.onclick = (e) => { e.stopPropagation(); if (!(OG.focused && OG.focused.id === cur.id)) OG.focus(cur.id); };
    crumbs.append(b);
  } else crumbs.append(el('span', 'og-col-crumb og-col-here', cur.id === 'home' ? 'just talking' : name));
}
// always there once the thread has a session: the door to the same conversation in Hermes, by id
function paintHermes(s) {
  const sid = s && s.opened && (s.opened.session_id || (s.opened.desktop && s.opened.desktop.session_id));
  openHermes.hidden = !sid;
  if (!sid) return;
  openHermes.href = 'hermes://open/' + encodeURIComponent(sid);
  openHermes.title = 'open this conversation in Hermes · session ' + sid;
  openHermes.replaceChildren('hermes ↗ ', el('span', 'og-col-sid', String(sid).slice(-6)));
  openHermes.onclick = (e) => { e.preventDefault(); OG.openSession(sid); };
}
// one quiet row when the router took a line somewhere new: "→ started a new card: X" / "→ went to Y"
let noteT = 0;
function say(text) {
  if (!text) { note.hidden = true; return; }
  note.textContent = text;
  note.hidden = false;
  if (OG.emit) OG.emit('route-note', { text });     // the open card shows it too (a phone has no column)
  clearTimeout(noteT);
  noteT = setTimeout(() => { note.hidden = true; }, 15000);
}

const view = {
  onChange(s) {
    if (!cur || s.id !== cur.id) return;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    column.classList.toggle('og-col-streaming', s.streaming);
    stop.hidden = !s.streaming;
    paintHermes(s);
    const hiddenN = Math.max(0, s.turns.length - SHOW_LAST);
    seen = seen || new WeakSet();
    list.replaceChildren(...s.turns.slice(hiddenN).map((t) => {
      const row = rowEl(t);
      if (seen.has(t)) row.classList.add('sf-seen'); else seen.add(t);
      return row;
    }));
    if (atBottom || s.streaming) list.scrollTop = list.scrollHeight;
  },
};

async function open(block, opts = {}) {
  if (!block) return close();
  if (OG.sample) return null;   // the sample desk: a sample card has no thread, and nothing said there could be kept
  if (cur && cur.id === block.id) { showQuestion(opts.question); return surface; }
  if (cur) close({ instant: true, silent: true });
  cur = block;
  seen = new WeakSet();
  store.setFocus(block.id);   // remember the conversation across reloads (Nov 9 fix)
  ttl.textContent = plainTitle(block).length > 34 ? plainTitle(block).slice(0, 33) + '…' : plainTitle(block);
  paintCrumbs();
  openHermes.hidden = true;
  if (!opts.keepNote) say('');
  why.textContent = opts.why || '';
  dot.className = 'og-col-dot';
  qEl.hidden = true;
  question = opts.question || null;
  setBody();
  surface = controller(block);
  surface.views.add(view);
  view.onChange(surface);
  const mine = surface;
  try { await mine.open(); } catch (e) { OG.toast && OG.toast(plainError(e.message || e), true); }
  // he closed the card or moved on while the thread was opening: this open is stale. Announcing it would make
  // the canvas re-focus a card he just left (an Esc that "did nothing", seen 1 run in 8 under load).
  if (cur !== block) return mine;
  showQuestion(opts.question);
  if (OG.emit) OG.emit('column', { open: true, block });
  if (OG.sounds && !opts.quiet) OG.sounds.play('send');
  if (!REDUCED && !opts.instant) {
    column.animate([{ transform: 'translateY(24px)', opacity: 0 }, { transform: 'none', opacity: 1 }],
      { duration: 300, easing: EASE_OUT });
  }
  return surface;
}

function showQuestion(q) {
  question = q || question;
  if (!question) { qEl.hidden = true; return; }
  qEl.hidden = false;
  qEl.replaceChildren(el('span', 'og-col-q-t', question),
    el('span', 'og-col-q-h', 'answer up in the bar'));
}

function close(opts = {}) {
  if (!cur) return;
  const was = cur;
  if (surface) { surface.views.delete(view); }
  surface = null;
  if (store.data.view && store.data.view.focus === was.id) store.setFocus(null);   // folded up for real
  cur = null;
  question = null;
  setBody();
  if (OG.emit) OG.emit('column', { open: false, block: was });
  if (!opts.silent && OG.sounds) OG.sounds.play('land');
}

stop.addEventListener('click', () => { if (surface && surface.streaming) surface.interrupt(); });
x.addEventListener('click', () => close());
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const a = document.activeElement;
  if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && a.classList.contains('tk-input')) {
    // the bar's own Esc logic runs first; only fold when the bar is empty of a cut
  }
  if (cur && !(a && a.tagName === 'INPUT' && a.value)) { if (OG.back && OG.focused) OG.back(); else close(); e.stopPropagation(); }   // one Esc = one step back (the shell must not also leave the lens)
}, true);
window.addEventListener('resize', setBody);

const api = {
  open,
  close,
  current: () => cur,
  surface: () => surface,
  has: (id) => !!(cur && cur.id === id),
  note: say,
};
if (OG && OG.on) for (const ev of ['lens', 'view', 'board']) OG.on(ev, paintCrumbs);
if (OG) OG.column = api;
export default api;
