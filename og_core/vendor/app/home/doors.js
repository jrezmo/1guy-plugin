// 1guy home · the two doors of the first run (docs/product.md, "The first 60 seconds").
// The server picks the door from what the home holds (board.first_run: 'a' | 'b' | null, his words in
// board.door; app/board/doors.py). This draws them inside the briefing; there is no second layout.
//   door A  a lived-in Hermes: the counted opener, the three that need you and the piles are already
//           the briefing's line and rail. This adds the last line: `pick one and i'll take it from here.`
//   door B  a sparse one: one big box. Every finished line (Enter, or a pause after a sentence) is a
//           card beside the box at once, then a to-do through POST /api/board/task (op add), the same
//           path the talk bar's "add to my list" takes. Three lines in, the board is read again so
//           untangle's piles show up on the rail (they preview before yes, like any proposal).
//   sample  door B's `look around a sample desk` reloads with ?sample=1 (core.js: read-only, own
//           namespace, no write leaves the page). A fixed chip and Esc leave it.
import OneGuy, { el, api, SAMPLE, REDUCED, EASE_OUT, springEase, sleep } from './core.js';

const PAUSE_MS = 1500;                  // a pause this long after a sentence ends the line
const SENTENCE = /[.!?…]["')\]]?\s*$/;
const MAX_LINE = 280;
const norm = (t) => String(t || '').replace(/^\s*(?:[-*•–]|\d+[.)])\s+/, '').replace(/\s+/g, ' ').trim();
const key = (t) => norm(t).toLowerCase().replace(/[.!?…]+$/, '');
const openTask = (b) => b && b.kind === 'task' && b.status !== 'done' && b.status !== 'parked';

function sampleUrl(on) {
  const u = new URL(location.href);
  if (on) u.searchParams.set('sample', '1'); else u.searchParams.delete('sample');
  return u.pathname + u.search + u.hash;
}
const hostSample = (on) => window.parent.postMessage({ type: 'oneguy:sample', on }, '*');
export const leaveSample = () => (window.ONEGUY_EMBED ? hostSample(false) : location.assign(sampleUrl(false)));

// the chip that says where you are and takes you back: fixed, on every view of the sample desk
export function sampleChip() {
  if (!SAMPLE) return null;
  document.body.classList.add('og-sample');
  const chip = el('button', 'og-sample-chip');
  chip.type = 'button';
  chip.append(el('span', 'og-sample-chip-t', 'sample desk'), el('span', 'og-sample-chip-x', '· leave'));
  chip.title = 'none of this is yours and nothing here is saved. tap (or Esc) to go back to your desk.';
  chip.setAttribute('aria-label', 'sample desk. leave and go back to your own desk');
  chip.onclick = leaveSample;
  document.body.append(chip);
  // Esc leaves, but only when nothing is open: with a card, a room, a menu or a dialog up, Esc is
  // theirs (it closes them). Asked on the window before any of them hears the key and closes.
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    const open = document.querySelector('.og-bloom, .og-inside, .og-ask-scrim, #og-menu:not([hidden])')
      || document.body.classList.contains('og-drawer-open') || document.body.classList.contains('og-focusing');
    if (!open) leaveSample();
  }, true);
  return chip;
}

export function createDoors({ col, wrap, onChange }) {
  let which = null;                     // the door on screen: 'a' | 'b' | null
  let words = {};
  let stuck = false;                    // door B stays for this visit even after the first judgment ends the first run
  const lines = new Map();              // key → {text, id, node, state: pending|saved|failed}
  let queue = Promise.resolve();
  let pauseT = 0, refreshT = 0, sorting = false, sortedFor = 0, noPile = false;

  // ---- door B: the box and the cards beside it
  const box = el('section', 'og-doorb');
  box.hidden = true;
  const pad = el('div', 'og-doorb-pad');
  const ta = el('textarea', 'og-doorb-in');
  ta.rows = 5;
  ta.spellcheck = false;
  ta.setAttribute('autocapitalize', 'sentences');
  ta.setAttribute('enterkeyhint', 'enter');
  const foot = el('div', 'og-doorb-foot');
  const hint = el('span', 'og-doorb-hint');
  const sample = el('button', 'og-doorb-sample');
  sample.type = 'button';
  sample.onclick = () => (window.ONEGUY_EMBED ? hostSample(true) : location.assign(sampleUrl(true)));
  foot.append(hint, sample);
  pad.append(ta, foot);
  const side = el('div', 'og-doorb-side');
  const cards = el('div', 'og-doorb-cards');
  cards.setAttribute('aria-live', 'polite');
  const status = el('div', 'og-doorb-status');
  side.append(cards, status);
  box.append(pad, side);
  col.insertBefore(box, wrap);
  // ---- both doors: his last line, under the rail
  const close = el('p', 'og-door-close');
  close.hidden = true;
  col.insertBefore(close, wrap.nextSibling);

  function cardNode(L) {
    const n = el('div', 'og-doorb-card');
    n.tabIndex = 0;
    n.setAttribute('role', 'button');
    n.append(el('i', 'og-doorb-dot'), el('span', 'og-doorb-t', L.text), el('span', 'og-doorb-s'));
    const go = () => open(L);
    n.onclick = go;
    n.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    if (!REDUCED) n.animate([{ opacity: 0, transform: 'translateX(-28px) scale(.92)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: springEase(8, 9) });
    return n;
  }
  function paint(L) {
    L.node.dataset.state = L.state;
    if (L.id) L.node.dataset.id = L.id; else delete L.node.dataset.id;
    L.node.querySelector('.og-doorb-s').textContent = L.state === 'failed' ? 'not saved. tap to try again.' : '';
    L.node.setAttribute('aria-label', L.text + (L.state === 'failed' ? '. not saved, tap to try again' : L.state === 'pending' ? '. saving' : '. on your list'));
  }

  // the block a line became: by the id the tasklist gave it, else by its words (an item's id changes
  // when it gets its permanent key)
  function blockOf(L) {
    const B = OneGuy.board;
    if (L.id && B.byId[L.id] && openTask(B.byId[L.id])) return B.byId[L.id];
    return B.blocks.find((b) => openTask(b) && (key(b.title) === key(L.text) || key(b.detail && b.detail.text) === key(L.text))) || null;
  }

  function save(L) {
    L.state = 'pending';
    paint(L);
    L.saved = queue = queue.then(async () => {
      try {
        const r = await api.post('/api/board/task', { op: 'add', text: L.text, origin: '1guy first run' });
        if (!r || r.ok === false) throw new Error((r && r.error) || 'not saved');
        L.id = r.block_id || null;
        L.state = 'saved';
      } catch (err) {
        L.state = 'failed';
        console.warn('[door] line not saved', err);
      }
      paint(L);
      clearTimeout(refreshT);
      refreshT = setTimeout(() => OneGuy.refresh({ quiet: true }), 350);   // the real card joins the board
    });
    return L.saved;
  }

  function add(text) {
    const t = norm(text).slice(0, MAX_LINE).replace(/([^.])\.$/, '$1');   // a to-do doesn't end in a full stop
    const k = key(t);
    if (k.length < 2 || lines.has(k)) return false;
    const L = { text: t, id: null, state: 'pending', node: null, typed: true };
    L.node = cardNode(L);
    lines.set(k, L);
    cards.append(L.node);
    L.node.scrollIntoView({ block: 'nearest', behavior: REDUCED ? 'auto' : 'smooth' });
    save(L);
    afterLines();
    return true;
  }

  // every finished line in the box that isn't a card yet becomes one
  function scan(all) {
    const parts = ta.value.split('\n');
    const last = parts.pop();
    for (const p of parts) add(p);
    if (all && last.trim()) add(last);
  }
  // phone: the box is as tall as what he has typed (home.css caps it), so the cards under it stay close
  const grow = () => {
    if (!document.body.classList.contains('og-mobile')) { ta.style.height = ''; return; }
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  };
  ta.addEventListener('focus', () => setTimeout(() => { if (document.activeElement === ta) ta.scrollIntoView({ block: 'nearest' }); }, 350));   // after the keyboard is up
  ta.addEventListener('input', () => {
    grow();
    scan(false);
    clearTimeout(pauseT);
    pauseT = setTimeout(() => {         // a pause after a sentence: the line is finished, the box moves on
      const v = ta.value, tail = v.slice(v.lastIndexOf('\n') + 1);
      if (!SENTENCE.test(tail) || norm(tail).length < 2) return;
      const atEnd = ta.selectionStart === v.length;
      scan(true);
      if (atEnd) { ta.value = v.replace(/\s*$/, '') + '\n'; grow(); ta.scrollTop = ta.scrollHeight; }
    }, PAUSE_MS);
  });
  ta.addEventListener('keydown', (e) => {
    e.stopPropagation();                // the page's own keys ('/', arrows, Esc) never fire while writing
    if (e.key === 'Escape') ta.blur();
  });

  async function open(L) {
    if (L.state === 'failed') { save(L); return; }
    if (L.state === 'pending') await L.saved;
    let b = blockOf(L);
    if (!b) { await OneGuy.refresh({ quiet: true }); b = blockOf(L); }
    if (b) OneGuy.view('briefing', { focus: b.id, srcEl: L.node });   // the card with its thread, as everywhere
    else OneGuy.toast('that one isn’t on the board yet. give it a second.');
  }

  const count = () => [...lines.values()].filter((L) => L.state !== 'failed').length;
  const piles = () => OneGuy.board.blocks.filter((b) => b.kind === 'proposal' && b.status === 'proposed' && b.detail && b.detail.writer === 'untangle');

  // three lines down: he sorts them. Untangle runs server-side when the board is read (one pass per
  // real change, in the background), so read it again a few times and let the piles land on the rail.
  async function afterLines() {
    paintStatus();
    const n = count();
    if (n < (words.pile_after || 3) || sorting || n === sortedFor) return;
    sorting = true;
    paintStatus();
    await queue;
    let seen = n;
    for (const wait of [700, 1600, 2600, 4000]) {
      await OneGuy.refresh({ quiet: true });
      await sleep(wait);
      if (count() !== seen) { seen = count(); await queue; continue; }   // still writing: sort what's there now
      if (piles().length) break;
    }
    await OneGuy.refresh({ quiet: true });
    sortedFor = seen;
    sorting = false;
    // he did what the hint asked; if nothing piles up, say so instead of going quiet
    noPile = !piles().length;
    paintStatus();
    if (count() !== seen) afterLines();
  }
  function paintStatus() {
    const n = count(), need = words.pile_after || 3;
    status.textContent = sorting ? 'sorting these…' : (which === 'b' && noPile && n >= need && !piles().length) ? 'none of these go together yet. keep going, or pick one.' : '';
    const ready = which === 'b' && n >= need && !sorting;
    const text = which === 'a' ? words.close : ready ? words.close : '';
    if (close.textContent !== (text || '')) {
      close.textContent = text || '';
      if (text && !REDUCED) close.animate([{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: EASE_OUT });
    }
    close.hidden = !text;
    box.classList.toggle('og-doorb-has', lines.size > 0);
  }

  // the board moved: cards that were already on the list join the ones typed here; a typed line that
  // was finished or deleted elsewhere leaves
  function sync(board) {
    for (const b of board.blocks.filter(openTask)) {
      const k = key(b.title);
      const L = lines.get(k) || [...lines.values()].find((x) => x.id === b.id);
      if (L) { if (L.state === 'saved') L.id = b.id; continue; }
      const N = { text: norm(b.title), id: b.id, state: 'saved', node: null, typed: false };
      N.node = cardNode(N);
      lines.set(k, N);
      cards.append(N.node);
    }
    for (const [k, L] of lines) {
      if (L.state !== 'saved') continue;
      const b = blockOf(L);
      if (b) L.id = b.id;
      else if (!L.typed || (L.goneAt && Date.now() - L.goneAt > 20000)) { L.node.remove(); lines.delete(k); continue; }
      else if (!L.goneAt) L.goneAt = Date.now();
      if (b) L.goneAt = 0;
      paint(L);
    }
  }

  function render(board) {
    const d = !SAMPLE && board.door && typeof board.door === 'object' ? board.door : null;
    const next = SAMPLE ? null : (board.first_run === 'a' || board.first_run === 'b') ? board.first_run : (stuck ? 'b' : null);
    if (d && d.which === next) words = d;
    if (next === 'b') stuck = true;
    const changed = next !== which;
    which = next;
    box.hidden = which !== 'b';
    document.body.classList.toggle('og-door-a', which === 'a');
    document.body.classList.toggle('og-door-b', which === 'b');
    if (which === 'b') {
      ta.setAttribute('aria-label', words.ask || 'what are you in the middle of?');
      hint.textContent = words.hint || '';
      sample.textContent = words.sample || '';
      sample.hidden = !words.sample;
      sync(board);
    }
    paintStatus();
    if (changed && onChange) onChange(which);
  }

  const doors = {
    render,
    get which() { return which; },
    get lines() { return count(); },    // door B: lines down so far (the email/calendar offer waits for the first)
    get line() {                        // door B: his question. the sample desk: where you are. else the briefing's own line
      const B = OneGuy.board;
      if (SAMPLE) return (B.sample && B.briefing && B.briefing.line) || '';
      return which === 'b' ? (words.ask || '') : '';
    },
    // door B's rail holds only what he proposes (the piles); the to-dos are the cards beside the box
    // door A's rail is in his order: the three that need you, then the piles, then the rest
    rail(forYou) {
      if (which === 'b') return forYou.filter((b) => b.kind === 'proposal');
      const order = which === 'a' && OneGuy.board.briefing && OneGuy.board.briefing.for_you;
      if (!Array.isArray(order)) return forYou;
      const at = (b) => { const i = order.indexOf(b.id); return i < 0 ? order.length : i; };
      return [...forYou].sort((x, y) => at(x) - at(y));
    },
    nodes: () => [box, close],
    focus() { if (which === 'b' && !document.body.classList.contains('og-mobile')) ta.focus({ preventScroll: true }); },
    // dictation (the talk bar's mic): what was said goes into the box, one line per sentence
    take(text) {
      if (which !== 'b' || !String(text || '').trim()) return false;
      const said = String(text).trim().split(/(?<=[.!?…])\s+/).join('\n');
      ta.value = (ta.value && !ta.value.endsWith('\n') ? ta.value + '\n' : ta.value) + said + '\n';
      scan(false);
      return true;
    },
    box: ta,
  };
  OneGuy.door = doors;
  return doors;
}
