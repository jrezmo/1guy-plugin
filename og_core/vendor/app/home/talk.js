// WS-F/T talk bar, Phase 2: THE adaptive bar. Every line lands in exactly one place
// (unified audit §5.2): a fly-off to its target (command, with real landing + 5s undo), the
// chat column bound to the item's thread (conversation), or the stage (show). The reply strip
// is RETIRED — his words land in the column now, nothing dies in a strip above the bar.
// Guess at submit: a tiny glyph while you type (↗ card / ▤ column); tap it to flip. The server's
// deterministic resolver (/api/talk) decides the shape; the UI obeys the shape, and the column
// (OG.column, column.js) is where a conversation, a clarify or a promotion lives.
// '/' focuses, Esc cuts him off / folds, the mic transcribes through /api/surface/transcribe.
import './surface.js';
import './column.js';
import './sounds.js';

const OG = window.OneGuy;

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

const HOME_PH = 'ask me anything about your stuff…';
const BLOCK_PH = 'talk about this…';
const SEL_PH = 'ask about these…';
const EASE_OUT = 'cubic-bezier(.22,.8,.24,1)';

// the submit-time guess, <5ms, mirroring the resolver's verb list (app/talk_resolve.py)
const VERBS = /^(add|put|move|reschedule|push|mark|tick|done|complete|delete|drop|remove|run|restart|pause|show|open)\b/i;
const QUESTION = /\?|^\s*(what|why|how|when|where|who|should|could|can|is|are|do|does|did|tell me|let'?s|help me)\b/i;
// the same cue the router uses (app/router.py is_switch); the router still decides WHICH card, if any
const SWITCH = /^\s*(?:no[,.]?\s+|actually[,.]?\s+|ok(?:ay)?[,.]?\s+)?(?:let'?s\s+)?(?:start with|switch to|go to|move (?:on )?to|jump to|work on|look at|focus on)\b|\binstead\s*[.!]?\s*$/i;
const guessShape = (text) => (!QUESTION.test(text) && VERBS.test(text.trim()) ? 'command' : 'conversation');
const GLYPH = { command: '↗', conversation: '▤' };
const GLYPH_T = { command: 'this looks like a command: it flies to its target (tap to talk it through instead)',
  conversation: 'this looks like a conversation: it opens the column (tap to send it as a command)' };

let focused = null;          // the focused block (from 'focus' events)
let guess = null;            // The user's manual glyph override for the current line
let tag = null;              // The user's manual say/steer/queue override while he's talking (was never declared: typing over him threw)
let repaint = () => {};
let undoT = null;

async function surfaceApi() {
  if (OG.surface && OG.surface.controller) return OG.surface;
  await import('./surface.js');
  return OG.surface;
}
const homeTarget = { id: 'home', kind: 'home', title: 'home', summary: 'the whole board' };
const target = () => (OG.column && OG.column.current()) || focused || homeTarget;
// the cards he drew a box round (core.js OneGuy.select); a card in focus outranks them
const picked = () => (focused ? [] : (OG.selected || []));
const cards = (n) => n + (n === 1 ? ' card' : ' cards');
const plain = (b) => (OG.surface && OG.surface.plainTitle ? OG.surface.plainTitle(b) : (b.title || b.id));

function build() {
  const bar = el('form', 'tk-bar');
  bar.setAttribute('role', 'search');
  const about = el('span', 'tk-about');
  about.hidden = true;
  const aboutT = el('span', 'tk-about-t', '');
  const aboutX = el('button', 'tk-about-x', '×');
  aboutX.type = 'button';
  aboutX.setAttribute('aria-label', 'back to home');
  aboutX.title = 'back to home';
  about.append(aboutT, aboutX);
  const glyph = el('button', 'tk-glyph', '');
  glyph.type = 'button';
  glyph.hidden = true;
  const tagBtn = el('button', 'sf-tag sf-tag-say tk-tag', 'say');
  tagBtn.type = 'button';
  tagBtn.title = 'he’s talking: this is how he’ll take your line (tap to flip)';
  const input = el('input', 'tk-input');
  input.placeholder = HOME_PH;
  input.autocomplete = 'off';
  input.enterKeyHint = 'send';
  input.setAttribute('aria-label', 'talk to the one guy');
  const stop = el('button', 'tk-stop', 'stop');
  stop.type = 'button';
  stop.title = 'cut him off (Esc)';
  const mic = el('button', 'tk-mic', '');
  mic.type = 'button';
  mic.setAttribute('aria-label', 'talk (mic)');
  mic.title = 'tap to record, tap again to send';
  mic.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"/></svg>';
  bar.append(about, glyph, tagBtn, input, stop, mic);
  document.body.append(bar);

  const current = () => (OG.surface && OG.surface.controller ? OG.surface.controller(target()) : null);
  const streamingNow = () => { const s = current(); return !!(s && s.streaming); };
  const paint = () => {
    const live = streamingNow();
    const t = input.value ? (live ? (tag || (OG.surface && OG.surface.classify ? OG.surface.classify(input.value, true) : 'steer')) : null) : null;
    tagBtn.textContent = t || 'say';
    tagBtn.className = 'sf-tag tk-tag sf-tag-' + (t || 'say');
    tagBtn.hidden = !live || !t;
    stop.hidden = !live;
    const g = guess || (input.value ? guessShape(input.value) : null);
    glyph.hidden = !input.value || live;
    glyph.textContent = g ? GLYPH[g] : '';
    glyph.title = g ? GLYPH_T[g] : '';
    glyph.classList.toggle('tk-glyph-flipped', !!guess);
    if (focused) {
      const name = plain(focused);
      aboutT.textContent = 'about: ' + (name.length > 34 ? name.slice(0, 33) + '…' : name);
      about.title = 'talking about: ' + name;
    } else if (picked().length) {
      const names = picked().map((id) => OG.board && OG.board.byId[id]).filter(Boolean).map(plain);
      aboutT.textContent = 'about: ' + cards(picked().length);
      about.title = 'asking about: ' + names.join(' · ');
      about.hidden = false;
    } else if (OG.column && OG.column.current()) {
      const name = plain(OG.column.current());
      aboutT.textContent = 'about: ' + (name.length > 34 ? name.slice(0, 33) + '…' : name);
      about.hidden = false;
    } else about.hidden = true;
    aboutX.title = picked().length ? 'let them go' : 'back to home';
    aboutX.setAttribute('aria-label', aboutX.title);
    input.placeholder = picked().length ? SEL_PH : (focused || (OG.column && OG.column.current())) ? BLOCK_PH : HOME_PH;
    bar.classList.toggle('tk-focused', !!(focused || picked().length || (OG.column && OG.column.current())));
    bar.classList.toggle('tk-live', live);
  };
  glyph.addEventListener('click', () => {
    guess = (guess || guessShape(input.value)) === 'command' ? 'conversation' : 'command';
    paint();
    input.focus();
  });
  tagBtn.addEventListener('click', () => {
    tag = { say: 'steer', steer: 'queue', queue: 'say' }[tagBtn.textContent] || 'say';
    paint();
    input.focus();
  });
  aboutX.addEventListener('click', () => {
    if (picked().length) { OG.select.clear(); input.focus(); return; }     // × lets the selection go, nothing else
    if (OG.column && OG.column.current()) OG.column.close();
    if (OG.unfocus) OG.unfocus();
    focused = null;
    paint();
    input.focus();
  });
  stop.addEventListener('click', () => { const s = current(); if (s && s.streaming) s.interrupt(); });
  input.addEventListener('input', () => { if (!input.value) { guess = null; tag = null; } paint(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const s = current();
      // with a card in focus Esc is always 'back one step' (card grammar); 'stop' is how you cut him off there
      if (s && s.streaming && !(OG.focused && !input.value)) { e.preventDefault(); s.interrupt(); e.stopPropagation(); return; }
      if (OG.column && OG.column.current() && !input.value) { e.preventDefault(); OG.column.close(); return; }
      input.blur();
      if (!input.value && OG.focused) return;
    }
    e.stopPropagation();
  });
  bar.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    const g = guess || guessShape(text);
    const streaming = streamingNow();
    const picked = tag;
    input.value = '';
    guess = null;
    tag = null;
    paint();
    // talking over him is still a cut-in — unless the line is plainly about switching cards
    // ("start with the PR instead"): that one goes to the router like any other, even mid-answer
    // (and unless he picked several cards: that line is about them, a thread of its own)
    if (streaming && !SWITCH.test(text) && picked().length < 2) {
      const mode = picked || OG.surface.classify(text, true);
      talk(text, mode);
      return;
    }
    // even with a conversation open the line goes through the router first: "start with the PR
    // instead" is about ANOTHER card, and the router knows which. conversation keeps
    // everything that isn't clearly about another card in the thread that's open.
    submit(text, g);
  });
  // Esc while a claim frame is up stops that turn, before anything else Esc means
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || document.querySelector('.og-ask-scrim')) return;
    const c = OG.surface && OG.surface.claim && OG.surface.claim.now();
    if (!c || c.over || c.state !== 'open' || !c.ids.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    c.cancel();
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    const a = document.activeElement;
    if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) return;
    e.preventDefault();
    input.focus();
  });
  OG.on('focus', ({ block } = {}) => {
    focused = block || null;
    paint();
  });
  OG.on('select', paint);
  repaint = paint;
  setInterval(paint, 800);
  paint();

  // the sounds toggle lives in the header now (sounds.js owns it)

  // ---- mic ----
  let rec = null;
  let chunks = [];
  mic.addEventListener('click', async () => {
    if (rec && rec.state === 'recording') { rec.stop(); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      OG.toast('the mic needs https or localhost (this page is neither)', true);
      return;
    }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (err) {
      OG.toast('mic: ' + (err.message || err), true);
      return;
    }
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((m) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) || '';
    rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) chunks.push(ev.data); };
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      mic.classList.remove('tk-rec');
      bar.classList.remove('tk-recording');
      OG.orb.mood('think');
      const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
      try {
        const text = await transcribe(blob);
        if (!text) { OG.orb.say("didn't catch that."); OG.orb.mood('idle'); return; }
        const g = guessShape(text);
        // door B (doors.js): with the box up and no card open, what was said goes in the box, a line a sentence
        if (!(OG.column && OG.column.current()) && OG.door && OG.door.take(text)) { OG.orb.mood('idle'); return; }
        if (OG.column && OG.column.current()) {
          const s = OG.column.surface();
          if (s) { sounds('send'); s.send(text, 'say'); }
          return;
        }
        submit(text, g);
      } catch (err) {
        OG.toast('transcribe: ' + (err.message || err), true);
        OG.orb.mood('idle');
      }
    };
    rec.start();
    mic.classList.add('tk-rec');
    bar.classList.add('tk-recording');
    OG.orb.mood('listen');
  });
  return { bar, input, paint };
}

async function transcribe(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  const r = await OG.api.post('/api/surface/transcribe', { audio_b64: btoa(bin), mime: blob.type || 'audio/webm' });
  return (r.text || '').trim();
}

const sounds = (name) => { if (OG.sounds) OG.sounds.play(name); };

// ---------------------------------------------------------------- the shapes
// The claim frame (frontier 4): the moment a line is sent, the cards it is about are framed with
// the -ing word of the ask (board/claim.py decides both, instantly: no model, no thread). The
// lifecycle is surface.js's Claim; live.js draws it. Nothing targeted = no frame.
function claimTurn(text) {
  if (!(OG.surface && OG.surface.claim)) return null;
  const c = OG.surface.claim.open();
  OG.api.post('/api/talk/claim', { text, focus: focused ? focused.id : null, view: OG.viewContext ? OG.viewContext() : undefined })
    .then((r) => { if (r) c.frame(r.ids || [], r.label); })
    .catch(() => {});
  return c;
}
// the cards the router resolved join the frame when its answer comes back
function claimResolved(c, r) {
  if (!c) return;
  const byId = (OG.board && OG.board.byId) || {};
  const ids = [r.target && r.target.id, r.route && r.route.kind === 'block' && r.route.key,
    ...((r.route && r.route.refers && r.route.refers.ids) || [])].filter((id) => id && byId[id]);
  if (ids.length) c.frame(ids);
}
// He said stop before the resolver answered. Whatever it already did is said plainly: a change
// that landed keeps its undo, a run it opened is stopped (or the frame says it can't be).
async function lateCancel(c, r, text) {
  if (r.shape === 'landed') { c.stuck(); return landed(r, text); }
  const route = r.route || {};
  if (r.shape === 'conversation' && (route.kind === 'todo' || route.kind === 'new') && route.key) {
    let stop = null;
    try { stop = await OG.api.post('/api/surface/interrupt', { block_id: route.key, shown: 0 }); } catch (e) { stop = { was_streaming: true, stopped: false }; }
    if (stop && stop.was_streaming && stop.stopped === false) c.stuck();
    if (OG.refresh) OG.refresh({ quiet: true });
  }
  return null;
}

async function submit(text, g) {
  sounds('send');
  // the cards he picked (a lasso): one of them is simply that card; several are a thread of their own
  const sel = picked().slice();
  const focus = focused ? focused.id : sel.length === 1 ? sel[0] : null;
  const c = claimTurn(text);
  let r;
  try {
    // view = what the user is looking at as he says it (core.js viewContext): "this" / "these" mean something
    r = await OG.api.post('/api/talk', { text, focus, guess: g, show: true,
      view: OG.viewContext ? OG.viewContext() : undefined });
  } catch (err) {
    sounds('error');
    if (c && c.cancelled) return;
    OG.orb.say("couldn't reach the resolver — it's a conversation, then.");
    if (c) c.fail();
    talk(text, 'say');
    return;
  }
  if (c && c.cancelled) return lateCancel(c, r, text);
  claimResolved(c, r);
  const shape = r.shape;
  // THE SELECTION IS A JUDGMENT: asking about several cards at once says "these go together", the
  // relational signal the ledger is for. One line per ask; then the selection has done its job.
  if (sel.length && shape === 'conversation') {
    if (sel.length > 1) OG.judge('select', sel[0], { ids: sel }, text.slice(0, 120));
    OG.select.clear();
  }
  if (shape === 'landed') { if (c) setTimeout(() => c.land(), 430); return landed(r, text); }
  if (shape === 'failed') { if (c) c.fail(); return failed(r, text); }
  if (c && shape !== 'conversation') c.end();   // confirm / clarify / show: The user acts next, nothing is being worked on
  if (shape === 'confirm') return confirmOnTarget(r, text);
  if (shape === 'clarify') return promote(r, text);
  if (shape === 'show') return show(r, text);
  return conversation(r, text, c);       // conversation (D10: unsure → the column)
}

// where a shape's target visually lives: the card on the canvas, or the briefing card
function targetEl(id) {
  if (!id || id === 'widget:todo' || id.startsWith('job:')) return null;
  const bid = (OG.board && OG.board.byId && OG.board.byId[id]) ? id : id.replace(/^task:/, 'task:');
  const el2 = document.querySelector(`.og-card[data-id="${CSS.escape(id)}"], #og-brief [data-id="${CSS.escape(id)}"]`);
  return el2 || null;
}

function ring(target, cls = 'og-land', ms = 1600) {
  const n = targetEl(typeof target === 'string' ? target : target && target.id);
  if (!n) return null;
  n.classList.add(cls);
  setTimeout(() => n.classList.remove(cls), ms);
  return n;
}

// FLY-OFF: the line lifts off the bar as a chip and flies (springy arc) to its target,
// the target lights, the change lands, a tick settles, 5s undo.
function landed(r, text) {
  const tgt = r.target || {};
  const to = targetEl(tgt.id);
  flyChip(text, to, { land: true });
  sounds('land');
  if (to) setTimeout(() => { ring(tgt.id); sounds('done'); }, 430);
  OG.orb.say(r.summary || 'done.', { ms: 2400 });
  showUndo(r.undo, r.summary || 'done');
  setTimeout(() => { if (OG.refresh) OG.refresh({ quiet: true }); }, 700);
}

// risky: it flies off but lands as tap-to-confirm ON the lit target, not in chat
function confirmOnTarget(r, text) {
  const tgt = r.target || {};
  const to = targetEl(tgt.id);
  flyChip(text, to, { land: false });
  sounds('needs-you');
  OG.orb.say(r.question || 'tap the target to confirm.', { ms: 3200 });
  const anchor = to || barAnchor();
  const pill = el('button', 'tk-confirm', 'tap to confirm · ' + (r.question || '').replace(' — tap the target to confirm', ''));
  pill.style.position = 'fixed';
  pill.style.zIndex = '72';
  const rect = anchor ? anchor.getBoundingClientRect() : barAnchor().getBoundingClientRect();
  pill.style.left = Math.max(8, rect.left - 10) + 'px';
  pill.style.top = Math.max(8, rect.top - 40) + 'px';
  document.body.append(pill);
  if (to) to.classList.add('og-confirm-ring');
  let done = false;
  const finish = (res) => {
    if (done) return;
    done = true;
    pill.remove();
    if (to) to.classList.remove('og-confirm-ring');
    if (res && res.shape === 'landed') landed(res, text);
    else if (res) { sounds('error'); OG.toast(res.error || "it didn't land", true); }
  };
  pill.addEventListener('click', async () => {
    pill.textContent = '…';
    try { finish(await OG.api.post('/api/talk/do', { token: r.token })); }
    catch (e) { finish({ shape: 'failed', error: e.message || e }); }
  });
  setTimeout(() => finish(null), 12000);
}

// PROMOTION (morph, never a jump): the chip unfurls into the column, anchored to the target,
// with the one guy's question at the top.
function promote(r, text) {
  const cands = r.candidates || [];
  const block = (r.target && OG.board && OG.board.byId[r.target.id]) || r.target ||
    (cands[0] ? (OG.board.byId[cands[0].id] || cands[0]) : null) || focused || homeTarget;
  const names = cands.length > 1 ? cands.map((c) => plain(c)).join(' · ') : '';
  OG.column.open(OG.board && OG.board.byId[block.id] ? OG.board.byId[block.id] : block,
    { question: r.question + (names ? ` (${names})` : ''), why: 'needs you' });
  sounds('needs-you');
  OG.orb.say(r.question || 'which one?', { ms: 3200 });
}

// the stage (Phase 4 owns it; the bar just obeys the shape)
function show(r, text) {
  if (OG.stage && r.show) { OG.stage.open(r.show, { asked: true }); return; }
  OG.orb.say('the stage is asleep here — ' + ((r.show && r.show.src) || ''), { ms: 2800 });
}

function failed(r, text) {
  sounds('error');
  if (r.target) {
    const n = targetEl(r.target.id);
    if (n && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      n.animate([{ transform: 'translateX(0)' }, { transform: 'translateX(-6px)' }, { transform: 'translateX(6px)' },
        { transform: 'translateX(0)' }], { duration: 300 });
    }
  }
  OG.orb.say(r.error || "that didn't land — talk it through?", { ms: 3200 });
  talk(text, 'say');       // the column takes it from here
}

// the COLUMN: a conversation bound to one thread. The key comes from the router's
// route object (docs/request-router.md) — never a bare 'home' default; 'home' only
// when the router explicitly said small talk.
async function conversation(r, text, c) {
  // the frame waits on the run this send opens: it lands, fails, or Esc stops it
  const send = async (s) => {
    if (!s) { if (c) c.fail(); return; }
    let sent = null;
    try { sent = await s.send(text, 'say'); } catch (e) { OG.toast(String(e.message || e), true); }
    if (c) c.bind(s, sent);
  };
  const route = r.route || {};
  const th = r.thread || {};
  const key = route.key || th.key || 'home';
  // He asked about several cards at once: the server opened a NEW thread about them and already
  // said his line there (board/selection.py). The column opens on it, names the cards, and follows
  // that run; nothing is sent twice, and no single card's thread hears it.
  if (route.kind === 'selection') {
    const names = (route.names || []).map((n) => (n.length > 40 ? n.slice(0, 39) + '…' : n));
    await OG.column.open({ id: key, kind: 'talk', title: route.title || cards((route.ids || []).length) }, { why: 'your question' });
    if (OG.column.note) OG.column.note('→ about ' + cards(names.length) + ': ' + names.join(' · '));
    const cs = OG.column.surface();
    if (!cs || !route.run_id) { if (c) c.fail(); return; }
    if (!cs.turns.some((t) => t.role === 'j' && t.text === text)) {
      const at = cs.turns.findIndex((t) => t.live);
      cs.turns.splice(at < 0 ? cs.turns.length : at, 0, { role: 'j', text, mode: 'say' });
    }
    cs.follow(route.run_id, {});
    cs.notify();
    if (c && !c.cancelled) c.bind(cs, { run_id: route.run_id });
    return;
  }
  // Something is in focus (its thread is the open conversation): the line stays in that thread
  // unless the router points at a DIFFERENT card on the board ("start with the PR instead"). Then
  // that card comes into focus — pulled back from put-away if need be, the trail grows — and the
  // line is said in ITS thread.
  const here = focused || (OG.column && OG.column.current()) || null;
  const other = here && key !== here.id && OG.board && OG.board.byId[key];
  if (here && !other) {
    const cs = (OG.column && OG.column.current() && OG.column.surface()) || (await surfaceApi()).controller(here);
    await send(cs);
    return;
  }
  if (other) {
    if (OG.rail && OG.rail.has && OG.rail.has(key) && OG.summon) OG.summon(key, null, { quiet: true });
    OG.emit('steer', { block: other, from: here.id });
    if (OG.hop && OG.focused) OG.hop(key); else OG.focus(key);
    send((await surfaceApi()).controller(other));
    setTimeout(() => OG.column && OG.column.note && OG.column.note('→ went to ' + plain(other)), 600);
    return;
  }
  const isHome = key === 'home';
  const block = (OG.board && OG.board.byId[key]) || focused && focused.id === key && focused ||
    { id: key, kind: (key.split(':')[0] || 'home'), title: isHome ? 'home'
      : route.title || key.replace(/^task:/, '') };
  await OG.column.open(block, { why: route.kind === 'todo' ? 'on my list now'
    : r.reason === 'question' ? 'your question' : '' });
  // say where the line landed when it wasn't where the user was: a new card, his list, another card
  if (OG.column.note) {
    const nm = String(route.title || plain(block)).replace(/^(?:1guy\s*·\s*[\w-]+:\s*)+/, '');
    if (route.kind === 'todo') OG.column.note('→ put on your list: ' + nm);
    else if (route.kind === 'new') OG.column.note('→ started a new card: ' + nm);
    else if (route.kind === 'block' && !isHome) OG.column.note('→ went to ' + nm);
  }
  if (c && c.cancelled) return;
  await send(OG.column.surface());
}

// programmatic send (decide etc.) — unchanged contract
async function talk(text, mode) {
  if (!(focused && OG.surface && OG.surface.isMounted && OG.surface.isMounted(focused.id))) OG.caption.j(text);
  const api = await surfaceApi();
  const block = target();
  const s = api.controller(block);
  try { await s.send(text, mode); } catch (err) { OG.toast(String(err.message || err), true); }
}

// ---------------------------------------------------------------- motion
function barAnchor() {
  return document.querySelector('.tk-bar') || document.body;
}

function flyChip(text, toEl, { land = true } = {}) {
  const bar = barAnchor().getBoundingClientRect();
  const chip = el('span', 'tk-fly', text.length > 56 ? text.slice(0, 55) + '…' : text);
  chip.style.left = bar.left + bar.width / 2 + 'px';
  chip.style.top = bar.top + 'px';
  document.body.append(chip);
  const end = toEl ? toEl.getBoundingClientRect()
    : (OG.orb && OG.orb.el ? OG.orb.el.getBoundingClientRect() : bar);
  const ex = end.left + Math.min(end.width / 2, 140);
  const ey = end.top + Math.min(end.height / 2, 40);
  const dx = ex - (bar.left + bar.width / 2);
  const dy = ey - bar.top;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) { chip.remove(); return; }
  const anim = chip.animate([
    { transform: 'translate(-50%, 0) scale(1)', offset: 0 },
    { transform: `translate(calc(-50% + ${dx * 0.55}px), ${dy * 0.45 - 46}px) scale(.86)`, offset: 0.55, easing: EASE_OUT },
    { transform: `translate(calc(-50% + ${dx}px), ${dy}px) scale(.5)`, offset: 1 },
  ], { duration: 430, easing: EASE_OUT });
  // the fade runs linear so the chip stays visible through most of the arc
  const fade = chip.animate([{ opacity: 1 }, { opacity: 1, offset: 0.72 }, { opacity: 0, offset: 1 }],
    { duration: 430, easing: 'linear' });
  Promise.all([anim.finished.catch(() => {}), fade.finished.catch(() => {})]).then(() => chip.remove());
}

// The 5s undo pill. undo = the talk route's {token} (posted to /api/talk/undo); or anything else
// that lands with an undo (a proposal's edits) naming its own door in `undo.post` / `undo.body`;
// or a function (another gesture's own undo, e.g. a drop on time). `onUndone` lets the caller put
// back what only the page holds. The third argument is the options, or just the ms to stay up.
function showUndo(undo, summary, opts = {}) {
  const { onUndone, ms = 5000 } = typeof opts === 'number' ? { ms: opts } : (opts || {});
  if (!undo || !(undo.token || typeof undo === 'function')) return;
  clearTimeout(undoT);
  let pill = document.querySelector('.tk-undo');
  if (!pill) {
    pill = el('button', 'tk-undo');
    pill.type = 'button';
    const bar = barAnchor().getBoundingClientRect();
    pill.style.left = (bar.left + bar.width / 2) + 'px';
    document.body.append(pill);
  }
  pill.textContent = '↩ undo · ' + summary;
  pill.onclick = async () => {
    pill.textContent = '…';
    try {
      const r = typeof undo === 'function' ? ((await undo()) || { ok: true })
        : await OG.api.post(undo.post || '/api/talk/undo', undo.body || { token: undo.token });
      pill.remove();
      if (r.ok) { sounds('done'); if (onUndone) onUndone(r); if (OG.refresh) OG.refresh({ quiet: true }); }
      else OG.toast(r.error || 'the undo closed', true);
    } catch (e) { pill.remove(); OG.toast(String(e.message || e), true); }
  };
  undoT = setTimeout(() => pill.remove(), ms);
}

if (OG) {
  const ui = build();
  OG.talk = { focus: () => ui.input.focus(), say: talk, input: ui.input, submit, showUndo, undo: showUndo };
}
