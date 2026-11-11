// 1guy home · gestures. Pointer events only (mouse, pen, touch), no libraries.
// tap = focus (bloom) · drag = a JUDGMENT, never a position (Nov 9): drop on a
// cluster = move it there (persisted), anything else = a session nudge that
// decays · fast release toward the right edge = FLICK into the rail ·
// empty space drag = pan (with a little momentum) ·
// ctrl/⌘-wheel or pinch = zoom; zooming out far enough steps up to the projects level.
// Phone: one finger pans; a quick swipe right on a card flicks it; long-press lifts it.
// LASSO (frontier 1): shift-drag on empty canvas (phone: long-press on empty canvas, then drag)
// draws a box; the cards fully or mostly inside it are the selection (core.js OneGuy.select).
// On a phone a pile's name row counts as empty canvas for this (there is little else).
// Shift-tap a card toggles it in or out (phone: a tap, while there is a selection); a tap on
// empty space or Esc clears it. A selection is a judgment only once he asks about it (talk.js).
import OneGuy, { clamp, store, REDUCED } from './core.js';
import * as time from './time.js';

// The lasso's whole rule, pure (tests/test_selection.py runs it under node): a card is in when at
// least `min` of its area is inside the box. box = two corners in any order; rects = [{id, left,
// top, right, bottom}] in the same coordinates. -> ids, in the order given.
export function boxHits(box, rects, min = 0.5) {
  const x0 = Math.min(box.x0, box.x1), x1 = Math.max(box.x0, box.x1);
  const y0 = Math.min(box.y0, box.y1), y1 = Math.max(box.y0, box.y1);
  const out = [];
  for (const r of rects || []) {
    const area = (r.right - r.left) * (r.bottom - r.top);
    if (!(area > 0)) continue;
    const w = Math.min(x1, r.right) - Math.max(x0, r.left), h = Math.min(y1, r.bottom) - Math.max(y0, r.top);
    if (w > 0 && h > 0 && (w * h) / area >= min) out.push(r.id);
  }
  return out;
}

const NO_DRAG = 'button, a, input, textarea, select, video, audio, label, [contenteditable], [data-nodrag]';

export function attachGestures({ stage, canvas, railW, isMobile }) {
  const view = canvas.view;
  const pointers = new Map();
  let g = null, mode = null, pinch = null, zTop = 10, inertia = 0, outPressure = 0, pressT = 0;

  const vel = () => {
    const s = g.samples;
    if (s.length < 2) return { x: 0, y: 0 };
    const a = s[0], b = s[s.length - 1];
    const dt = Math.max(8, b.t - a.t);
    return { x: (b.x - a.x) / dt, y: (b.y - a.y) / dt };
  };

  function zoomAt(f, sx, sy) {
    if (view.level !== 'blocks') return;
    const min = isMobile() ? 1 : 0.55, max = isMobile() ? 1 : 1.4;
    const z1 = clamp(view.z * f, min, max);
    if (f < 1 && view.z <= min + 0.001) {
      const now = performance.now();
      outPressure = now - pressT < 400 ? outPressure + (1 - f) : (1 - f);
      pressT = now;
      if (outPressure > 0.35) { outPressure = 0; canvas.setLevel('projects'); }
      return;
    }
    view.x = sx - (sx - view.x) * (z1 / view.z);
    view.y = sy - (sy - view.y) * (z1 / view.z);
    view.z = z1;
    canvas.clampView();
    canvas.applyView(false);
    clearTimeout(zoomAt.t);
    zoomAt.t = setTimeout(() => store.setView({ zoom: +view.z.toFixed(3) }), 500);
  }

  function beginDrag() {
    const node = canvas.els.get(g.card);
    const it = canvas.itemOf(g.card);
    if (!node || !it) { mode = 'pan'; return; }
    mode = 'drag';
    clearTimeout(g.lp);
    g.node = node;
    g.wx0 = it.x; g.wy0 = it.y;
    g.tilt = 0;
    node.classList.remove('glide');
    node.classList.add('og-lift');
    document.body.classList.add('og-dragging');
    node.style.zIndex = String(++zTop);
    if (g.touch && navigator.vibrate) navigator.vibrate(8);
  }

  // the lasso: a box in screen space over cards measured once (the desk holds still while he draws)
  function beginLasso(e) {
    mode = 'lasso';
    clearTimeout(g.lp);
    g.rects = [];
    for (const [id, node] of canvas.els) {
      if (!node.isConnected || node.classList.contains('og-gone')) continue;
      const r = node.getBoundingClientRect();
      g.rects.push({ id, node, left: r.left, top: r.top, right: r.right, bottom: r.bottom });
    }
    g.box = document.createElement('div');
    g.box.className = 'og-lasso';
    document.body.append(g.box);
    g.hits = [];
    document.body.classList.add('og-lassoing');
    if (g.touch && navigator.vibrate) navigator.vibrate(8);
    drawLasso(e || { clientX: g.x0, clientY: g.y0 });
  }
  function drawLasso(e) {
    const x = Math.min(g.x0, e.clientX), y = Math.min(g.y0, e.clientY);
    Object.assign(g.box.style, { left: x + 'px', top: y + 'px', width: Math.abs(e.clientX - g.x0) + 'px', height: Math.abs(e.clientY - g.y0) + 'px' });
    g.hits = boxHits({ x0: g.x0, y0: g.y0, x1: e.clientX, y1: e.clientY }, g.rects);
    for (const r of g.rects) r.node.classList.toggle('og-sel-pre', g.hits.includes(r.id));
  }
  function endLasso(keep) {
    for (const r of g.rects) r.node.classList.remove('og-sel-pre');
    g.box.remove();
    document.body.classList.remove('og-lassoing');
    if (!keep || !g.hits.length) return;      // a box round nothing changes nothing
    OneGuy.select.set(g.shift ? [...OneGuy.selected, ...g.hits] : g.hits);     // shift adds to what he has
  }

  const briefing = () => document.body.dataset.view === 'briefing';
  function onDown(e) {
    if (e.button > 0) return;
    if (canvas.bloom || briefing()) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    cancelAnimationFrame(inertia);
    if (pointers.size === 2 && view.level === 'blocks') {
      const [a, b] = [...pointers.values()];
      if (g && mode === 'drag' && g.node) settleBack();
      if (g && mode === 'lasso') endLasso(false);
      g = null; mode = null;
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), d0: Math.hypot(a.x - b.x, a.y - b.y) };
      return;
    }
    if (pointers.size > 1) return;
    if (view.level !== 'blocks') return;
    // a phone has little empty canvas: a finger on a pile's name row counts as empty too (a tap
    // there is still the label's own click; a long-press starts the box, a drag pans)
    const onLabel = e.pointerType === 'touch' && !!e.target.closest('.og-region-l');
    if (e.target.closest(NO_DRAG) && !onLabel) return;
    const card = e.target.closest('.og-card');
    g = {
      id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(),
      samples: [{ x: e.clientX, y: e.clientY, t: performance.now() }],
      card: card ? card.dataset.id : null, touch: e.pointerType === 'touch',
      vx0: view.x, vy0: view.y, shift: e.shiftKey && e.pointerType !== 'touch',
    };
    mode = 'maybe';
    if (g.touch) g.lp = setTimeout(() => { if (mode === 'maybe') { if (g.card) beginDrag(); else beginLasso(); } }, 380);
    if (!g.touch) e.preventDefault();
  }

  function onMove(e) {
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      zoomAt(d / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
      pinch.d = d;
      return;
    }
    if (!g || e.pointerId !== g.id) return;
    const now = performance.now();
    g.samples.push({ x: e.clientX, y: e.clientY, t: now });
    while (g.samples.length > 2 && now - g.samples[0].t > 100) g.samples.shift();
    const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
    if (mode === 'maybe') {
      if (Math.hypot(dx, dy) < (g.touch ? 9 : 5)) return;
      clearTimeout(g.lp);
      if (g.shift && !g.card) beginLasso(e);
      else if (g.card && !g.touch) beginDrag();
      else if (g.card && g.touch && dx > 0 && Math.abs(dx) > Math.abs(dy) * 1.4) {
        mode = 'swipe';
        g.node = canvas.els.get(g.card);
        g.base = canvas.baseTransform(g.card);
        if (g.node) g.node.classList.remove('glide');
      } else mode = 'pan';
    }
    if (mode === 'lasso') drawLasso(e);
    else if (mode === 'pan') {
      view.x = g.vx0 + dx;
      view.y = g.vy0 + dy;
      canvas.clampView();
      canvas.applyView(false);
    } else if (mode === 'drag') {
      const v = vel();
      g.tilt += (clamp(v.x * 6, -7, 7) - g.tilt) * 0.25;
      g.wx = g.wx0 + (dx - (view.x - g.vx0)) / view.z;     // the world may pan under a drag (time: edge scroll)
      g.wy = g.wy0 + (dy - (view.y - g.vy0)) / view.z;
      if (canvas.lens === 'time') time.hover(g.card, e, g.node, canvas);
      g.node.style.transform = `translate3d(${g.wx}px, ${g.wy}px, 0) rotate(${g.tilt.toFixed(2)}deg) scale(1.03)`;
      document.body.classList.toggle('og-rail-hover', !isMobile() && e.clientX > window.innerWidth - railW() - 24);
    } else if (mode === 'swipe' && g.node) {
      g.node.style.transform = `${g.base} translateX(${dx.toFixed(1)}px) rotate(${(dx * 0.03).toFixed(2)}deg)`;
      g.node.style.opacity = String(1 - clamp(dx / 600, 0, 0.4));
    }
  }

  function settleBack() {
    const node = g.node, id = g.card;
    node.classList.remove('og-lift');
    node.classList.add('glide');
    node.style.opacity = '';
    node.style.transform = canvas.baseTransform(id);
  }

  function onUp(e) {
    pointers.delete(e.pointerId);
    if (pinch && pointers.size < 2) { pinch = null; return; }
    if (!g || e.pointerId !== g.id) return;
    clearTimeout(g.lp);
    const v = vel();
    const id = g.card;
    document.body.classList.remove('og-rail-hover', 'og-dragging');
    if (mode === 'maybe' && e.type === 'pointerup') {
      // shift-tap (phone: any tap while cards are picked) takes a card in or out of the selection
      if (id && (g.shift || (g.touch && OneGuy.selected.length))) OneGuy.select.toggle(id);
      else if (id) OneGuy.focus(id);
      else OneGuy.select.clear();              // a tap on empty space lets the selection go
    } else if (mode === 'lasso') {
      endLasso(e.type === 'pointerup');
    } else if (mode === 'drag') {
      const vw = window.innerWidth;
      const overTime = canvas.lens === 'time' && time.over(e);
      const toRail = !isMobile() && !overTime && (e.clientX > vw - railW() - 8 || (e.clientX > vw - railW() - 140 && v.x > 0.25));
      const flick = v.x > (overTime ? 2.2 : 0.9) && v.x > Math.abs(v.y) * 1.1;   // over the week, a drop wins unless it's a real fling
      g.node.classList.remove('og-lift');
      if (e.type === 'pointerup' && (toRail || flick)) {
        canvas.flickToRail(id, v);
      } else {
        // the drop is a judgment, not a position: onto a cluster = move it there,
        // anywhere else = a session nudge (decays on reload, never persisted)
        const cid = canvas.clusterUnderPoint(e.clientX, e.clientY, g.node);
        const it = canvas.itemOf(id);
        if (canvas.lens === 'time' && time.drop(id, e, g.node, canvas)) {
          // a drop on time is a schedule (time.js): the card glides to its slot
        } else if (canvas.lens === 'time') {
          settleBack();
        } else if (cid && cid !== (it ? it.cluster : null)) {
          canvas.clusterMove(id, cid);
        } else {
          canvas.nudge(id, g.wx ?? g.wx0, g.wy ?? g.wy0);
        }
      }
    } else if (mode === 'swipe' && g.node) {
      const dx = e.clientX - g.x0;
      if (e.type === 'pointerup' && (dx > 110 || (dx > 40 && v.x > 0.55))) {
        g.node.style.opacity = '';
        canvas.flickToRail(id, v);
      } else settleBack();
    } else if (mode === 'pan' && !REDUCED) {
      // a little momentum, decaying
      let vx = v.x * 16, vy = v.y * 16;
      const step = () => {
        vx *= 0.92; vy *= 0.92;
        if (Math.hypot(vx, vy) < 0.4) return;
        view.x += vx; view.y += vy;
        canvas.clampView();
        canvas.applyView(false);
        inertia = requestAnimationFrame(step);
      };
      if (Math.hypot(vx, vy) > 2) inertia = requestAnimationFrame(step);
    }
    g = null;
    mode = null;
  }

  function onWheel(e) {
    if (canvas.bloom || briefing()) return;
    if (view.level === 'projects') {
      if (e.ctrlKey && e.deltaY < 0) {
        e.preventDefault();
        outPressure += -e.deltaY * 0.01;
        if (outPressure > 0.5) { outPressure = 0; canvas.setLevel('blocks'); }
      }
      return;
    }
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      zoomAt(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0035)), e.clientX, e.clientY);
      return;
    }
    const k = e.deltaMode === 1 ? 32 : e.deltaMode === 2 ? window.innerHeight : 1;
    view.x -= (e.shiftKey ? e.deltaY : e.deltaX) * k;
    view.y -= (e.shiftKey ? 0 : e.deltaY) * k;
    canvas.clampView();
    canvas.applyView(false);
  }

  // Esc lets the selection go before it means anything else (a typed line, a running turn and an
  // open card keep their own Esc)
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !OneGuy.selected.length || canvas.bloom || document.querySelector('.og-ask-scrim')) return;
    const a = document.activeElement;
    if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA') && a.value) return;
    const c = OneGuy.surface && OneGuy.surface.claim && OneGuy.surface.claim.now();
    if (c && !c.over && c.state === 'open' && c.ids.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    OneGuy.select.clear();
  }, true);
  stage.addEventListener('pointerdown', onDown);
  window.addEventListener('pointermove', onMove, { passive: true });
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
  stage.addEventListener('wheel', onWheel, { passive: false });
  // Safari trackpad pinch
  stage.addEventListener('gesturestart', (e) => e.preventDefault());
  stage.addEventListener('gesturechange', (e) => { e.preventDefault(); zoomAt(e.scale / (onWheel.s || 1), e.clientX, e.clientY); onWheel.s = e.scale; });
  stage.addEventListener('gestureend', (e) => { e.preventDefault(); onWheel.s = 1; });
  return { zoomAt };
}
