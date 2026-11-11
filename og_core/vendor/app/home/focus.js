// 1guy home · focus: the ONE card grammar (docs/card-grammar.md). Every kind, every lens.
//
//   the face     headline + why + ONE verb, always in the same corner. Tap the card = focus.
//   focus        the item on the left, his thread already open beside it, "behind this" collapsed,
//                and exactly three slots in the same place for every kind:
//                  primary (amber, ⏎) resolves it · later (L) puts it off · ⋯ holds everything rare
//
// Kinds supply data, never buttons: a kind may register `grammar(block)` returning any of
// { primary, enter, later, more, behind, connections } to override what's derived here from the
// block's own actions. Everything a kind's old bloom drew as a control (work-with-me, option
// buttons, a "details" toggle) is taken out here and re-expressed in the grammar.
//
//   grammar(block)            → { primary, enter?, later, more[] }   (each: {label, run(block)})
//   faceVerb(block, node)     → puts the one verb on a face
//   compose(block, panel)     → fills a focus panel; returns { slots } for keys
import OneGuy, { el, headlineOf, store } from './core.js';

const act = (b, id) => (b.actions || []).find((a) => a.id === id) || null;
const live = (b) => OneGuy.board.byId[b.id] || b;
const talk = () => { try { OneGuy.talk && OneGuy.talk.focus && OneGuy.talk.focus(); } catch (e) { /* ignore */ } };
const run = (a) => (b) => OneGuy.runAction(live(b), a);
const enter = (b, srcEl) => { OneGuy.focus(b.id, srcEl ? { srcEl } : {}); setTimeout(talk, 360); };

const MORE_LABEL = { edit: 'edit the text', delete: 'delete it', steer: 'steer it (send a note)', reject: 'send it back', run: 'run it now', dismiss: 'dismiss it', keep: 'keep it', approve: 'approve', github: 'open on github', done: 'mark it done' };
const SKIP = new Set(['surface', 'build']);   // "open work surface" IS focus now

// ------------------------------------------------------------------ the grammar
export function grammar(block) {
  const b = block || {};
  const own = (() => { try { const k = OneGuy.kinds[b.kind]; return (k && k.grammar && k.grammar(b)) || {}; } catch (e) { return {}; } })();
  const opts = b.headline && b.headline.ask && Array.isArray(b.headline.ask.options) ? b.headline.ask.options.filter((o) => o && (o.label || o.say)) : [];
  const used = new Set();
  const use = (id) => { const a = act(b, id); if (a) used.add(id); return a; };
  let primary = null, enterLabel = null;
  if (opts.length) primary = { label: opts[0].label || opts[0].say, run: (x) => OneGuy.decide(live(x), opts[0]) };
  else if (b.status === 'failed' && use('run')) primary = { label: 'run it again', run: run(act(b, 'run')) };
  else if (b.kind === 'crew' && b.status === 'needs-you' && use('approve')) primary = { label: 'approve', run: run(act(b, 'approve')) };
  else if (b.kind === 'proposal' && use('keep')) primary = { label: editsOf(b) ? act(b, 'keep').label || 'do it' : 'keep', run: run(act(b, 'keep')), preview: !!editsOf(b) };
  else if (use('done')) {
    primary = { label: b.status === 'done' ? 'reopen' : 'done', run: run(act(b, 'done')) };
    if (b.status === 'needs-you') enterLabel = 'answer';        // a question: the face invites you in; done resolves it
  } else if (b.kind === 'artifact' && b.source && /^(https?:\/\/|\/(?!\/))/i.test(String(b.source.url || ''))) {
    primary = { label: 'read', run: (x) => OneGuy.openUrl(x.source.url) };
  } else if (use('open')) {
    primary = { label: 'open', run: run(act(b, 'open')) };        // a tool or page whose whole point is to be opened
  } else {
    // nothing to resolve: the verb is the way in, and ⏎ hands you the talk bar
    // a chat card's verb starts a thread ABOUT that chat (one item = one thread); it does not post into the chat
    // itself, so it can't say 'reply'. A talk-bar thread does continue, so it keeps the word.
    const word = b.kind === 'session' ? 'talk about this' : b.kind === 'talk' ? 'reply' : b.kind === 'job' ? 'look' : b.kind === 'project' ? 'open' : 'look';
    primary = { label: word, run: () => talk(), enterOnly: true };
    enterLabel = word;
  }
  // later: one word. A proposal's "not now" is its own way of putting off; everything else parks.
  let later = { label: 'later', run: (x) => OneGuy.park(x.id) };
  if (b.kind === 'proposal' && act(b, 'dismiss')) { used.add('dismiss'); later = { label: 'not now', run: run(act(b, 'dismiss')) }; }
  // ⋯ : everything rare
  const more = [];
  opts.slice(1).forEach((o) => more.push({ label: o.label || o.say, run: (x) => OneGuy.decide(live(x), o) }));
  for (const a of b.actions || []) {
    if (used.has(a.id) || SKIP.has(a.id) || a.surface) continue;
    if (a.id === 'done' && opts.length) { more.push({ label: MORE_LABEL.done, run: run(a) }); continue; }
    more.push({ label: MORE_LABEL[a.id] || a.label || a.id, run: a.href && !a.post ? () => OneGuy.openUrl(a.href) : run(a), danger: a.id === 'delete' });
  }
  const sid = (b.kind === 'session' && b.detail && b.detail.session_id) || null;
  if (sid) more.push({ label: 'open in hermes ↗', run: () => OneGuy.openSession(sid) });
  return { primary, enter: enterLabel, later, more, ...own };
}

// ------------------------------------------------------------------ the face: one verb, one corner
// Whatever a kind (or the live layer) drew as a control on a face is taken out: checkboxes go (the
// verb is `done`), links and buttons become their plain text. One button per face, literally.
export function stripControls(node) {
  for (const n of node.querySelectorAll('input, select')) n.remove();
  for (const n of node.querySelectorAll('button:not(.fx-verb), a, [role="button"]')) {
    if (n === node) continue;
    const t = (n.textContent || '').trim();
    if (!t || n.classList.contains('og-live-open')) { n.remove(); continue; }
    const span = document.createElement('span');
    span.className = n.className;
    span.textContent = t;
    n.replaceWith(span);
  }
}

export function faceVerb(block, node) {
  if (!node || !block) return;
  for (const old of node.querySelectorAll(':scope > .fx-verb')) old.remove();
  stripControls(node);
  movedMark(block, node);
  if (block.kind === 'widget' || block.status === 'done') return;   // glances are furniture; done is done
  const g = grammar(block);
  const label = g.enter || (g.primary && g.primary.label);
  if (!label) return;
  const btn = el('button', 'fx-verb' + (g.enter ? ' fx-verb-in' : ''), label);
  btn.type = 'button';
  btn.dataset.nodrag = '1';
  btn.onclick = async (e) => {
    e.stopPropagation();
    if (g.enter) return enter(block, node);
    btn.disabled = true;
    try { await g.primary.run(block); } finally { btn.disabled = false; }
  };
  if (!g.enter && g.primary.preview) previewable(btn, block);
  node.append(btn);
  node.classList.add('fx-face');
}

// ------------------------------------------------------------------ what moved since you left (frontier 3)
// A card the guy, the crew or Hermes changed while he was away (block.changed = {what, by, at}, worked
// out in board/briefing.py) wears a thin dashed outline and one quiet line under its headline until
// he focuses it. Focus posts /api/seen {id}; the board being in front of him a few seconds posts
// {board: true}. His opener counts them, and tapping it walks them oldest first: focus one, step
// back, he offers the next (the same go / not now chip as the guided run).
const SEEN_AFTER_MS = 4000;
const movedBlocks = () => (OneGuy.board.blocks || []).filter((b) => b.changed && b.changed.what)
  .sort((a, b) => (Date.parse(a.changed.at) || 0) - (Date.parse(b.changed.at) || 0));
export function movedMark(block, node) {
  for (const old of node.querySelectorAll('.fx-moved-what')) old.remove();
  const c = live(block).changed;
  node.classList.toggle('fx-moved', !!(c && c.what));
  if (!c || !c.what) return;
  const w = el('div', 'fx-moved-what', c.what);
  if (c.by) w.append(el('span', 'fx-moved-by', ' · ' + c.by));
  const t = node.querySelector('.og-fy-t, .kf-title');
  if (t) t.after(w); else node.append(w);
}
const post = (body) => { try { OneGuy.api.post('/api/seen', body).catch(() => {}); } catch (e) { /* offline: the mark comes back */ } };
function sawItem(id) {
  if (!id) return;
  post({ id });
  const b = OneGuy.board.byId[id];
  if (!b || !b.changed) return;
  delete b.changed;
  for (const n of document.querySelectorAll(`[data-id="${CSS.escape(id)}"].fx-moved`)) movedMark(b, n);
  OneGuy.emit('moved', { n: movedBlocks().length });
}
let movedWalk = false, seenT = 0, lastSeen = null;
function walkMoved() {
  const m = movedBlocks();
  movedWalk = m.length > 0;
  if (m.length) OneGuy.view('briefing', { focus: m[0].id });
  return movedWalk;
}
OneGuy.moved = movedBlocks;
OneGuy.walkMoved = walkMoved;
OneGuy.on('focus', ({ block } = {}) => {
  if (lastSeen && (!block || block.id !== lastSeen)) sawItem(lastSeen);        // what landed while it was open was read
  lastSeen = block ? block.id : null;
  if (block) { sawItem(block.id); return; }
  if (movedWalk) setTimeout(() => {
    if (OneGuy.focused || !movedWalk) return;
    const nxt = movedBlocks()[0];
    if (!nxt) { movedWalk = false; OneGuy.orb.say('that’s everything that moved.', { ms: 3000 }); return; }
    showOffer(nxt.id, `next: ${lower(headlineOf(nxt).title || nxt.title)} — ${nxt.changed.what}`);
  }, 450);
});
OneGuy.on('next', ({ answer } = {}) => { if (answer !== 'go') movedWalk = false; });
OneGuy.on('board', () => {            // only after a load: that build already holds whatever moved before now
  clearTimeout(seenT);
  seenT = setTimeout(() => { if (!document.hidden) post({ board: true }); }, SEEN_AFTER_MS);
});

// ------------------------------------------------------------------ preview before yes
// A proposal that changes the board carries its edits (block.detail.edits). Point at its verb
// (hover; on a phone press and hold) and you see the result before you say yes: the cards it moves
// glide to their new pile, translucent, each with a thin line back to where it was; a card it would
// put away, tick or schedule says so on its face (`→ thu 3pm`); everything else dims a little.
// Let go or move off and nothing changed. Tap = yes: the server carries the edits out and logs each
// one as a judgment, the cards stay, and the 5s undo pill takes all of it back.
const editsOf = (b) => (b && b.kind === 'proposal' && b.detail && Array.isArray(b.detail.edits) && b.detail.edits.length ? b.detail.edits : null);
const nodesOf = (id) => [...document.querySelectorAll(`#og-cards .og-card[data-id="${CSS.escape(id)}"], #og-brief [data-id="${CSS.escape(id)}"]`)];
const pileName = (cid) => (OneGuy.preview && OneGuy.preview.pile(cid)) || String(cid).replace(/^\w+:/, '');
function whenWords(when) {            // 2026-11-12T15:00 → "thu 3pm" · 2026-11-12 → "thu"
  const d = new Date(String(when).length === 10 ? when + 'T12:00' : when);
  if (isNaN(d)) return String(when);
  const far = Math.abs(d - Date.now()) > 6 * 864e5;
  const day = d.toLocaleDateString('en-US', { weekday: 'short' }).toLowerCase() + (far ? ' ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toLowerCase() : '');
  if (String(when).length === 10) return day;
  const h = d.getHours(), m = d.getMinutes();
  return `${day} ${h % 12 || 12}${m ? ':' + String(m).padStart(2, '0') : ''}${h < 12 ? 'am' : 'pm'}`;
}
const editWords = (e) => (e.op === 'move' ? pileName(e.to_pile) : e.op === 'park' ? 'put away' : e.op === 'done' ? 'done' : whenWords(e.when));
function previewNote(edits) {         // the whole change in one plain line, beside the verb
  const cards = (n) => `${n} card${n === 1 ? '' : 's'}`;
  const piles = new Set(edits.filter((e) => e.op === 'move').map((e) => e.to_pile));
  if (edits.every((e) => e.op === 'move') && piles.size === 1) return `${cards(edits.length)} → ${pileName(edits[0].to_pile)}`;
  if (edits.length === 1) return `${title(edits[0].id) || '1 card'} → ${editWords(edits[0])}`;
  const n = (op) => edits.filter((e) => e.op === op).length;
  return [['move', 'move'], ['park', 'put away'], ['schedule', 'schedule'], ['done', 'tick']].filter(([op]) => n(op)).map(([op, w]) => `${w} ${n(op)}`).join(' · ');
}
let pvNow = null;                     // {id, undo[]} — the one preview on screen
function previewStart(block, btn) {
  const edits = editsOf(live(block));
  if (!edits || (pvNow && pvNow.id === block.id)) return;
  previewEnd();
  const undo = [];
  const moves = {};
  for (const e of edits) if (e.op === 'move') moves[e.id] = e.to_pile;
  const br = btn.getBoundingClientRect();
  const shown = (OneGuy.preview && OneGuy.preview.start(moves, btn.closest('.og-card') ? block.id : null,
    { x: br.left + br.width / 2, y: br.top + br.height / 2 })) || {};   // his verb holds still under your finger
  const seen = shown.seen || new Set();
  document.body.classList.add('fx-previewing');
  for (const n of nodesOf(block.id)) { n.classList.add('fx-pv-src'); undo.push(() => n.classList.remove('fx-pv-src')); }
  for (const e of edits) {
    for (const n of nodesOf(e.id)) {
      const line = el('span', 'fx-ghost-line', '→ ' + editWords(e));
      n.classList.add('fx-ghost', 'fx-ghost-' + e.op);
      n.append(line);
      undo.push(() => { n.classList.remove('fx-ghost', 'fx-ghost-' + e.op); line.remove(); });
    }
  }
  const note = el('div', 'fx-pv-note');
  note.append(el('div', 'fx-pv-h', previewNote(edits)));
  note.setAttribute('role', 'status');
  // what he can't see land (off screen, in another lens, on a phone's long column) is named in words
  const onScreen = (e) => seen.has(e.id) || (e.op !== 'move' && nodesOf(e.id).some((n) => { const r = n.getBoundingClientRect(); return r.width && r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth; }));
  if (edits.some((e) => !onScreen(e))) {
    const same = previewNote(edits).includes('→');
    for (const e of edits.slice(0, 5)) note.append(el('div', 'fx-pv-i', (title(e.id) || 'a card that has gone') + (same ? '' : ' → ' + editWords(e))));
    if (edits.length > 5) note.append(el('div', 'fx-pv-i', `and ${edits.length - 5} more`));
  }
  document.body.append(note);
  const r = btn.getBoundingClientRect();
  note.style.left = Math.max(8, Math.min(window.innerWidth - note.offsetWidth - 8, r.right - note.offsetWidth)) + 'px';
  note.style.top = Math.max(8, r.top - note.offsetHeight - 8) + 'px';
  undo.push(() => note.remove());
  pvNow = { id: block.id, undo };
}
function previewEnd(keep, moved) {
  const p = pvNow;
  pvNow = null;
  if (p) for (const fn of p.undo) fn();
  document.body.classList.remove('fx-previewing');
  if (OneGuy.preview && (p || keep)) OneGuy.preview.end(!!keep, moved);
}
// the gesture on a verb: hover (mouse) or press-and-hold (touch) shows it; leaving or letting go ends
// it; a held press is not a tap (the click that follows it is swallowed)
function previewable(btn, block) {
  let hold = 0, held = false, hot = null;
  btn.classList.add('fx-verb-pv');
  // the preview re-lays the board, and the verb may ride along for a moment: "still pointing at it"
  // is judged against where the verb WAS when the pointer arrived, not where it is mid-glide
  const inHot = (e) => hot && e.clientX >= hot.left - 6 && e.clientX <= hot.right + 6 && e.clientY >= hot.top - 6 && e.clientY <= hot.bottom + 6;
  const away = (e) => { if (!inHot(e) && !btn.contains(e.target)) stop(); };
  const stop = () => { hot = null; document.removeEventListener('pointermove', away, true); previewEnd(); };
  btn.addEventListener('pointerenter', (e) => {
    if (e.pointerType !== 'mouse' || hot) return;
    hot = btn.getBoundingClientRect();
    document.addEventListener('pointermove', away, true);
    previewStart(block, btn);
  });
  btn.addEventListener('pointerleave', (e) => { clearTimeout(hold); if (e.pointerType === 'mouse' && inHot(e)) return; stop(); });
  btn.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse') return;
    held = false;
    clearTimeout(hold);
    hold = setTimeout(() => { held = true; previewStart(block, btn); }, 320);
  });
  for (const ev of ['pointerup', 'pointercancel']) btn.addEventListener(ev, (e) => { clearTimeout(hold); if (e.pointerType !== 'mouse') stop(); });
  btn.addEventListener('contextmenu', (e) => e.preventDefault());
  btn.addEventListener('click', (e) => { if (held) { held = false; e.preventDefault(); e.stopImmediatePropagation(); } }, true);
  btn.addEventListener('focus', () => { if (btn.matches(':focus-visible')) previewStart(block, btn); });
  btn.addEventListener('blur', () => stop());
}
// yes landed: the placements are the user's now (the page's own store, as a drag or a flick would write
// them); the ledger lines were written by the server. Undo puts back exactly what was there.
OneGuy.on('action-done', ({ block, result } = {}) => {
  if (!result || !result.placements || !result.undo) return;
  const prior = {};
  for (const [id, p] of Object.entries(result.placements)) {
    const cur = store.get(id);
    prior[id] = cur ? { cluster: cur.cluster, parked: cur.parked } : null;
    store.set(id, p);
  }
  previewEnd(true, Object.keys(result.placements));
  try { OneGuy.orb.say(`${result.summary}. undo is right there.`, { ms: 2600 }); } catch (e) { /* ignore */ }
  const back = () => {
    for (const [id, was] of Object.entries(prior)) { if (was) store.set(id, { cluster: was.cluster, parked: was.parked }); else store.drop(id); }
    walked.delete(block.id);
    OneGuy.emit('undone', { block, result });        // the idea is an open question again: its card comes back
    if (OneGuy.preview) OneGuy.preview.relayout();
  };
  if (OneGuy.talk && OneGuy.talk.showUndo) OneGuy.talk.showUndo(result.undo, result.summary, { onUndone: back });
});
for (const ev of ['lens', 'view', 'focus']) OneGuy.on(ev, () => previewEnd());

// ------------------------------------------------------------------ focus: the item, behind this, three slots
const PLAIN_STATUS = { 'needs-you': 'waiting on you', failed: 'broke', proposed: 'an idea from me', done: 'done', parked: 'put away' };   // never a bare 'moving' / 'idle'

// A kind's bloom body is DATA to us: what it wraps in `.kb-behind` becomes "behind this".
function splitBody(block) {
  const lead = el('div', 'fx-lead'), behind = el('div', 'fx-behind-in');
  const k = OneGuy.kinds[block.kind] || {};
  try {
    if (k.bloom) k.bloom(block, lead);
  } catch (err) { console.warn('[focus] kind body failed', block.id, err); lead.replaceChildren(); }
  for (const d of lead.querySelectorAll('.kb-behind')) { behind.append(...d.childNodes); d.remove(); }
  for (const n of lead.querySelectorAll('.kb-sec, .kb')) if (!n.textContent.trim() && !n.querySelector('img, video, input, svg')) n.remove();
  return { lead, behind };
}

export function compose(block, panel, { onClose } = {}) {
  const b = block, hl = headlineOf(b), g = grammar(b);
  panel.classList.add('fx-focus');
  // head: where it lives + its state, the headline, one plain sentence
  const hd = el('header', 'bloom-hd fx-hd');
  const kindLine = el('div', 'og-bloom-k');
  const proj = b.project && (OneGuy.board.projects || []).find((p) => p.id === b.project);
  const ico = el('span', 'og-bloom-ico', '●');
  if (proj) { ico.style.color = proj.color; kindLine.append(el('span', 'og-bloom-proj', proj.title.replace(/\s*\([^)]*\)\s*$/, ''))); }
  // the status chip is the item's world line when it has one ("conflict: pickup overlaps the standup
  // · Sam"); else a state that means something; else nothing — never a bare "moving" (card grammar §6)
  const lv = b.live && typeof b.live === 'object' && b.live.line ? b.live : null;
  const ps = lv ? String(lv.line) + (lv.who && !String(lv.line).toLowerCase().includes(String(lv.who).toLowerCase()) ? ' · ' + lv.who : '') : (PLAIN_STATUS[b.status] ?? '');
  if (ps) kindLine.append(el('span', 'og-st og-st-' + b.status + (lv ? ' fx-world fx-world-' + String(lv.kind || '').replace(/[^a-z-]/gi, '') : ''), ps));
  const tt = el('div', 'og-bloom-tt');
  tt.append(kindLine, el('h2', 'og-bloom-t', hl.title || b.title));
  if (hl.why && !hl.said) tt.append(el('p', 'og-bloom-why', hl.why));   // a chat's last line is in "where it left off" just below
  if (hl.ask && hl.ask.question && hl.ask.question !== hl.title) tt.append(el('p', 'fx-q', hl.ask.question));
  // phone: there is no chat column, so the card itself says where this conversation lives, links to
  // its Hermes session, and shows the "→ went to X" note (the desktop column's header does this there)
  const whereRow = el('div', 'fx-where');
  const w = where(b.id);
  whereRow.append(el('span', 'fx-where-t', [w.view, w.place].filter(Boolean).join(' › ')));
  const hermes = el('a', 'fx-where-h');
  hermes.hidden = true;
  const noteEl = el('div', 'fx-where-note');
  noteEl.hidden = true;
  const fillHermes = () => {
    let sid = null;
    try { const c = OneGuy.surface && OneGuy.surface.controller && OneGuy.surface.controller(b); sid = c && c.opened && c.opened.session_id; } catch (e) { /* ignore */ }
    if (!sid || !hermes.isConnected) return;
    hermes.hidden = false;
    hermes.href = 'hermes://open/' + encodeURIComponent(sid);
    hermes.title = 'open this conversation in Hermes · session ' + sid;
    hermes.replaceChildren('hermes ↗ ', el('span', 'fx-where-sid', String(sid).slice(-6)));
    hermes.onclick = (e) => { e.preventDefault(); e.stopPropagation(); OneGuy.openSession(sid); };
  };
  whereRow.append(hermes);
  for (const ms of [600, 2500, 7000]) setTimeout(fillHermes, ms);
  panel._fx = { fillHermes, noteEl };
  tt.append(whereRow, noteEl);
  const x = el('button', 'og-bloom-x', '×');
  x.type = 'button';
  x.setAttribute('aria-label', 'back');
  x.onclick = (e) => { e.stopPropagation(); onClose && onClose(); };
  hd.append(ico, tt, x);

  const body = el('div', 'bloom-body fx-body');
  body.dataset.slot = 'body';
  const { lead, behind } = splitBody(b);
  if (lead.childNodes.length) body.append(lead);
  if (behind.childNodes.length) {
    const d = el('details', 'fx-behind');
    d.append(el('summary', 'fx-behind-s', 'behind this'), behind);
    body.append(d);
  }
  // the strip is the one place connections live; a kind's own piece list (projects) hops too.
  const strip = connections(b);
  if (strip) body.append(strip);
  lead.addEventListener('click', (e) => {
    const t = e.target.closest && e.target.closest('[data-focus]');
    if (t && OneGuy.board.byId[t.dataset.focus]) { e.stopPropagation(); e.preventDefault(); hop(t.dataset.focus); }
  }, true);
  const thread = el('div', 'bloom-thread');
  thread.dataset.slot = 'thread';

  // the three slots: same place, same keys, every kind
  const foot = el('footer', 'bloom-actions fx-slots');
  foot.dataset.slot = 'actions';
  const mk = (cls, slot, key) => {
    const btn = el('button', 'fx-slot ' + cls);
    btn.type = 'button';
    btn.append(el('span', 'fx-slot-l', slot.label), el('kbd', 'fx-key', key));
    btn.onclick = async (e) => {
      e.stopPropagation();
      if (btn.disabled) return;
      btn.disabled = true;
      try { await slot.run(b); } finally { btn.disabled = false; }
    };
    return btn;
  };
  const primary = mk('fx-primary', g.primary, '⏎');
  primary.dataset.action = 'primary';
  if (g.primary.preview) previewable(primary, b);
  const later = mk('fx-later', g.later, 'L');
  later.dataset.action = 'later';
  foot.append(primary, later);
  const wrap = el('div', 'og-more fx-more');
  const mb = el('button', 'fx-slot fx-dots og-more-b', '⋯');
  mb.type = 'button';
  mb.setAttribute('aria-label', 'more');
  mb.setAttribute('aria-haspopup', 'menu');
  mb.setAttribute('aria-expanded', 'false');
  const menu = el('div', 'og-more-m');
  menu.setAttribute('role', 'menu');
  menu.hidden = true;
  for (const m of g.more) {
    const it = el('button', 'og-act og-more-i' + (m.danger ? ' fx-danger' : ''), m.label);
    it.type = 'button';
    it.setAttribute('role', 'menuitem');
    it.onclick = (e) => { e.stopPropagation(); menu.hidden = true; m.run(b); };
    menu.append(it);
  }
  if (!g.more.length) menu.append(el('span', 'og-act-none', 'nothing else to do with this one.'));
  mb.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; mb.setAttribute('aria-expanded', String(!menu.hidden)); };
  wrap.append(mb, menu);
  foot.append(wrap);

  panel.append(hd, body, thread, foot);
  return { hd, body, thread, foot, slots: { primary, later, more: mb } };
}

// ------------------------------------------------------------------ connections: how pieces stitch together
// Chips for what this item is tied to: the session it came from, its project, its task or artifact,
// anything the board links it to (the same links the tendrils draw). Tap one = focus it; the trail grows.
const KIND_WORD = { session: 'session', task: 'task', project: 'project', artifact: 'made', proposal: 'idea', crew: 'crew ask', job: 'routine', goal: 'goal', media: 'clip', talk: 'said', widget: 'glance', plugin: 'tool' };
export function connected(block) {
  const own = (() => { try { const k = OneGuy.kinds[block.kind]; const g = k && k.grammar && k.grammar(block); return (g && g.connections) || []; } catch (e) { return []; } })();
  const d = block.detail || {};
  const ids = [...own, ...(block.links || [])];
  const sid = (d.origin && d.origin.session_id) || d.session_id || null;      // where it came from
  if (sid && block.kind !== 'session') ids.unshift('session:' + sid);
  if (d.by && /^\d{8}_\d{6}_/.test(String(d.by))) ids.push('session:' + d.by);
  const seen = new Set([block.id]);
  return ids.filter((id) => id && !seen.has(id) && seen.add(id) && OneGuy.board.byId[id]).slice(0, 8);
}
function connections(block) {
  const ids = connected(block);
  if (!ids.length) return null;
  const row = el('div', 'fx-links');
  row.setAttribute('aria-label', 'connected to');
  row.append(el('span', 'fx-links-l', 'connected'));
  for (const id of ids) {
    const o = OneGuy.board.byId[id];
    const chip = el('button', 'fx-link fx-link-' + o.kind + (o.status === 'needs-you' || o.status === 'failed' ? ' hot' : ''));
    chip.type = 'button';
    chip.dataset.focus = id;
    chip.dataset.id = id;
    chip.title = 'go to this (Esc comes back)';
    chip.append(el('span', 'fx-link-k', KIND_WORD[o.kind] || o.kind), el('span', 'fx-link-t', headlineOf(o).title || o.title));
    chip.onclick = (e) => { e.stopPropagation(); hop(id); };
    row.append(chip);
  }
  return row;
}

// ⏎ resolves, L puts it off — only when the user isn't typing or pressing some other control
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
  const panel = document.querySelector('.og-bloom.fx-focus');
  if (!panel || document.querySelector('.og-ask-scrim')) return;
  const a = document.activeElement;
  if (a && a !== document.body && a !== panel && /^(INPUT|TEXTAREA|BUTTON|A|SELECT|SUMMARY)$/.test(a.tagName)) return;
  if (a && a.isContentEditable) return;
  const key = e.key === 'Enter' ? 'primary' : e.key === 'l' || e.key === 'L' ? 'later' : null;
  if (!key) return;
  const btn = panel.querySelector(`.fx-slot[data-action="${key}"]`);
  if (btn && !btn.disabled) { e.preventDefault(); btn.click(); }
});

OneGuy.grammar = grammar;

// ------------------------------------------------------------------ getting back: the trail, Esc, recent
// The trail reads   ↺  today › loose ends › Review the sync PR   across the top while something is
// focused. Every crumb is a way back. Esc (OneGuy.back) walks ONE step: to the item you came from,
// else out of focus into the lens you're in. ↺ holds the last five things you focused.
const stack = [];                  // focused ids, oldest first; grows when you hop item → item
let hopping = false;               // true while WE move the focus (a hop or a step back)
const trail = el('nav', 'fx-trail');
trail.setAttribute('aria-label', 'where you are');
trail.hidden = true;
document.body.append(trail);
const title = (id) => { const b = OneGuy.board.byId[id]; return b ? (headlineOf(b).title || b.title) : null; };
// RECENT never drops work. Two sources, merged newest-first: what this browser opened (view-state),
// and the server's list (GET /api/recent: every conversation in the thread map + every decision in
// the ledger). An item that was put away, aged out or isn't a card stays in the list: put-away
// comes back when tapped, off-board opens its Hermes session.
const localRecent = () => ((store.data.view && store.data.view.recent) || [])
  .map((r) => (typeof r === 'string' ? { id: r, at: 0 } : r)).filter((r) => r && r.id);
let served = [];
let recentKeys = false;        // opened with R: keep keyboard focus inside it across its repaints
let recentOpen = false;        // the drawer stays open across a repaint (its own data arriving repaints it)
let servedAt = 0;
async function pullRecent(force) {
  if (!force && Date.now() - servedAt < 20000) return;
  servedAt = Date.now();
  try { const r = await OneGuy.api.get('/api/recent?limit=24'); served = (r && r.recent) || []; paintTrail(); } catch (e) { /* the local list still works */ }
}
function recent() {
  const m = new Map();
  for (const r of served) m.set(r.key, { ...r, at: Date.parse(r.at) || 0 });
  for (const r of localRecent()) {
    const cur = m.get(r.id), b = OneGuy.board.byId[r.id];
    if (!cur && !b) continue;                                   // an id this browser knew that no longer exists anywhere
    if (!cur) m.set(r.id, { key: r.id, title: title(r.id), at: r.at, last: 'opened', on_board: true, session_id: null });
    else if (r.at > cur.at) { cur.at = r.at; cur.last = 'opened'; }
  }
  return [...m.values()].map((r) => ({ ...r, on_board: !!OneGuy.board.byId[r.key] || false,
    title: (OneGuy.board.byId[r.key] && title(r.key)) || r.title || r.key,
    parked: !!((store.get(r.key) || {}).parked) })).sort((a, b) => b.at - a.at).slice(0, 12);
}
const WHAT = { talked: 'talked', opened: 'opened', park: 'put away', unpark: 'taken back', done: 'done', keep: 'kept', 'not-now': 'not now',
  delete: 'deleted', run: 'ran', verdict: 'answered', decide: 'decided', steer: 'switched to', next: 'offered', cluster: 'moved', schedule: 'scheduled' };
const agoWords = (t) => { if (!t) return ''; const m = Math.max(0, Math.round((Date.now() - t) / 60000)); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; };
const VIEW_NAME = { today: 'today', board: 'board', time: 'time', inbox: 'inbox' };

function hop(id) {                 // focus another item FROM this one: the trail grows
  if (!OneGuy.board.byId[id]) return;
  const at = stack.indexOf(id);
  if (at >= 0) stack.length = at; // going back to a crumb drops what came after it
  hopping = true;
  try { OneGuy.focus(id); } finally { hopping = false; }
}
function back() {                  // Esc: one step, never to the top, never out of the lens
  if (stack.length > 1) {
    stack.pop();
    const prev = stack.pop();
    hopping = true;
    try { OneGuy.focus(prev); } finally { hopping = false; }
    return true;
  }
  if (OneGuy.focused) { OneGuy.unfocus(); return true; }
  return false;
}
function crumb(text, cls, fn) {
  const c = el(fn ? 'button' : 'span', 'fx-crumb ' + (cls || ''), text);
  if (fn) { c.type = 'button'; c.onclick = (e) => { e.stopPropagation(); fn(); }; }
  return c;
}
function paintTrail() {
  const rec = recent();
  trail.hidden = !stack.length && !rec.length;
  trail.classList.toggle('fx-trail-idle', !stack.length);
  document.body.classList.toggle('fx-recent-idle', !stack.length && rec.length > 0);   // phone: ↺ takes a slot in the tabs row
  trail.replaceChildren();
  // ↺ recent
  if (rec.length) {
    const wrap = el('span', 'fx-recent');
    const btn = el('button', 'fx-crumb fx-recent-b', '↺');
    btn.type = 'button';
    btn.title = 'recent: everything you touched lately, wherever it is now';
    btn.setAttribute('aria-haspopup', 'menu');
    const menu = el('span', 'fx-recent-m');
    menu.setAttribute('role', 'menu');
    menu.hidden = !recentOpen;
    menu.append(el('span', 'fx-recent-h', 'recent'));
    for (const r of rec) {
      const where = r.parked ? 'put away' : !r.on_board ? (r.session_id ? 'not on the board · opens in hermes ↗' : 'not on the board') : '';
      const it = el('button', 'fx-recent-i' + (r.on_board ? '' : ' fx-recent-off'));
      it.type = 'button';
      it.setAttribute('role', 'menuitem');
      it.disabled = !r.on_board && !r.session_id;
      it.append(el('span', 'fx-recent-t', r.title),
        el('span', 'fx-recent-s', [...new Set([WHAT[r.last] || r.last || '', agoWords(r.at), where].filter(Boolean))].join(' · ')));
      it.onclick = (e) => {
        e.stopPropagation();
        menu.hidden = true;
        recentOpen = false;
        if (!r.on_board) { if (r.session_id) OneGuy.openSession(r.session_id); return; }
        if (r.parked && OneGuy.summon) OneGuy.summon(r.key, null, { quiet: true });   // back from put-away
        stack.length = 0;
        OneGuy.focus(r.key);
      };
      menu.append(it);
    }
    btn.onclick = (e) => { e.stopPropagation(); recentOpen = menu.hidden; menu.hidden = !recentOpen; if (recentOpen) pullRecent(true); };
    // opened from the keyboard (R): the first row takes focus, ↑/↓ walk the rows, Esc closes just the drawer
    menu.addEventListener('keydown', (e) => {
      const rows = [...menu.querySelectorAll('.fx-recent-i:not(:disabled)')], i = rows.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); const n = rows[(i + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length]; if (n) n.focus(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); recentOpen = recentKeys = false; menu.hidden = true; btn.blur(); if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); }
      else if (e.key === 'Enter') e.stopPropagation();          // the row's own click, not the card's amber verb
    }, true);
    wrap.append(btn, menu);
    trail.append(wrap);
    if (recentOpen && recentKeys) requestAnimationFrame(() => { const f = menu.querySelector('.fx-recent-i:not(:disabled)'); if (f && !menu.contains(document.activeElement)) f.focus(); });
  }
  if (!stack.length) return;
  const lens = document.body.dataset.ogLens || 'today';
  if (window.innerWidth <= 700) {      // phone: one compact chip that says where "back" goes
    const prev = stack.length > 1 ? title(stack[stack.length - 2]) : (VIEW_NAME[lens] || lens);
    trail.append(crumb('‹ ' + prev, 'fx-crumb-back', () => back()));
    return;
  }
  trail.append(crumb(VIEW_NAME[lens] || lens, 'fx-crumb-view', () => { stack.length = 0; OneGuy.unfocus(); }));
  const place = OneGuy.placeOf ? OneGuy.placeOf(stack[0]) : null;
  if (place) trail.append(el('i', 'fx-sep', '›'), crumb(place, 'fx-crumb-place', () => { stack.length = 0; OneGuy.unfocus(); }));
  stack.forEach((id, i) => {
    const last = i === stack.length - 1;
    trail.append(el('i', 'fx-sep', '›'), crumb(title(id) || 'gone', last ? 'fx-crumb-here' : '', last ? null : () => hop(id)));
  });
}
OneGuy.on('focus', ({ block } = {}) => {
  if (block) {
    if (!hopping && !OneGuy._refocus) stack.length = 0;
    if (stack[stack.length - 1] !== block.id) stack.push(block.id);
    store.setView({ recent: [{ id: block.id, at: Date.now() }, ...localRecent().filter((r) => r.id !== block.id)].slice(0, 8) });
  } else if (!hopping && !OneGuy._refocus) {
    // openBloom closes the old panel before opening the next: only a real unfocus empties the trail
    setTimeout(() => { if (!OneGuy.focused) { stack.length = 0; paintTrail(); } }, 0);
    return;
  }
  paintTrail();
});
OneGuy.on('lens', paintTrail);
OneGuy.on('view', paintTrail);
OneGuy.on('board', paintTrail);
OneGuy.on('ready', () => { paintTrail(); pullRecent(true); });
OneGuy.on('board', () => pullRecent(false));
for (const ev of ['park', 'action-done', 'column']) OneGuy.on(ev, () => setTimeout(() => pullRecent(true), 1500));
document.addEventListener('pointerdown', (e) => { if (!(e.target.closest && e.target.closest('.fx-recent'))) { recentOpen = recentKeys = false; for (const m of trail.querySelectorAll('.fx-recent-m')) m.hidden = true; } }, true);
// An item can change its id while you work on it: opening a task's thread gives it its permanent
// key (task:<hash> → task:^xxxx). The thread map knows both. repoint(old) finds where the item
// lives now and carries the trail, the recent list and (if it was open) the focus over to it.
async function repoint(old) {
  let key = null;
  try { const r = await OneGuy.api.get('/api/threads/resolve?key=' + encodeURIComponent(old)); key = r && r.key; } catch (e) { return null; }
  if (!key || key === old || !OneGuy.board.byId[key]) return null;
  if (OneGuy.surface && OneGuy.surface.rekey) OneGuy.surface.rekey(old, OneGuy.board.byId[key]);   // same conversation, new id
  for (let i = 0; i < stack.length; i++) if (stack[i] === old) stack[i] = key;
  const seen = new Set();
  store.setView({ recent: localRecent().map((r) => (r.id === old ? { ...r, id: key } : r)).filter((r) => !seen.has(r.id) && seen.add(r.id)) });
  if (OneGuy.focused && OneGuy.focused.id === old) {
    if (stack[stack.length - 1] === key) stack.pop();        // focus pushes it back
    hopping = true;
    try { OneGuy.focus(key); } finally { hopping = false; }
  }
  paintTrail();
  return key;
}
// where an item lives, for anything that has to say so (the trail here, the chat column's header):
// [view, pile, title]. The pile is the lens's own place for it when a lens is showing, else its
// project / home pile on the board.
const HOME_PILE = { job: 'routines', widget: 'housekeeping', goal: 'goals' };
function where(id) {
  const b = OneGuy.board.byId[id];
  const lens = document.body.dataset.ogLens || 'today';
  if (!b) return { view: VIEW_NAME[lens] || lens, place: null, title: null, onBoard: false };
  const proj = b.project && (OneGuy.board.projects || []).find((p) => p.id === b.project);
  const pile = (OneGuy.placeOf && OneGuy.placeOf(id)) || (b.kind === 'project' ? null
    : proj ? String(proj.title || '').replace(/\s*\([^)]*\)\s*$/, '') : (HOME_PILE[b.kind] || 'loose ends'));
  return { view: VIEW_NAME[lens] || lens, place: pile, title: title(id), onBoard: true };
}
OneGuy.where = where;
// the column's "where your line went" note, mirrored into the open card (it is the only place a phone has)
OneGuy.on('route-note', ({ text } = {}) => {
  const p = document.querySelector('.og-bloom.fx-focus');
  if (!p || !p._fx || !text) return;
  p._fx.noteEl.textContent = text;
  p._fx.noteEl.hidden = false;
  setTimeout(() => { if (p._fx.noteEl.textContent === text) p._fx.noteEl.hidden = true; }, 15000);
});
OneGuy.on('say', () => { const p = document.querySelector('.og-bloom.fx-focus'); if (p && p._fx) setTimeout(p._fx.fillHermes, 400); });
OneGuy.repoint = repoint;
OneGuy.back = back;
OneGuy.hop = hop;
OneGuy.trail = () => stack.slice();      // the focus trail, oldest first (core.js viewContext sends it with a talk turn)

// ------------------------------------------------------------------ the guided walk: he offers the next one
// The rail is an ordered run. When you resolve or put off the thing you were focused on, he offers
// the next in his order — "next: the staging deploy — it's waited 3h" — with go (⏎) / not now.
// "go" focuses it; say no and he drops it. He also offers to start the run from the opener, once.
// And there is at most ONE amber "look here" on screen: the first card of the run that's in view.
let offer = null;                  // {el, id, t}
let last = { id: null, at: 0 };    // what was in focus most recently
const walked = new Set();          // dealt with during this visit
const theRun = () => OneGuy.briefing(OneGuy.board).forYou.filter((b) => !walked.has(b.id));
const lower = (t) => { const s = String(t || ''); return /^[A-Z][a-z]/.test(s) ? s.charAt(0).toLowerCase() + s.slice(1) : s; };

function dropOffer() {
  const o = offer;
  offer = null;
  if (!o) return;
  clearTimeout(o.t);
  o.el.classList.remove('on');
  setTimeout(() => o.el.remove(), 320);
}
function showOffer(id, text) {
  dropOffer();
  const wrap = el('div', 'fx-next');
  const chip = el('div', 'fx-next-chip');
  chip.setAttribute('role', 'group');
  const go = el('button', 'fx-next-go');
  go.type = 'button';
  go.append(el('span', '', 'go'), el('kbd', 'fx-key', '⏎'));
  go.onclick = (e) => { e.stopPropagation(); dropOffer(); OneGuy.emit('next', { block: OneGuy.board.byId[id], answer: 'go' }); OneGuy.focus(id); };
  const no = el('button', 'fx-next-no', 'not now');
  no.type = 'button';
  no.onclick = (e) => { e.stopPropagation(); dropOffer(); OneGuy.emit('next', { block: OneGuy.board.byId[id], answer: 'not now' }); };
  chip.append(el('i', 'fx-next-orb'), el('span', 'fx-next-t', text), go, no);
  wrap.append(chip);
  const head = document.body.dataset.view === 'briefing' && document.querySelector('#og-brief .og-brief-head');
  if (head) { wrap.classList.add('fx-next-inline'); head.append(wrap); }
  else { document.body.append(wrap); try { OneGuy.orb.say(''); } catch (e) { /* ignore */ } }
  offer = { el: wrap, id, t: setTimeout(dropOffer, 25000) };
  requestAnimationFrame(() => wrap.classList.add('on'));
  if (OneGuy.sounds) OneGuy.sounds.play('needs-you');
  // the card he means is on screen when he says it (the rail may have slid while the last one left)
  const card = document.querySelector(`#og-brief .og-fy[data-id="${CSS.escape(id)}"]`);
  if (card && card.offsetParent !== null) { try { card.scrollIntoView({ inline: 'start', block: 'nearest', behavior: 'smooth' }); } catch (e) { /* ignore */ } }
  setTimeout(markLook, 500);
}
function offerNext(after) {
  if (OneGuy.focused) return;                       // he moved on by himself
  const all = OneGuy.briefing(OneGuy.board).forYou;
  const at = all.findIndex((b) => b.id === after);
  const rest = theRun().filter((b) => b.id !== after);
  const nxt = (at >= 0 && all.slice(at + 1).find((b) => rest.includes(b))) || rest[0];
  if (!nxt) { OneGuy.orb.say('that was the last one. nothing else needs you.', { ms: 3200 }); return; }
  const h = headlineOf(nxt);
  const why = nxt.fy_why || h.why || '';
  showOffer(nxt.id, `next: ${lower(h.title || nxt.title)}${why ? ' — ' + lower(why) : ''}`);
}
function resolved(block, wait = 1100) {
  if (!block) return;
  walked.add(block.id);
  if (movedWalk) return;                 // walking what moved: that walk offers the next one
  if (last.id !== block.id || Date.now() - last.at > 4000) return;   // only when it was THE thing in focus
  setTimeout(() => offerNext(block.id), wait);                         // after it has flown off
}
OneGuy.on('focus', ({ block } = {}) => {
  if (block) { last = { id: block.id, at: Date.now() }; dropOffer(); } else if (last.id) last.at = Date.now();
});
OneGuy.on('action-done', ({ block, action, result } = {}) => {
  if (block && action && ((result && result.decided) || ['done', 'keep', 'approve', 'dismiss', 'reject', 'delete'].includes(action.id)) && !(action.id === 'done' && block.status === 'done')) {
    if (OneGuy.focused && OneGuy.focused.id === block.id) last = { id: block.id, at: Date.now() };
    resolved(block);
  }
});
OneGuy.on('park', ({ block } = {}) => resolved(block, 250));   // 'park' fires when the card has already landed in put-away
OneGuy.on('lens', dropOffer);
OneGuy.on('view', dropOffer);
OneGuy.on('orb-say', ({ text } = {}) => { if (text && offer && !offer.el.classList.contains('fx-next-inline')) dropOffer(); });   // one thing in the bar-zone at a time
// from the opener, once per visit: "seven things today. start?"
OneGuy.on('ready', () => setTimeout(() => {
  if (OneGuy.focused || offer || document.body.dataset.view !== 'briefing') return;
  try { if (sessionStorage.getItem('fx.start')) return; sessionStorage.setItem('fx.start', '1'); } catch (e) { /* private mode */ }
  const r = theRun();
  if (r.length < 2) return;
  showOffer(r[0].id, `start with “${String(headlineOf(r[0]).title || r[0].title).replace(/[.?!…]+$/, '')}”?`);   // no second count: his headline already said it
}, 2600));
document.addEventListener('keydown', (e) => {
  if (!offer || e.metaKey || e.ctrlKey || e.altKey || e.repeat || document.querySelector('.og-bloom, .og-ask-scrim')) return;
  const a = document.activeElement;
  if (a && a !== document.body && /^(INPUT|TEXTAREA|BUTTON|A|SELECT)$/.test(a.tagName) && !(a.tagName === 'INPUT' && !a.value)) return;
  if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); const id = offer.id; dropOffer(); OneGuy.emit('next', { block: OneGuy.board.byId[id], answer: 'go' }); OneGuy.focus(id); }
  else if (e.key === 'Escape') { e.stopPropagation(); dropOffer(); }
}, true);

// one amber "look here": the first card of the run that has a face on screen right now
function markLook() {
  for (const n of document.querySelectorAll('.fx-look')) n.classList.remove('fx-look');
  if (OneGuy.focused) return;
  const brief = document.body.dataset.view === 'briefing';
  const sel = brief ? '#og-brief .og-fy' : '#og-cards .og-card';
  // in a lens it's the first of his run in READING order there (top-left first), not in rail order:
  // on a full board the amber card must be where the eye starts
  const inRun = new Set(theRun().map((b) => b.id));
  const ids = brief ? theRun().map((b) => b.id) : cardsInOrder().filter((id) => inRun.has(id));
  for (const id of ids) {
    const n = document.querySelector(`${sel}[data-id="${CSS.escape(id)}"]`);
    if (n && n.offsetParent !== null && !n.classList.contains('og-pre')) { n.classList.add('fx-look'); return; }
  }
}
let lookT = 0;
const look = () => { clearTimeout(lookT); lookT = setTimeout(markLook, 420); };
for (const ev of ['board', 'lens', 'view', 'focus', 'ready', 'park']) OneGuy.on(ev, look);

// ------------------------------------------------------------------ keys: the whole grammar from the keyboard
//   ⏎ primary · L later · Esc back · the user / K next / previous card in the lens you're in · ? shows this
const KEYS = [['⏎', 'do the amber thing (or "go" when he offers the next one)'], ['L', 'later: put it off'], ['Esc', 'back one step'],
  ['J', 'next card'], ['K', 'previous card'], ['1–4', 'today · board · time · inbox'], ['R', 'recent: what you touched lately'], ['/', 'talk to him'], ['?', 'these keys']];
function cardsInOrder() {
  const brief = document.body.dataset.view === 'briefing';
  const nodes = [...document.querySelectorAll(brief ? '#og-brief .og-fy[data-id]' : '#og-cards .og-card[data-id]')]
    .filter((n) => OneGuy.board.byId[n.dataset.id] && !n.classList.contains('og-gone'));
  if (brief) return nodes.map((n) => n.dataset.id);
  const at = (n) => { const it = n.style.transform.match(/translate3d\(([-\d.]+)px,\s*([-\d.]+)px/); return it ? [parseFloat(it[1]), parseFloat(it[2])] : [0, 0]; };
  return nodes.map((n) => ({ id: n.dataset.id, c: n.dataset.cluster || '', p: at(n) }))
    .sort((a, b) => (a.c === b.c ? a.p[1] - b.p[1] || a.p[0] - b.p[0] : a.p[0] - b.p[0] || a.p[1] - b.p[1])).map((x) => x.id);
}
function step(dir) {
  const ids = cardsInOrder();
  if (!ids.length) return;
  const cur = OneGuy.focused ? OneGuy.focused.id : null;
  const i = cur ? ids.indexOf(cur) : -1;
  const next = ids[i < 0 ? (dir > 0 ? 0 : ids.length - 1) : (i + dir + ids.length) % ids.length];
  if (!next || next === cur) return;
  stack.length = 0;                    // a sibling move, not a hop: the trail stays one item deep
  OneGuy.focus(next);
}
let keysEl = null;
function toggleKeys(on) {
  if (keysEl && on !== true) { keysEl.remove(); keysEl = null; return; }
  if (keysEl || on === false) return;
  keysEl = el('div', 'fx-keys');
  keysEl.setAttribute('role', 'dialog');
  keysEl.setAttribute('aria-label', 'keys');
  const box = el('div', 'fx-keys-box');
  box.append(el('div', 'fx-keys-t', 'keys'));
  for (const [k, what] of KEYS) { const row = el('div', 'fx-keys-r'); row.append(el('kbd', 'fx-key', k), el('span', '', what)); box.append(row); }
  keysEl.append(box);
  keysEl.addEventListener('pointerdown', () => toggleKeys(false));
  document.body.append(keysEl);
}
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (keysEl && (e.key === 'Escape' || e.key === '?')) { e.preventDefault(); e.stopPropagation(); toggleKeys(false); return; }
  const a = document.activeElement;
  if (a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return;
  if (document.querySelector('.og-ask-scrim')) return;
  if (e.key === '?') { e.preventDefault(); toggleKeys(); }
  else if (e.key === 'j' || e.key === 'J') { e.preventDefault(); step(1); }
  else if (e.key === 'k' || e.key === 'K') { e.preventDefault(); step(-1); }
  else if (/^[1-4]$/.test(e.key)) { const t = document.querySelectorAll('#og-lens button[data-lens]')[+e.key - 1]; if (t) { e.preventDefault(); t.click(); } }
  else if (e.key === 'r' || e.key === 'R') { const r = [...document.querySelectorAll('.fx-recent-b')].find((n) => n.offsetParent); if (r) { e.preventDefault(); recentKeys = true; r.click(); if (!recentOpen) recentKeys = false; else paintTrail(); } }
}, true);
OneGuy.keys = toggleKeys;
