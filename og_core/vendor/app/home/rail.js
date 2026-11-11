// 1guy home · "put away" (the rail; WS-A1, renamed in v1.1 by WS-S). Right edge, 260px; a
// slim tab when there's nothing in it; a bottom drawer on phones. Sections in plain words:
// you put away (tag parked) · he didn't finish (didnt-land) · queued (queue). Dormant blocks
// no longer come here (they live in their project tile and the canvas); a chip explicitly
// added with tag 'dormant' shows under "resting". API names are unchanged for compatibility.
import { el, springEase, EASE_OUT, REDUCED } from './core.js';

const SECTIONS = [
  ['parked', 'you put away'],
  ['didnt-land', 'he didn’t finish'],
  ['queue', 'queued'],
  ['dormant', 'resting'],
];
const LABEL = Object.fromEntries(SECTIONS);
const DORMANT_SHOW = 7;
const STATUS_DOT = {
  'needs-you': 'amber', failed: 'red', active: 'teal', proposed: 'amber', waiting: 'grey',
  idle: 'grey', done: 'grey', parked: 'grey',
};

export function createRail(root, { isMobile, onSummon, onCollapse }) {
  const chips = new Map();          // id → chip data
  const managed = { parked: [], dormant: [] };
  let dormantOpen = false;
  let drawerOpen = false;
  let forced = false;          // The user opened the empty rail from its tab
  let collapsed = null;
  let focusId = null;          // the focused block: its chip (or a chip that belongs to it) wears the focus ring

  const head = el('div', 'og-rail-hd');
  const title = el('span', 'og-rail-title', 'put away');
  const count = el('span', 'og-rail-count', '0');
  const close = el('button', 'og-rail-close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', 'close');
  close.onclick = () => { if (isMobile()) setDrawer(false); else { forced = false; render(); } };
  head.append(title, count, close);
  const list = el('div', 'og-rail-list');
  const hint = el('div', 'og-rail-hint', 'put away, not deleted. tap one to pick it back up.');
  root.append(head, list, hint);
  // the slim tab (desktop, when collapsed): also the flick target
  const tab = el('button', 'og-rail-tab');
  tab.type = 'button';
  tab.setAttribute('aria-label', 'put away: things you flicked aside');
  tab.append(el('span', 'og-rail-tab-t', '→ put away'));
  tab.onclick = () => { forced = true; render(); };
  document.body.append(tab);

  const handle = el('button', 'og-rail-handle');
  handle.type = 'button';
  handle.setAttribute('aria-label', 'open put away');
  handle.onclick = () => setDrawer(!drawerOpen);
  document.body.append(handle);
  const scrim = el('div', 'og-rail-scrim');
  scrim.onclick = () => setDrawer(false);
  document.body.append(scrim);

  // drag the drawer header down to close it (phone)
  let dy0 = null;
  head.addEventListener('pointerdown', (e) => { if (isMobile()) { dy0 = e.clientY; head.setPointerCapture(e.pointerId); } });
  head.addEventListener('pointermove', (e) => {
    if (dy0 == null) return;
    const d = Math.max(0, e.clientY - dy0);
    root.style.transform = `translateY(${d}px)`;
  });
  head.addEventListener('pointerup', (e) => {
    if (dy0 == null) return;
    const d = e.clientY - dy0;
    dy0 = null;
    root.style.transform = '';
    if (d > 70) setDrawer(false);
  });

  function setDrawer(on) {
    drawerOpen = !!on && isMobile();
    document.body.classList.toggle('og-drawer-open', drawerOpen);
  }

  function all() {
    const out = [];
    for (const [tag] of SECTIONS) {
      const custom = [...chips.values()].filter((c) => c.tag === tag);
      const man = (managed[tag] || []).filter((c) => !chips.has(c.id));
      out.push([tag, [...custom, ...man]]);
    }
    return out;
  }

  function chipNode(c) {
    const b = el('button', 'og-chip');
    b.type = 'button';
    b.dataset.id = c.id;
    b.dataset.tag = c.tag;
    if (c.block) b.dataset.block = c.block;
    if (focusId && (c.id === focusId || c.block === focusId)) b.classList.add('og-focus');
    const dot = el('span', 'og-chip-dot ' + (STATUS_DOT[c.status] || (c.tag === 'didnt-land' ? 'amber' : c.tag === 'queue' ? 'teal' : 'grey')));
    const lbl = el('span', 'og-chip-l', c.label || c.id);
    b.title = (LABEL[c.tag] || c.tag) + ' · ' + (c.label || c.id);
    b.append(dot, lbl);
    if (c.glyph) b.prepend(el('span', 'og-chip-g', c.glyph));
    b.onclick = (e) => { e.stopPropagation(); summon(c); };
    return b;
  }

  function render() {
    const prev = new Map([...list.querySelectorAll('.og-chip')].map((n) => [n.dataset.id, n]));
    list.replaceChildren();
    let total = 0;
    for (const [tag, label] of SECTIONS) {
      const items = all().find(([t]) => t === tag)[1];
      if (!items.length) continue;
      total += items.length;
      const sec = el('section', 'og-rail-sec');
      sec.dataset.tag = tag;
      const h = el('div', 'og-rail-sh');
      h.append(el('span', '', label), el('span', 'og-rail-n', String(items.length)));
      sec.append(h);
      const box = el('div', 'og-rail-chips');
      let shown = items;
      if (tag === 'dormant' && !dormantOpen && items.length > DORMANT_SHOW) shown = items.slice(0, DORMANT_SHOW);
      for (const c of shown) box.append(prev.get(c.id) && prev.get(c.id).dataset.tag === c.tag ? prev.get(c.id) : chipNode(c));
      if (tag === 'dormant' && items.length > DORMANT_SHOW) {
        const more = el('button', 'og-rail-more', dormantOpen ? 'fewer' : `+${items.length - DORMANT_SHOW} more`);
        more.type = 'button';
        more.onclick = (e) => { e.stopPropagation(); dormantOpen = !dormantOpen; render(); };
        box.append(more);
      }
      sec.append(box);
      list.append(sec);
    }
    if (!total) list.append(el('div', 'og-rail-empty', 'nothing put away. flick a card to the right and it waits here.'));
    for (const n of list.querySelectorAll('.og-chip')) n.classList.toggle('og-focus', !!focusId && (n.dataset.id === focusId || n.dataset.block === focusId));
    count.textContent = String(total);
    const hot = all().filter(([t]) => t === 'didnt-land' || t === 'queue').reduce((n, [, xs]) => n + xs.length, 0);
    handle.innerHTML = '';
    handle.append(el('span', 'og-rail-handle-bar'), el('span', 'og-rail-handle-t', 'put away'), el('span', 'og-rail-handle-n' + (hot ? ' hot' : ''), String(total)));
    document.body.classList.toggle('og-rail-has', total > 0);
    const c = total === 0 && !forced;
    document.body.classList.toggle('og-rail-forced', forced);
    if (c !== collapsed) {
      collapsed = c;
      document.body.classList.toggle('og-rail-collapsed', c);
      if (onCollapse) onCollapse(c);
    }
  }

  async function summon(c) {
    const node = list.querySelector(`.og-chip[data-id="${CSS.escape(c.id)}"]`);
    const rect = node ? node.getBoundingClientRect() : null;
    if (node && !REDUCED) {
      await node.animate([{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(-46px)', opacity: 0 }],
        { duration: 300, easing: EASE_OUT, fill: 'forwards' }).finished.catch(() => {});
    }
    if (chips.has(c.id)) chips.delete(c.id);
    if (isMobile()) setDrawer(false);
    try {
      if (c.onSummon) await c.onSummon(c, rect);
      else await onSummon(c.id, rect);
    } catch (err) { console.warn('[rail] summon failed', err); }
    render();
  }

  function pop(id) {
    const n = list.querySelector(`.og-chip[data-id="${CSS.escape(id)}"]`);
    if (!n || REDUCED) return;
    n.animate([{ transform: 'scale(.6)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }],
      { duration: 520, easing: springEase(8, 11) });
  }

  const api = {
    root, handle,
    addChip(c) {
      if (!c || !c.id) return;
      chips.set(c.id, { ...c, tag: SECTIONS.some(([t]) => t === c.tag) ? c.tag : 'parked' });
      render();
      if (!c.silent) pop(c.id);
    },
    removeChip(id) {
      const had = chips.delete(id);
      const n = list.querySelector(`.og-chip[data-id="${CSS.escape(id)}"]`);
      if (n && had && !REDUCED) {
        n.animate([{ transform: 'none', opacity: 1 }, { transform: 'translateX(-46px)', opacity: 0 }],
          { duration: 300, easing: EASE_OUT, fill: 'forwards' }).onfinish = render;
      } else render();
    },
    has: (id) => chips.has(id) || managed.parked.some((c) => c.id === id) || managed.dormant.some((c) => c.id === id),
    setManaged(tag, items) { managed[tag] = items; render(); },
    pop,
    // where a flicked card should land (screen coords)
    slotPoint(tag = 'parked') {
      if (isMobile()) {
        const r = handle.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width };
      }
      const sec = list.querySelector(`.og-rail-sec[data-tag="${tag}"] .og-rail-chips`);
      const lr = list.getBoundingClientRect();
      if (sec) {
        const r = sec.getBoundingClientRect();
        const lastChip = sec.lastElementChild ? sec.lastElementChild.getBoundingClientRect() : null;
        const y = lastChip ? lastChip.bottom + 6 + 17 : r.top + 17;
        return { x: r.left + 106, y: Math.min(y, window.innerHeight - 40), w: 212 };
      }
      return { x: lr.left + 24 + 106, y: lr.top + 40, w: 212 };
    },
    chipRect(id) {
      const n = list.querySelector(`.og-chip[data-id="${CSS.escape(id)}"]`);
      return n ? n.getBoundingClientRect() : null;
    },
    setDrawer,
    setFocus(id) {
      focusId = id || null;
      for (const n of list.querySelectorAll('.og-chip')) {
        n.classList.toggle('og-focus', !!focusId && (n.dataset.id === focusId || n.dataset.block === focusId));
      }
    },
    get open() { return drawerOpen; },
    get collapsed() { return !!collapsed; },
    get total() { return all().reduce((n, [, xs]) => n + xs.length, 0); },
    tab,
    render,
  };
  render();
  return api;
}
