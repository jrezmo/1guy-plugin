// 1guy home · sound + touch cues (lab/cues graduated). OFF until the user says yes, and NOTHING plays
// or buzzes when off — needs-you included. Silence is a promise.
//
//   OG.sounds.play('send'|'land'|'done'|'needs-you'|'error'|'whoosh'|'open'|'away')
//   OG.sounds.toggle / OG.sounds.set(bool) -> boolean      OG.sounds.on
//
// This module owns the whole feature: the voices (the lab's, plus three for the canvas), the
// phone haptics, WHEN they fire (it listens to the bus; nobody else has to remember to call it),
// and the header slot that first asks "sounds?" and then is the toggle. Every cue is original,
// synthesized (no assets), under half a second and quiet.
const OG = window.OneGuy || window.OG || {};
const KEY = 'og.sounds.on', ASKED = 'og.sounds.asked';
const ls = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
};
let on = ls.get(KEY) === '1';

let ctx = null;
function ac() {
  if (!ctx) { try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; } }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}
// one voice: an oscillator with a pitch glide and a soft envelope, optionally low-passed (lab.js tone)
function tone(c, { f = 440, f2 = null, t = 0, d = 0.18, type = 'sine', g = 0.1, q = null }) {
  const o = c.createOscillator(), a = c.createGain();
  const t0 = c.currentTime + t;
  o.type = type;
  o.frequency.setValueAtTime(f, t0);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, t0 + d);
  a.gain.setValueAtTime(0.0001, t0);
  a.gain.exponentialRampToValueAtTime(g, t0 + 0.012);
  a.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
  let node = o;
  if (q) { const bq = c.createBiquadFilter(); bq.type = 'lowpass'; bq.frequency.value = q; o.connect(bq); node = bq; }
  node.connect(a).connect(c.destination);
  o.start(t0);
  o.stop(t0 + d + 0.05);
}
// air: a short band of filtered noise that sweeps — the whoosh of cards changing places
function air(c, { d = 0.32, g = 0.05, from = 500, to = 2400 }) {
  const n = Math.floor(c.sampleRate * d), buf = c.createBuffer(1, n, c.sampleRate), ch = buf.getChannelData(0);
  for (let i = 0; i < n; i++) ch[i] = Math.random() * 2 - 1;
  const src = c.createBufferSource(), bp = c.createBiquadFilter(), a = c.createGain(), t0 = c.currentTime;
  src.buffer = buf;
  bp.type = 'bandpass'; bp.Q.value = 0.9;
  bp.frequency.setValueAtTime(from, t0);
  bp.frequency.exponentialRampToValueAtTime(to, t0 + d * 0.6);
  bp.frequency.exponentialRampToValueAtTime(from * 1.4, t0 + d);
  a.gain.setValueAtTime(0.0001, t0);
  a.gain.exponentialRampToValueAtTime(g, t0 + d * 0.35);
  a.gain.exponentialRampToValueAtTime(0.0001, t0 + d);
  src.connect(bp).connect(a).connect(c.destination);
  src.start(t0);
  src.stop(t0 + d + 0.02);
}

// the vocabulary: [what plays, the phone's buzz pattern in ms]
const CUES = {
  // the lab's five
  'send':      [(c) => tone(c, { f: 520, f2: 880, d: 0.14, type: 'triangle', g: 0.07 }), [8]],                      // something leaves
  'land':      [(c) => { tone(c, { f: 180, f2: 120, d: 0.12, g: 0.16 }); tone(c, { f: 900, d: 0.04, type: 'triangle', g: 0.025 }); }, [14]],   // a low wooden knock
  'done':      [(c) => { tone(c, { f: 660, d: 0.22, g: 0.06 }); tone(c, { f: 990, t: 0.09, d: 0.32, g: 0.06 }); }, [6, 40, 10]],              // a rising fifth
  'needs-you': [(c) => { tone(c, { f: 784, d: 0.45, g: 0.055 }); tone(c, { f: 784, t: 0.18, d: 0.5, g: 0.045 }); }, [20, 80, 20]],            // two soft taps of a bell
  'error':     [(c) => tone(c, { f: 220, f2: 140, d: 0.28, type: 'sawtooth', g: 0.03, q: 900 }), [30, 40, 30]],
  // the canvas's three
  'whoosh':    [(c) => air(c, {}), [5]],                                                                             // a lens change
  'open':      [(c) => { tone(c, { f: 392, f2: 523, d: 0.16, g: 0.05 }); tone(c, { f: 784, t: 0.06, d: 0.18, g: 0.025 }); }, [6]],            // a card opens
  'away':      [(c) => { air(c, { d: 0.22, g: 0.035, from: 1800, to: 700 }); tone(c, { f: 330, f2: 196, t: 0.05, d: 0.2, g: 0.05 }); }, [10]], // put away
};

function play(name) {
  if (!on || document.hidden) return;    // nothing plays when off
  const cue = CUES[name];
  if (!cue) return;
  const c = ac();
  if (c) { try { cue[0](c); } catch (e) { /* audio is garnish, never load-bearing */ } }
  try { if (navigator.vibrate) navigator.vibrate(cue[1]); } catch (e) { /* no haptics here */ }
}
function set(v) {
  on = !!v;
  ls.set(KEY, on ? '1' : '0');
  ls.set(ASKED, '1');                    // any answer settles the question
  paint();
  if (on) play('done');                  // the toggle confirms itself, audibly
  if (OG.emit) OG.emit('sounds', { on });
  return on;
}
export const sounds = { play, set, toggle: () => set(!on), get on() { return on; } };
if (OG) OG.sounds = sounds;

// ------------------------------------------------------------------ the choice, visible
// One place, the header. Until the user has answered, the toggle's own slot asks: "♪ sounds? on · off".
// Any answer (or his first lens switch / opened card — he's busy, don't nag) settles it and the
// slot becomes the plain toggle. Nothing floats next to the orb or over the page.
const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
let slot = null;
const asking = () => !ls.get(ASKED);
function paint() {
  if (!slot) return;
  slot.classList.toggle('og-snd-asking', asking());
  if (asking()) {
    const yes = mk('button', 'og-snd-yes', 'on'), no = mk('button', 'og-snd-no', 'off');
    yes.type = no.type = 'button';
    yes.onclick = () => set(true);
    no.onclick = () => set(false);
    slot.setAttribute('role', 'group');
    slot.setAttribute('aria-label', 'sounds? on or off');
    slot.replaceChildren(mk('span', 'og-snd-i', '♪'), mk('span', 'og-snd-t', 'sounds?'), yes, mk('span', 'og-snd-dot', '·'), no);
    return;
  }
  const b = mk('button', 'og-snd-b');
  b.type = 'button';
  b.setAttribute('aria-pressed', String(on));
  b.title = on ? 'sounds are on. tap to turn them off' : 'sounds are off. tap to turn them on';
  b.append(mk('span', 'og-snd-i', '♪'), mk('span', 'og-snd-t', 'sounds'), mk('span', 'og-snd-s', on ? 'on' : 'off'));
  b.onclick = () => sounds.toggle();
  slot.removeAttribute('role');
  slot.removeAttribute('aria-label');
  slot.replaceChildren(b);
}
function settle() {                       // he moved on without answering: that's "off", quietly
  if (!asking()) return;
  ls.set(ASKED, '1');
  paint();
}
function headerToggle() {
  const top = document.querySelector('#og-top'), lens = document.querySelector('#og-lens');
  if (!top || slot) return;
  slot = mk('div');
  slot.id = 'og-snd';
  top.insertBefore(slot, lens || null);
  paint();
}

// ------------------------------------------------------------------ when the cues fire
if (OG.on) {
  let lastWhoosh = 0;
  const whoosh = () => { const t = Date.now(); if (t - lastWhoosh > 400) { lastWhoosh = t; play('whoosh'); } };
  let entered = document.body.classList.contains('og-entered');
  OG.on('ready', () => { entered = true; headerToggle(); });
  OG.on('lens', () => { whoosh(); settle(); });
  OG.on('view', () => { if (entered) { whoosh(); settle(); } });
  OG.on('bloom-open', () => { if (entered) { play('open'); settle(); } });
  OG.on('park', () => play('away'));
  OG.on('fly', ({ phase } = {}) => play(phase === 'land' ? 'land' : 'send'));
  // a new 'for you' card arrived while the user is here: the bell, once (never on the first board)
  let known = null;
  OG.on('board', ({ board } = {}) => {
    const ids = new Set(((board && board.briefing && board.briefing.for_you) || []));
    if (known && [...ids].some((id) => !known.has(id))) play('needs-you');
    known = ids;
  });
  headerToggle();
}
