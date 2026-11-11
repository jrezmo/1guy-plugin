// 1guy home · the one guy (WS-A1). An amber orb with no face: personality is motion.
// Bobs when idle, pulses when listening, breathes when thinking, hops when pleased,
// stretches along his velocity and toward what he points at (media/README.md).
// Screen-space; a spring follows a target each frame. Motion code from the clips.
import { el, clamp, REDUCED } from './core.js';

const TAU = Math.PI * 2;

export function createOrb(layer, opts = {}) {
  const wrap = el('div', 'og-orbwrap');
  const halo = el('div', 'og-halo');
  const rings = el('div', 'og-rings');
  const trails = [0, 1, 2, 3].map(() => el('div', 'og-trail'));
  const body = el('div', 'og-orb');
  wrap.append(halo, rings, ...trails, body);
  const cap = el('div', 'og-cap');
  cap.setAttribute('role', 'status');
  layer.append(wrap, cap);

  const vw = () => window.innerWidth, vh = () => window.innerHeight;
  const s = {
    x: vw() / 2, y: vh() / 2, vx: 0, vy: 0,
    target: null,          // fn → {x, y}
    zip: false,
    mood: 'idle',
    appear: 0, appearT0: -1, hidden: true,
    hopT0: -1, lean: null, leanAmt: 0,
    capMs: 0, capT0: 0, capOn: false, capSide: 'right',
    hist: [],
  };
  let homeFn = () => ({ x: 60, y: vh() - 60 });
  let ringT = 0, last = performance.now();

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    const t = now / 1000;
    const tgt = (s.target || homeFn)();
    if (tgt) {
      const k = s.zip ? 240 : 70, c = s.zip ? 27 : 13;
      const ax = k * (tgt.x - s.x) - c * s.vx, ay = k * (tgt.y - s.y) - c * s.vy;
      s.vx += ax * dt; s.vy += ay * dt;
      s.x += s.vx * dt; s.y += s.vy * dt;
      if (REDUCED) { s.x = tgt.x; s.y = tgt.y; s.vx = s.vy = 0; }
    }
    const speed = Math.hypot(s.vx, s.vy);
    let stretch = clamp(speed / 2600, 0, 0.42);
    let ang = Math.atan2(s.vy, s.vx) * 180 / Math.PI;

    // lean toward what he points at
    s.leanAmt += ((s.lean ? 1 : 0) - s.leanAmt) * Math.min(1, dt * 6);
    let lx = 0, ly = 0;
    if (s.lean && s.leanAmt > 0.01) {
      const p = typeof s.lean === 'function' ? s.lean() : s.lean;
      if (p) {
        const dx = p.x - s.x, dy = p.y - s.y, d = Math.hypot(dx, dy) || 1;
        lx = (dx / d) * 7 * s.leanAmt; ly = (dy / d) * 7 * s.leanAmt;
        if (speed < 120) { stretch = Math.max(stretch, 0.13 * s.leanAmt); ang = Math.atan2(dy, dx) * 180 / Math.PI; }
      }
    }
    // idle bob, fades out while travelling
    const still = clamp(1 - speed / 160, 0, 1);
    const bob = REDUCED ? 0 : 4 * Math.sin(t * TAU / 2.4) * still;
    // hop
    let hop = 0, squash = 0;
    if (s.hopT0 >= 0) {
      const p = (t - s.hopT0) / 0.5;
      if (p < 1) hop = 16 * Math.sin(Math.PI * p);
      else if (p < 1.35) squash = Math.sin(Math.PI * (p - 1) / 0.35) * 0.14;
      else s.hopT0 = -1;
    }
    // listening pulse / thinking breath
    let sc = 1, glow = 1 + 0.12 * Math.sin(t * 2.1);
    if (s.mood === 'listen') {
      sc = 1 + 0.08 * (0.5 + 0.5 * Math.sin(t * TAU * 1.6));
      glow += 0.25;
      if (t - ringT > 0.9 && !REDUCED) { ringT = t; ring(); }
    } else if (s.mood === 'think') {
      glow = 1 + 0.22 * (0.5 + 0.5 * Math.sin(t * TAU * 0.6));
      sc = 1 + 0.03 * Math.sin(t * TAU * 0.6);
    }
    glow += 0.2 * stretch;
    // blink-on (spark → orb with a squash and settle)
    let ap = 1;
    if (s.appearT0 >= 0) {
      const p = (t - s.appearT0) / 0.9;
      if (p < 0.18) ap = 0.18 * Math.sin(Math.PI * p / 0.18);           // the spark blinks
      else if (p < 1) { const q = (p - 0.18) / 0.82; ap = 1 - Math.exp(-6 * q) * Math.cos(9 * q); }
      else { ap = 1; s.appearT0 = -1; }
    } else if (s.hidden) ap = 0;

    const X = s.x + lx, Y = s.y + ly + bob - hop;
    wrap.style.transform = `translate3d(${X.toFixed(2)}px, ${Y.toFixed(2)}px, 0)`;
    wrap.style.opacity = s.hidden && s.appearT0 < 0 ? '0' : '1';
    const sx = sc * ap * (1 + stretch) * (1 + squash), sy = sc * ap * (1 - stretch * 0.55) * (1 - squash * 0.6);
    body.style.transform = `rotate(${ang.toFixed(2)}deg) scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
    halo.style.transform = `scale(${(glow * ap).toFixed(3)})`;
    // trails while zipping
    s.hist.unshift({ x: X, y: Y });
    if (s.hist.length > 12) s.hist.length = 12;
    trails.forEach((d, i) => {
      const h = s.hist[(i + 1) * 2] || { x: X, y: Y };
      const a = clamp(speed / 1400, 0, 1) * (1 - i / trails.length) * 0.55 * ap;
      d.style.opacity = a.toFixed(3);
      d.style.transform = `translate(${(h.x - X).toFixed(1)}px, ${(h.y - Y).toFixed(1)}px) scale(${(1 - i * 0.12).toFixed(2)})`;
    });
    placeCap(X, Y, now);
    requestAnimationFrame(frame);
  }

  function ring() {
    const r = el('div', 'og-ring');
    rings.append(r);
    const a = r.animate([{ transform: 'scale(1)', opacity: 0.55 }, { transform: 'scale(2.8)', opacity: 0 }],
      { duration: 900, easing: 'cubic-bezier(.22,.8,.24,1)' });
    a.onfinish = () => r.remove();
  }

  // caption pill beside him: right by default, left near the rail, above near the talk bar
  function placeCap(X, Y, now) {
    if (!s.capOn) return;
    if (s.capMs && now - s.capT0 > s.capMs) { hideCap(); return; }
    const w = cap.offsetWidth, h = cap.offsetHeight || 32;
    const right = opts.rightLimit ? opts.rightLimit() : vw() - 12;
    const low = opts.lowZone ? opts.lowZone() : vh() - 70;
    let L, T;
    if (Y > low - 10) { L = clamp(X - 18, 10, right - w); T = Y - h - 26; }
    else if (X + 26 + w > right) { L = X - 26 - w; T = Y - h / 2; }
    else { L = X + 26; T = Y - h / 2; }
    L = clamp(L, 8, Math.max(8, right - w));
    T = clamp(T, 8 + (opts.topInset ? opts.topInset() : 0), vh() - h - 8);
    cap.style.transform = `translate3d(${L.toFixed(1)}px, ${T.toFixed(1)}px, 0)`;
  }
  function hideCap() {
    s.capOn = false;
    cap.classList.remove('on');
  }

  const orb = {
    el: wrap,
    get pos() { return { x: s.x, y: s.y }; },
    setHome(fn) { homeFn = fn; },
    home() { s.target = null; s.zip = false; },
    follow(fn, zip = false) { s.target = fn; s.zip = zip; },
    jump(x, y) { s.x = x; s.y = y; s.vx = s.vy = 0; },
    appear() { s.hidden = false; s.appearT0 = performance.now() / 1000; },
    show() { s.hidden = false; },
    // fly to a point; resolves on arrival (or after maxMs)
    flyTo(x, y, { zip = true, maxMs = 900 } = {}) {
      s.target = () => ({ x, y });
      s.zip = zip;
      return new Promise((resolve) => {
        const t0 = performance.now();
        const check = () => {
          const d = Math.hypot(s.x - x, s.y - y);
          if ((d < 4 && Math.hypot(s.vx, s.vy) < 60) || performance.now() - t0 > maxMs || REDUCED) resolve();
          else requestAnimationFrame(check);
        };
        check();
      });
    },
    say(text, { ms } = {}) {
      if (!text) { hideCap(); return; }
      { const t = String(text).trim().slice(0, 140); cap.textContent = t.charAt(0).toLowerCase() + t.slice(1); }   // his voice starts lowercase; names and acronyms keep theirs
      s.capOn = true;
      s.capT0 = performance.now();
      s.capMs = ms || Math.min(9000, 2400 + 45 * cap.textContent.length);
      cap.classList.remove('on');
      void cap.offsetWidth;
      cap.classList.add('on');
    },
    hush: hideCap,
    mood(m) {
      if (m === 'pleased') { s.hopT0 = performance.now() / 1000; s.mood = 'idle'; return; }
      s.mood = ['idle', 'listen', 'think'].includes(m) ? m : 'idle';
      layer.dataset.mood = s.mood;
    },
    hop() { s.hopT0 = performance.now() / 1000; },
    // pointAt(id | {x,y} | fn | null): he leans and stretches toward it
    pointAt(target) {
      if (!target) { s.lean = null; return; }
      if (typeof target === 'string') {
        const id = target;
        s.lean = () => (opts.pointOf ? opts.pointOf(id) : null);
      } else s.lean = target;
    },
  };
  requestAnimationFrame(frame);
  return orb;
}
