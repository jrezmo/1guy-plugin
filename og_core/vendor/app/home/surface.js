// WS-F/T work surfaces: each block gets its own thread (a Hermes session with carried context).
// Uses ONLY the contract API (docs/home-contract.md): OneGuy.on/emit, rail.addChip, orb.say,
// orb.mood, caption.j, api.get/post, toast, board, and sets OneGuy.surface.
//
// ONE INPUT, EVER (v1.1): the bloom's thread slot shows the history, the streaming reply and the
// chips, nothing to type into. The global talk bar (talk.js) is the only input; with a block
// focused it talks to that block's surface. OneGuy.surface.say(block, text) is the programmatic
// send (the shell's decide, 'work on this with me').
//
// Streaming: the server consumes the Hermes SSE; we long-poll /api/surface/poll and reveal the
// reply word by word. Interrupting = talking while he streams. The tag is read from the words
// (wait/no/actually/instead → steer; also/btw/oh and → queue) and the user can tap it to flip.
const OG = window.OneGuy;

// main.js only imports the modules: bring our own stylesheet along.
if (!document.querySelector('link[data-ws="F"]')) {
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = new URL('./surface.css', import.meta.url).href;
  l.dataset.ws = 'F';
  document.head.append(l);
}

// ---- the user's tone → tag (mirrors classify in app/routes/surface.py; keep in sync) ----
const STEER_LEAD = /^\W*(wait|no+|nope|nah|actually|instead|stop|hold on|hang on|scratch that|forget that|never ?mind|rather)\b/i;
const STEER_ANY = /\binstead\b|\bscratch that\b|\bforget (that|it)\b/i;
const QUEUE_LEAD = /^\W*(also|and add|and also|btw|by the way|oh,? and|plus|one more( thing)?|add)\b/i;
const QUEUE_ANY = /\b(also add|and also|by the way|btw)\b/i;
export function classify(text, streaming = true) {
  const t = (text || '').trim();
  if (!streaming || !t) return 'say';
  if (STEER_LEAD.test(t)) return 'steer';
  if (QUEUE_LEAD.test(t)) return 'queue';
  if (STEER_ANY.test(t)) return 'steer';
  if (QUEUE_ANY.test(t)) return 'queue';
  return 'steer';
}
const NEXT_TAG = { say: 'steer', steer: 'queue', queue: 'say' };

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const firstSentence = (s) => {
  const t = (s || '').replace(/[*_`#>]/g, '').replace(/\s+/g, ' ').trim();
  const m = t.match(/^(.{4,160}?[.!?])(\s|$)/);
  const out = m ? m[1] : t.slice(0, 120);
  return out.charAt(0).toLowerCase() + out.slice(1);   // his voice starts lowercase; 'PR' and names keep theirs
};
const homeBlock = () => ({ id: 'home', kind: 'home', title: 'home', summary: 'the whole board' });

function boardTop() {
  // The 15 most-attention ITEMS (projects are containers, done is done): on the live board three
  // project blocks used to take a third of the slots. Projects ride along as one line instead.
  const blocks = (OG.board && OG.board.blocks) || [];
  const byAtt = [...blocks].sort((a, b) => (b.attention || 0) - (a.attention || 0));
  const items = byAtt.filter((b) => b.kind !== 'project' && b.status !== 'done').slice(0, 15)
    .map((b) => ({ id: b.id, kind: b.kind, title: b.title, status: b.status }));
  const projects = byAtt.filter((b) => b.kind === 'project' && b.status !== 'done').slice(0, 8);
  if (projects.length) items.push({ id: 'projects', kind: 'projects', status: 'list', title: projects.map((b) => b.title).join(' · ') });
  return items;
}
function linkedOf(block) {
  const byId = (OG.board && OG.board.byId) || {};
  const find = (id) => byId[id] || ((OG.board && OG.board.blocks) || []).find((b) => b.id === id);
  return (block.links || []).map(find).filter(Boolean).slice(0, 10)
    .map((b) => ({ id: b.id, title: b.title, summary: b.summary, status: b.status }));
}
function slimBlock(b) {
  const { id, kind, title, summary, status, project, tags, priority, due, updated, source, links, detail } = b;
  const out = { id, kind, title, summary, status, project, tags, priority, due, updated, source, links, detail };
  // the plain framing the user sees on the card rides along, so the first message shares it (WS-T)
  const h = b.headline;
  if (h && (h.title || h.why)) out.headline = { title: h.title, why: h.why, ask: h.ask ? { question: h.ask.question, options: (h.ask.options || []).map((o) => ({ label: o.label })) } : undefined };
  return out;
}
// what the talk bar's 'about:' chip and the strip call a block: the headline, else the title
export const plainTitle = (b) => (b && ((b.headline && b.headline.title) || b.title || b.id)) || 'this';
function removeChip(id) {
  // Not in the v1 contract (delta proposed in docs/home-ws-F.md); harmless if absent.
  try { OG.rail.removeChip && OG.rail.removeChip(id); } catch (e) { /* ignore */ }
}

// ---------------------------------------------------------------- one surface per block
// An error in his voice, not the server's (the column printed "RuntimeError: API_SERVER_KEY not found…").
// Anything that means "Hermes didn't pick up" gets one plain line; anything else keeps its words, minus the class name.
export function plainError(msg) {
  const m = String(msg || '').trim();
  if (/API_SERVER_KEY|api server|connection refused|ECONNREFUSED|URLError|urlopen|timed out|failed to fetch|NetworkError|\b50[234]\b/i.test(m)) return 'your hermes isn’t answering. is its API server on?';
  return m.replace(/^\w*(Error|Exception):\s*/, '');
}

class Surface {
  constructor(block) {
    this.block = block;
    this.id = block.id;
    this.turns = [];              // {role:'j'|'guy', text, mode?, cut?, live?}
    this.opened = null;
    this.live = null;             // {runId, text(received), shown, done, turn}
    this.followed = new Set();
    this.views = new Set();       // listeners: {onChange}
    this.parked = [];             // didn't-land pieces not yet summoned (one per cut)
    this.queueChips = new Map();  // queue item id → chip id
  }
  get streaming() { return !!(this.live && !(this.live.done && this.live.pending === '')); }
  notify() { this.views.forEach((v) => { try { v.onChange(this); } catch (e) { console.warn(e); } }); }

  async open() {
    if (this.opened) return this.opened;
    const isHome = this.id === 'home';
    const r = await OG.api.post('/api/surface/open', {
      block: slimBlock(this.block), linked: isHome ? [] : linkedOf(this.block),
      board_top: isHome ? boardTop() : undefined,
    });
    this.opened = r;
    this.turns = (r.history || []).map((h) => ({ role: h.role, text: h.text, mode: h.mode, carried: h.carried }));
    (r.queue || []).forEach((q) => this.addQueueChip(q));
    if (r.live && !r.live.done) this.follow(r.live.run_id, { resumed: true });
    this.notify();
    return r;
  }

  // ---- talking ----
  async send(text, mode, opts = {}) {
    text = (text || '').trim();
    if (!text) return;
    await this.open().catch(() => {});
    const streaming = this.streaming;
    if (!mode) mode = classify(text, streaming);
    if (!streaming && mode !== 'say') mode = 'say';
    OG.emit('say', { text, from: 'j', block: this.block, mode });
    const isHome = this.id === 'home';
    const body = { block_id: this.id, text, mode, block: slimBlock(this.block),
      linked: isHome ? [] : linkedOf(this.block), board_top: isHome ? boardTop() : undefined,
      shown: this.live ? this.live.shown : 0,
      view: OG.viewContext ? OG.viewContext() : undefined };   // the thread hears one plain line of it
    if (mode === 'queue') {
      this.turns.push({ role: 'j', text, mode: 'queue', queued: true });
      this.notify();
      const r = await OG.api.post('/api/surface/say', body);
      if (r.queued) {
        this.addQueueChip(r.queued);
        OG.orb.say('queued.');
        return r;
      }
      if (r.run_id) this.follow(r.run_id, {});   // he'd finished in the meantime: it just went
      return r;
    }
    let cut = null;
    if (streaming) cut = this.freeze();           // the caption freezes the moment the user cuts in
    this.turns.push({ role: 'j', text, mode, kickoff: !!opts.kickoff, picked: !!opts.picked });
    this.notify();
    if (mode === 'steer') OG.orb.say('got it.', { ms: 1600 });
    else OG.orb.mood('think');
    const r = await OG.api.post('/api/surface/say', body);
    if (r.interrupted && cut) this.park(r.interrupted, cut);
    if (r.run_id) this.follow(r.run_id, {});
    return r;
  }

  async interrupt() {
    if (!this.streaming) return null;
    const cut = this.freeze();
    const r = await OG.api.post('/api/surface/interrupt', { block_id: this.id, shown: cut.shown });
    this.park(r, cut);
    OG.orb.mood('listen');
    return r;
  }

  freeze() {
    const L = this.live;
    if (!L) return null;
    L.frozen = true;
    L.done = true;
    const cut = { runId: L.runId, shown: L.shown, turn: L.turn };
    L.turn.cut = true;
    L.turn.live = false;          // tick stops on frozen, so finish never runs for a cut turn
    L.turn.unheard = L.text.slice(L.shown);
    L.pending = '';
    this.notify();
    return cut;
  }

  // One cut = at most ONE quiet chip, labelled with the real first sentence that didn't land
  // (the server picks it). When this thread is on screen, its inline 'cut off' line is the single
  // home for the cut text: no chip. The orb never re-offers it (the user cut it off on purpose).
  park(res, cut) {
    const pieces = ((res && res.pieces) || []).filter((p) => p && p.label);
    if (cut && cut.turn) cut.turn.unheard = (res && res.unheard) || cut.turn.unheard || '';
    const p = pieces[0];
    if (p && !mounted.has(this.id)) {
      const chipId = `dl-${cut ? cut.runId : 'x'}`;
      const piece = { ...p, chipId, block: this.id };
      this.parked.push(piece);
      if (cut && cut.turn) cut.turn.parked = true;
      OG.rail.addChip({
        id: chipId, label: p.label, tag: 'didnt-land', block: this.id,
        onSummon: () => this.summon(piece),
      });
    }
    this.notify();
  }

  summon(piece) {
    this.parked = this.parked.filter((p) => p !== piece);
    removeChip(piece.chipId);
    const what = piece.ungenerated ? piece.label : (piece.text || piece.label);
    if (OG.focus && this.id !== 'home') { try { OG.focus(this.id); } catch (e) { /* ignore */ } }
    return this.send('go back to: ' + what, this.streaming ? 'steer' : 'say');
  }

  addQueueChip(q) {
    const chipId = 'q-' + q.id;
    this.queueChips.set(q.id, chipId);
    OG.rail.addChip({
      id: chipId, label: q.text.length > 48 ? q.text.slice(0, 47) + '…' : q.text, tag: 'queue', block: this.id,
      onSummon: async () => {   // send it now instead of waiting
        this.queueChips.delete(q.id);
        removeChip(chipId);
        await OG.api.post('/api/surface/unqueue', { block_id: this.id, id: q.id }).catch(() => {});
        const t = this.turns.find((x) => x.queued && x.text === q.text);
        if (t) t.queued = false;
        this.send(q.text, this.streaming ? 'steer' : 'say');
      },
    });
  }

  // ---- streaming (long-poll + word-by-word reveal) ----
  follow(runId, opts) {
    if (!runId || this.followed.has(runId)) return;
    this.followed.add(runId);
    const turn = { role: 'guy', text: '', live: true };
    this.turns.push(turn);
    const L = { runId, text: '', pending: '', shown: 0, done: false, frozen: false, turn, t0: performance.now(), tFirst: null };
    this.live = L;
    OG.orb.mood('think');
    this.notify();
    this.tick(L);
    this.poll(L, 0);
  }

  async poll(L, since) {
    let p;
    try {
      p = await OG.api.get(`/api/surface/poll?block_id=${encodeURIComponent(this.id)}&run=${encodeURIComponent(L.runId)}&since=${since}&wait=20`);
    } catch (e) {
      if (L.frozen) return;
      L.turn.error = plainError(e.message || e);
      L.done = true;
      this.notify();
      return;
    }
    if (L.frozen) return;
    if (p.delta) {
      if (L.tFirst == null) { L.tFirst = performance.now(); L.turn.firstMs = Math.round(L.tFirst - L.t0); }
      L.text += p.delta;
      L.pending += p.delta;
    }
    if (p.session_id && this.opened && !this.opened.session_id) {
      // the session is created lazily on the first say: surface the Desktop affordance now
      const title = this.opened.title;
      this.opened.session_id = p.session_id;
      this.opened.desktop = { session_id: p.session_id, title, deep_link: 'hermes://open/' + p.session_id,
        hint: `Hermes Desktop: sidebar section 'API' → '${title}'. The deep link opens it when Desktop's active connection is this VPS gateway.` };
    }
    if (p.tools && p.tools.length) L.turn.tools = p.tools;
    if (p.error) L.turn.error = plainError(p.error);
    // a queued item that the server just sent: retire its chip
    if (p.done) {
      L.done = true;
      L.status = p.status;
      if (p.next_run_id && !this.followed.has(p.next_run_id)) {
        const waitReveal = () => (L.pending ? setTimeout(waitReveal, 120) : this.followNext(p.next_run_id));
        waitReveal();
      }
      this.notify();
      return;
    }
    this.poll(L, p.total);
  }

  async followNext(runId) {
    // peek: a queued run carries its queue_id + user_text
    let p = null;
    try { p = await OG.api.get(`/api/surface/poll?block_id=${encodeURIComponent(this.id)}&run=${runId}&since=0&wait=0`); } catch (e) { /* ignore */ }
    if (p && p.queue_id) {
      const chipId = this.queueChips.get(p.queue_id);
      if (chipId) { removeChip(chipId); this.queueChips.delete(p.queue_id); }
      const t = this.turns.find((x) => x.queued && x.text === p.user_text);
      if (t) t.queued = false; else this.turns.push({ role: 'j', text: p.user_text, mode: 'queue' });
    }
    this.follow(runId, {});
  }

  tick(L) {
    if (L.frozen) return;
    if (L.pending) {
      const backlog = L.pending.split(/\s+/).length;
      const n = backlog > 80 ? 3 : backlog > 30 ? 2 : 1;
      let out = '';
      for (let i = 0; i < n && L.pending; i++) {
        const m = L.pending.match(/^\s*\S+\s*/);
        const piece = m ? m[0] : L.pending;
        out += piece;
        L.pending = L.pending.slice(piece.length);
      }
      L.shown += out.length;
      L.turn.text += out;
      this.notify();
    }
    if (L.done && !L.pending) { this.finish(L); return; }
    setTimeout(() => this.tick(L), 70);
  }

  finish(L) {
    if (L.finished) return;
    L.finished = true;
    L.turn.live = false;
    const said = L.turn.text.trim();
    if (said) {
      // his first sentence as a pill is for when the thread ISN'T on screen. With its conversation open
      // beside the card (or inside it), the pill only covers the card's headline.
      const shown = (OG.column && OG.column.has && OG.column.has(this.id)) || mounted.has(this.id);
      if (!shown) OG.orb.say(firstSentence(said));
      OG.emit('say', { text: said, from: 'guy', block: this.block });
    }
    OG.orb.mood('idle');
    this.notify();
  }
}

const surfaces = new Map();
export function controller(block) {
  if (!block) block = homeBlock();
  let s = surfaces.get(block.id);
  if (!s) { s = new Surface(block); surfaces.set(block.id, s); } else if (block.title) s.block = block;
  return s;
}

// ---------------------------------------------------------------- the claim frame (frontier 4)
// One turn, one claim: the cards the line is about, framed with an -ing word the moment it is
// sent, until the turn lands, fails or the user stops it (Esc, or the frame's ×). The word and the ids
// come from the server (board/claim.py, POST /api/talk/claim); live.js draws the frame and
// redraws it when the canvas re-renders. States: 'open' (the verb, with ×) → 'stopping' →
// 'said' (a closing word for 3s: didn't work / can't stop this one) → over.
const CLAIM_SAY_MS = 3000;
let claimNow = null;
class Claim {
  constructor() {
    this.ids = [];
    this.label = '';
    this.state = 'open';
    this.over = false;
    this.cancelled = false;
    this.s = null;                // the surface whose run this claim waits on (bind)
    this.view = null;
    this.t = 0;
  }
  changed(extra) { if (OG.emit) OG.emit('claim', { claim: this, ...(extra || {}) }); }
  // pile: the frame sits on that pile as one (a brief of a whole pile), not on each of its cards
  frame(ids, label, pile) {
    if (this.over || this.state !== 'open') return this;
    if (pile) this.pile = pile;
    for (const id of ids || []) if (typeof id === 'string' && !this.ids.includes(id)) this.ids.push(id);
    if (label) this.label = label;
    this.changed();
    return this;
  }
  // wait on the run a send opened: `sent` is what Surface.send answered
  bind(s, sent) {
    if (this.over || this.state !== 'open') return this;
    if (!s || !sent || !sent.run_id) { if (sent && sent.queued) this.end(); else this.fail(); return this; }
    this.s = s;
    this.view = { onChange: () => this.check() };
    s.views.add(this.view);
    this.check();
    return this;
  }
  unbind() {
    if (this.s && this.view) this.s.views.delete(this.view);
    this.view = null;
  }
  check() {
    const s = this.s;
    if (!s || s.streaming || this.state !== 'open') return;
    const last = [...s.turns].reverse().find((t) => t.role === 'guy');
    if (last && last.error) this.fail();
    else if (last && last.cut) this.end();     // he was cut off some other way (stop, a steer): no land ring
    else this.land();
  }
  land() {
    if (this.over) return;
    this.end({ landed: this.ids.slice() });    // the frame goes, the land ring plays (live.js)
  }
  fail() { this.say("didn't work"); }
  say(words) {
    if (this.over) return;
    this.unbind();
    clearTimeout(this.t);
    if (!this.ids.length) { this.end(); return; }
    this.state = 'said';
    this.label = words;
    this.changed();
    this.t = setTimeout(() => this.end(), CLAIM_SAY_MS);
  }
  // the turn had already done something by the time he said stop: say so, don't pretend
  stuck() {
    if (!this.ids.length) return;
    this.over = false;
    claimNow = this;
    this.say("can't stop this one");
  }
  end(extra) {
    if (this.over) return;
    this.over = true;
    clearTimeout(this.t);
    this.unbind();
    if (claimNow === this) claimNow = null;
    this.changed(extra);
  }
  // Esc / ×: stop waiting, and stop the run if the turn opened one
  async cancel() {
    if (this.over || this.state !== 'open') return;
    this.cancelled = true;
    const s = this.s;
    this.unbind();
    if (!(s && s.streaming)) { this.end(); return; }
    this.state = 'stopping';
    this.label = 'stopping…';
    this.changed();
    let r = null;
    try { r = await s.interrupt(); } catch (e) { r = { was_streaming: true, stopped: false }; }
    if (r && r.was_streaming && r.stopped === false) this.say("can't stop this one"); else this.end();
  }
}
export const claim = {
  open() { if (claimNow) claimNow.end(); claimNow = new Claim(); return claimNow; },
  now: () => claimNow,
  of: (id) => (claimNow && !claimNow.over && claimNow.ids.includes(id) ? claimNow : null),
};

// A card whose id changed under it (a to-do gets its anchor once its thread opens: task:<hash> → task:^xxxx) keeps
// the SAME conversation object: its turns, its open session and a reply that is still streaming. Without this a
// refresh mid-reply started a fresh, empty one and the words vanished until they were saved.
export function rekey(oldId, block) {
  const s = surfaces.get(oldId);
  if (!s || !block || block.id === oldId || surfaces.has(block.id)) return false;
  surfaces.delete(oldId);
  s.id = block.id;
  s.block = block;
  surfaces.set(block.id, s);
  return true;
}

// ---------------------------------------------------------------- thread UI (bloom slot)
// History + the streaming reply + chips. NO composer: the talk bar is the one input. Collapsed to
// the last SHOW_LAST turns; 'earlier (n)' expands the rest.
const SHOW_LAST = 2;
class ThreadView {
  constructor(surface, slot) {
    this.s = surface;
    this.slot = slot;
    this.expanded = false;
    this.root = el('div', 'sf');
    const hd = el('div', 'sf-hd');
    this.earlier = el('button', 'sf-earlier', '');
    this.earlier.type = 'button';
    this.earlier.hidden = true;
    this.earlier.addEventListener('click', () => { this.expanded = !this.expanded; this.render(this.s); });
    const gap = el('span', 'sf-gap');
    this.stop = el('button', 'sf-stop', 'stop');
    this.stop.type = 'button';
    this.stop.title = 'cut him off (Esc in the talk bar)';
    this.stop.addEventListener('click', () => this.s.interrupt());
    this.open = el('a', 'sf-open', 'open in hermes');
    this.open.hidden = true;
    hd.append(this.earlier, gap, this.stop, this.open);
    this.list = el('div', 'sf-turns');
    this.list.setAttribute('aria-live', 'polite');
    this.root.append(hd, this.list);
    slot.replaceChildren(this.root);
    surface.views.add(this);
    this.onChange(surface);
  }
  onChange(s) {
    const rec = s.opened && s.opened.desktop;
    if (rec) {
      this.open.hidden = false;
      this.open.href = rec.deep_link;
      this.open.title = rec.hint + ' · session ' + rec.session_id;
    }
    this.root.classList.toggle('sf-streaming', s.streaming);
    this.stop.hidden = !s.streaming;
    this.render(s);
  }
  render(s) {
    // an empty thread takes no room in the bloom (calm); it appears with the first line
    this.slot.hidden = !s.turns.length;
    const atBottom = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 40;
    const hiddenN = this.expanded ? 0 : Math.max(0, s.turns.length - SHOW_LAST);
    this.earlier.hidden = s.turns.length <= SHOW_LAST;
    this.earlier.textContent = this.expanded ? 'fewer' : `earlier (${hiddenN})`;
    // rebuild the rows (cheap), but only a turn's FIRST row animates in: a re-render every 70 ms
    // tick used to restart sf-in on every row, so the thread sat dim.
    this.seen = this.seen || new WeakSet();
    this.seenCut = this.seenCut || new WeakSet();   // the split animation plays once too
    this.list.replaceChildren(...s.turns.slice(hiddenN).map((t) => {
      const row = this.turnEl(t);
      if (this.seen.has(t)) row.classList.add('sf-seen'); else this.seen.add(t);
      if (t.cut) { if (this.seenCut.has(t)) row.classList.add('sf-cut-seen'); else this.seenCut.add(t); }
      return row;
    }));
    if (atBottom || s.streaming) this.list.scrollTop = this.list.scrollHeight;
  }
  turnEl(t) {
    const row = el('div', 'sf-turn sf-' + t.role + (t.live ? ' sf-live' : '') + (t.cut ? ' sf-cut' : '') + (t.queued ? ' sf-queued' : '') + (t.kickoff ? ' sf-kick' : ''));
    if (t.role === 'j') {
      if (t.kickoff) row.append(el('span', 'sf-note sf-note-lead', 'started with'));
      if (t.picked) row.append(el('span', 'sf-note sf-note-lead', 'you picked'));
      row.append(el('span', 'sf-text', t.text));
      if (t.mode && t.mode !== 'say') row.append(el('span', 'sf-mtag sf-mtag-' + t.mode, t.mode));
      if (t.queued) row.append(el('span', 'sf-note', 'waits for him to finish'));
    } else {
      const txt = el('span', 'sf-text', t.text || (t.live ? '' : '…'));
      row.append(txt);
      if (t.live && !t.cut) row.append(el('span', 'sf-caret'));
      if (t.cut && t.unheard) {
        const u = el('span', 'sf-unheard', t.unheard.slice(0, 220) + (t.unheard.length > 220 ? '…' : ''));
        u.title = t.parked ? "didn't land: parked in the rail" : "didn't land";
        row.append(u);
      }
      if (t.cut) row.append(el('span', 'sf-mtag sf-mtag-cut', 'cut off'));
      if (t.tools && t.tools.length && t.live) row.append(el('span', 'sf-tools', '⋯ ' + t.tools.slice(-2).join(', ')));
      if (t.error) row.append(el('span', 'sf-err', t.error));
    }
    return row;
  }
  destroy() { this.s.views.delete(this); }
}

const mounted = new Map();   // block id → ThreadView
function mount(block, panel) {
  if (!block || !panel) return;
  if (OG.column && OG.column.has(block.id)) return;   // the column owns this conversation (Phase 2)
  const slot = panel.querySelector('[data-slot=thread]');
  if (!slot) return;
  const prev = mounted.get(block.id);
  if (prev) prev.destroy();
  const s = controller(block);
  const v = new ThreadView(s, slot);
  mounted.set(block.id, v);
  s.open().catch((e) => OG.toast('work surface: ' + (e.message || e), true));
  return v;
}

// ---------------------------------------------------------------- programmatic send (WS-T)
const resolve = (block) => {
  if (!block) return homeBlock();
  if (typeof block === 'string') return block === 'home' ? homeBlock() : ((OG.board && OG.board.byId && OG.board.byId[block]) || { id: block });
  return block;
};
// say(block, text, {mode, kickoff, picked}): open (or create) the block's surface and send. The
// reply streams into the bloom's thread when that block is open, else into the talk bar's strip.
export async function say(block, text, opts = {}) {
  block = resolve(block);
  text = (text || '').trim();
  if (!text) return null;
  const s = controller(block);
  await s.open();
  // the reply needs a surface on screen: the column (Phase 2) when it's not mounted in a bloom
  if (!mounted.has(block.id) && !(OG.column && OG.column.has(block.id))) {
    if (OG.column) { try { OG.column.open(block); } catch (e) { console.warn('[surface] column', e); } }
    else if (OG.talk && OG.talk.focus) OG.talk.focus();
  }
  return s.send(text, opts.mode, opts);
}
// 'work on this with me': with an empty thread, send the headline's first_message (the one guy
// wrote it); otherwise there's nothing to start, just point the user at the talk bar.
export function firstMessage(block) {
  const h = block && block.headline;
  if (h && h.first_message) return h.first_message;
  return `let's work on this: ${plainTitle(block)}. where do we start?`;
}
export async function workOn(block) {
  block = resolve(block);
  const s = controller(block);
  await s.open();
  if (OG.talk && OG.talk.focus) { try { OG.talk.focus(); } catch (e) { /* ignore */ } }
  if (s.turns.length || s.streaming) return { started: false };
  const r = await say(block, firstMessage(block), { mode: 'say', kickoff: true });
  return { started: true, ...(r || {}) };
}

if (OG) {
  OG.on('bloom-open', ({ block, panel }) => mount(block, panel));
  // the column (Phase 2) owns the conversation now: when it opens for a block, the bloom's
  // thread slot unmounts — the bloom keeps the face and actions, never two copies of the thread.
  OG.on('column', ({ open, block } = {}) => {
    if (!open || !block) return;
    const v = mounted.get(block.id);
    if (v) { const slot = v.slot; v.destroy(); mounted.delete(block.id); slot.hidden = true; slot.replaceChildren(); }
  });
  OG.on('bloom-close', ({ block }) => {
    const v = block && mounted.get(block.id);
    if (v) { v.destroy(); mounted.delete(block.id); }
  });
  OG.surface = {
    open(block) {
      // 'open work surface': bloom the block (its thread mounts there) and hand the user the talk bar
      if (!block) return controller(homeBlock()).open();
      const v = mounted.get(block.id);
      const talkFocus = () => { if (OG.talk && OG.talk.focus) OG.talk.focus(); };
      if (v) { talkFocus(); return v.s.open(); }
      if (OG.focus) { try { OG.focus(block.id); } catch (e) { /* ignore */ } }
      setTimeout(talkFocus, 320);
      return controller(block).open();
    },
    say,
    workOn,
    firstMessage,
    plainTitle,
    controller,
    rekey,
    classify,
    claim,
    isMounted: (id) => mounted.has(id),
    list: () => OG.api.get('/api/surface/list'),
  };
}
