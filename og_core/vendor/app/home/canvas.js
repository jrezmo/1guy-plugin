// 1guy home · the canvas (WS-A1, relational v2). The freeform work pane:
// attention decides space, the SYSTEM decides layout. The user's drags are judgments, not
// pixels: drop on a cluster = move it there, flick = park, anything else = a session
// nudge that decays. Stored {x,y} placements are legacy and no longer honored —
// "tidy the table" clears them (docs/adaptive-surfaces.md REFINEMENT 3).
import OneGuy, { el, clamp, lerp, hash, springEase, EASE_OUT, EASE_IN_OUT, REDUCED, store, sleep, headlineOf, words, flyOff, SENT } from './core.js';
import { compose, faceVerb } from './focus.js';
import * as time from './time.js';
import { worldMark } from './live.js';

const SPAN = { hero: 4, card: 2, compact: 2, fold: 2 };
const TIER_RANK = { hero: 0, card: 1, compact: 2, fold: 3 };
const HOT = new Set(['needs-you', 'failed']);
const outCubic = (p) => 1 - Math.pow(1 - p, 3);
const inOutCubic = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const inOutSine = (p) => -(Math.cos(Math.PI * p) - 1) / 2;
const seg = (t, a, b) => clamp((t - a) / (b - a), 0, 1);

export const score = (b) => (b.attention || 0) + (HOT.has(b.status) ? 0.08 : 0) + (b.status === 'proposed' ? 0.02 : 0);
const isDormant = (b) => !HOT.has(b.status) && (b.status === 'done' || b.status === 'parked' || (b.kind === 'job' && b.status === 'idle'));

export function createCanvas(ctx) {
  const { stage, world, cardsLayer, regionsLayer, projectsEl, tendrils, backdrop, rail, orb, isMobile, railW, topInset } = ctx;
  const els = new Map();           // block id → card element
  const sig = new Map();           // block id → render signature
  // one layout memory per lens, so a trip through Time never reshuffles the Board
  const newMemo = () => ({ tier: new Map(), order: new Map(), clusters: [], pos: new Map(), packW: 0 });
  const memos = { board: newMemo() };
  let memo = memos.board;
  let lens = 'board';               // 'board' | 'time' | 'inbox' — same nodes, three layouts
  const expanded = new Set(store.data.expanded || []);
  const nudges = new Map();       // block id → {x, y} — session-only offsets, decay on reload
  let briefNew = null;            // the brief written this visit (turnIntoBrief): shown, never folded away
  const pv = new Map();          // block id → pile id — a proposal's moves, shown before yes (preview)
  const view = { x: 32, y: 80, z: 1, level: 'blocks' };
  let L = { items: new Map(), clusters: [], bounds: { w: 0, h: 0 } };
  let focusId = null, bloom = null, entering = false;
  const g = () => (isMobile()
    ? { u: Math.max(240, window.innerWidth - 16 * 2 - 12 * 2), gap: 12, pad: 12, labelH: 38, cgap: 18, margin: 16 }
    : { u: 120, gap: 12, pad: 14, labelH: 36, cgap: 22, margin: 24 });

  // ------------------------------------------------------------ view transform
  function applyView(animate) {
    world.style.transition = animate ? `transform 620ms ${EASE_IN_OUT}` : 'none';
    world.style.transform = `translate3d(${view.x.toFixed(1)}px, ${view.y.toFixed(1)}px, 0) scale(${view.z.toFixed(4)})`;
    stage.style.backgroundPosition = `${(view.x % (32 * view.z)).toFixed(1)}px ${(view.y % (32 * view.z)).toFixed(1)}px`;
    stage.style.backgroundSize = `${(32 * view.z).toFixed(2)}px ${(32 * view.z).toFixed(2)}px`;
    if (bloom) drawTendrils(false);
  }
  function clampView() {
    const vw = window.innerWidth - railW(), vh = window.innerHeight;
    const w = L.bounds.w * view.z, h = L.bounds.h * view.z;
    const keep = 140;
    view.x = clamp(view.x, Math.min(g().margin, vw - w - g().margin) - (isMobile() ? 0 : keep), isMobile() ? g().margin : vw - keep);
    view.y = clamp(view.y, Math.min(topInset(), vh - h - 120), topInset() + (isMobile() ? 40 : vh * 0.5));
  }
  const toWorld = (sx, sy) => ({ x: (sx - view.x) / view.z, y: (sy - view.y) / view.z });
  // The stage is overflow:hidden but still scrollable from code: a browser bringing a card into view (Tab onto
  // it, find-in-page, a test's click on an off-screen card) scrolls the stage natively and the world slides
  // out from under the canvas's own pan (QA: Time lens at 390, 'no card to focus'). Fold any native scroll
  // back into the view: the card stays where the browser put it, and the pan math stays true.
  stage.addEventListener('scroll', () => {
    const sx = stage.scrollLeft, sy = stage.scrollTop;
    if (!sx && !sy) return;
    stage.scrollLeft = 0; stage.scrollTop = 0;
    view.x -= sx; view.y -= sy;
    applyView(false);
  }, { passive: true });

  // ------------------------------------------------------------ classify + tiers
  function clusterMeta(board) {
    const projById = Object.fromEntries((board.projects || []).map((p) => [p.id, p]));
    const projBlock = {};
    const members = {};
    for (const b of board.blocks) {
      if (b.kind === 'project') projBlock[b.id] = b;
      else if (b.project) members[b.project] = (members[b.project] || 0) + 1;
    }
    const clusterOf = (b) => {
    // The user's cluster judgments (drop on a pile) outrank the system's default assignment
    // (a preview is the same thing held in the air: pv outranks the store and is never saved)
    const ov = pv.has(b.id) ? { cluster: pv.get(b.id) } : store.get(b.id);
    if (ov && ov.cluster && ov.cluster !== b.id) return ov.cluster;
    if (b.kind === 'project') return members[b.id] ? null : 'cluster:quiet';       // projects ARE their cluster label
      if (time.isEvent(b)) return 'cluster:week';     // time is a place: never a project grid (card-grammar §6)
      if (b.project && (projById[b.project] || projBlock[b.project])) return b.project;
      if (b.detail && b.detail.pile) return b.detail.pile;     // a brief lives in the pile it was written from
      if (b.kind === 'job') return 'cluster:routines';
      if (b.kind === 'goal') return 'cluster:goals';
      if (b.kind === 'widget') return 'cluster:housekeeping';
      return 'cluster:loose';
    };
    const info = (cid) => {
      if (cid === 'cluster:quiet') return { title: 'quiet projects', color: '#8b8d94' };
      if (cid === 'cluster:routines') return { title: 'routines', color: '#8b8d94' };
      if (cid === 'cluster:goals') return { title: 'goals', color: '#a9aab0' };
      if (cid === 'cluster:housekeeping') return { title: 'housekeeping', color: '#8b93a7' };
      if (cid === 'cluster:loose') return { title: 'loose ends', color: '#8b8d94' };
      if (cid === 'cluster:week') return { title: 'on the calendar', color: '#8b93a7', lensTo: 'time' };
      const p = projById[cid] || {}, pb = projBlock[cid] || {};
      // a pile he or the user made that isn't a project: its id is its name (pile:friday-digest → "friday digest")
      return { title: p.title || pb.title || (/^pile:/.test(cid) ? cid.slice(5).replace(/[-_.]+/g, ' ') : cid.replace(/^project:/, '')), color: p.color || '#8b8d94', block: projBlock[cid] || null };
    };
    return { clusterOf, info, projBlock };
  }

  // ------------------------------------------------------------ lenses: same nodes, other layouts
  // A lens is only a different plan: which blocks show, how they group, how the groups pack.
  // The card elements are the same ones, so switching lenses is travel (setLens), never a re-render.
  //   time  — a week strip, laid out by time.js (the Time lens owns days, hours and drops)
  //   inbox — what's waiting on the user (block.inbox), grouped by why, decisions as buttons on the face
  const INBOX_GROUPS = [['decide', 'waiting on a yes or no'], ['broke', 'broke'], ['idea', 'ideas from me']];
  function lensPlan(board) {
    const meta = clusterMeta(board);
    const mobile = isMobile();
    const parked = [], live = [], tier = new Map(), groups = new Map();
    const put = (cid, b) => { if (!groups.has(cid)) groups.set(cid, []); groups.get(cid).push(b); };
    for (const b of board.blocks) {
      const p = store.get(b.id);
      if (p && p.parked) parked.push(b); else live.push(b);
    }
    let defs = [], timePack = 'row';
    if (lens === 'time') {
      ({ defs, pack: timePack } = time.plan(live, { put, groups, tier, mobile, room: window.innerWidth - railW() - g().margin * 2 }));
    } else {
      for (const b of live) if (b.inbox && b.status !== 'done') put('inbox:' + (b.inbox.group || 'decide'), b);
      for (const [k, title] of INBOX_GROUPS) {
        const xs = groups.get('inbox:' + k);
        if (!xs) continue;
        xs.sort((a, b) => score(b) - score(a));
        for (const b of xs) tier.set(b.id, 'hero');   // wide faces: the decision has room to be buttons
        defs.push({ id: 'inbox:' + k, title, color: k === 'decide' ? '#ffb547' : '#8b8d94' });
      }
    }
    const clusters = defs.map((d) => {
      const open = expanded.has(d.id);
      const folded = d.folded || [];
      const shown = [...(open ? folded : []), ...(groups.get(d.id) || [])];
      return { ...d, shown, folded, open, all: [...new Set([...folded, ...shown])], lens };
    });
    return { clusters, tier, parked, dormant: [], placed: [], meta, ranked: live, pack: lens === 'time' ? timePack : 'col' };
  }
  // the lens's own mark on a face: when it happens (time). The verb comes from the grammar (focus.js).
  function lensMark(b, node) { if (lens === 'time') time.mark(b, node); }

  function plan(board) {
    if (lens !== 'board') return lensPlan(board);
    const mobile = isMobile();
    const meta = clusterMeta(board);
    const FULL = mobile ? 7 : 10, HERO = mobile ? 1 : 2, COMPACT = mobile ? 10 : 14;
    const parked = [], dormant = [], placed = [], flow = [];
    for (const b of board.blocks) {
      const cid = meta.clusterOf(b);
      if (!cid) continue;
      const p = store.get(b.id);
      if (p && p.parked) { parked.push(b); continue; }
      // legacy {x,y} placements no longer pull cards out of the system layout (Nov 9)
      if (isDormant(b)) dormant.push(b);      // v1.1: dormant folds inside its cluster (not the rail)
      flow.push(b);
    }
    // fresh tiers by score; memoised tiers keep stable blocks from jumping between polls
    const ranked = [...flow].sort((a, b) => score(b) - score(a));
    const fresh = new Map();
    ranked.forEach((b, i) => fresh.set(b.id, i < HERO ? 'hero' : i < FULL ? 'card' : i < FULL + COMPACT ? 'compact' : 'fold'));
    const tier = new Map();
    for (const b of ranked) {
      let t = memo.tier.get(b.id) || fresh.get(b.id);
      if (HOT.has(b.status) && TIER_RANK[t] > TIER_RANK.card) t = 'card';     // needs-you floats up
      if (isDormant(b)) t = 'fold';
      if ((pv.has(b.id) || b.id === briefNew) && t === 'fold') t = 'compact';     // what a preview moves is never hidden in a fold; nor the brief he just asked for
      // an idea about the board itself has to be ON the board to be previewed there
      if (b.kind === 'proposal' && b.detail && b.detail.edits && TIER_RANK[t] > TIER_RANK.card) t = 'card';
      if (mobile && t === 'hero' && fresh.get(b.id) !== 'hero') t = 'card';
      tier.set(b.id, t);
    }
    for (const b of [...placed, ...parked]) if (!tier.has(b.id)) tier.set(b.id, memo.tier.get(b.id) || 'card');
    // clusters, in memoised order (new clusters join by loudness)
    const byCluster = new Map();
    for (const b of ranked) {
      const cid = meta.clusterOf(b);
      if (!byCluster.has(cid)) byCluster.set(cid, []);
      byCluster.get(cid).push(b);
    }
    const loud = (cid) => {
      const xs = byCluster.get(cid) || [];
      return Math.max(0, ...xs.map(score)) + 0.04 * xs.filter((b) => HOT.has(b.status)).length - (cid.startsWith('cluster:') ? 0.05 : 0);
    };
    const known = memo.clusters.filter((c) => byCluster.has(c));
    const fresher = [...byCluster.keys()].filter((c) => !known.includes(c)).sort((a, b) => loud(b) - loud(a));
    const order = memo.clusters.length ? [...known, ...fresher] : fresher;
    const clusters = order.map((cid) => {
      const xs = byCluster.get(cid).sort((a, b) =>
        TIER_RANK[tier.get(a.id)] - TIER_RANK[tier.get(b.id)] ||
        (memo.order.get(a.id) ?? 1e9) - (memo.order.get(b.id) ?? 1e9) ||
        score(b) - score(a));
      const open = expanded.has(cid);
      // a long pile folds its quiet tail, so the next pile is a thumb away (18 chats ran 2+ phone screens).
      // Never what's waiting on him, live, or in focus; not remembered in memo.tier, so a wider window unfolds it.
      const over = new Set(), cap = mobile ? 4 : 8, keep = OneGuy.focused && OneGuy.focused.id;
      let seen = 0;
      for (const b of xs) {
        if (tier.get(b.id) === 'fold') continue;
        if (++seen > cap && !HOT.has(b.status) && b.status !== 'active' && b.id !== keep && b.id !== briefNew) over.add(b.id);
      }
      const isFold = (b) => tier.get(b.id) === 'fold' || over.has(b.id);
      const shown = xs.filter((b) => !isFold(b) || open);
      const folded = xs.filter(isFold);
      const all = board.blocks.filter((b) => meta.clusterOf(b) === cid || b.project === cid);
      return { id: cid, ...meta.info(cid), shown, folded, open, all };
    });
    return { clusters, tier, parked, dormant, placed, meta, ranked };
  }

  // ------------------------------------------------------------ card elements
  function signature(b) {
    const h = b.headline || {};
    return JSON.stringify([lens, b.title, b.summary, b.status, b.due, b.priority, b.updated, b.detail && b.detail.last_status, (b.actions || []).length, h.title, h.why, h.hash,
      lens === 'time' && b.when, lens === 'inbox' && b.inbox, b.world && b.world.line, b.changed && b.changed.what, lens === 'time' && Math.floor(Date.now() / 600000)]);
  }
  // The face sees the headline layer: plain title + why, no #tags. block.headline is passed
  // through untouched, so K's faces can read it directly; older faces get the plain words.
  function faceBlock(b) {
    const h = headlineOf(b);
    return { ...b, title: h.title || b.title, summary: h.why || '', tags: [], raw_title: b.title };
  }
  function fallbackFace(b, node) {
    const f = el('div', 'kf og-kf');
    f.append(el('div', 'kf-title', b.title || b.id), el('div', 'kf-line', b.summary || ''));
    node.replaceChildren(f);
  }
  function renderFace(b, node) {
    const k = OneGuy.kinds[b.kind];
    node.dataset.view = 'canvas';
    try {
      if (k && k.card) k.card(faceBlock(b), node);
      else fallbackFace(faceBlock(b), node);
    } catch (err) {
      console.warn('[home] card failed', b.id, err);
      fallbackFace(b, node);
    }
    try { lensMark(b, node); worldMark(b, node); faceVerb(b, node); } catch (err) { console.warn('[home] face marks failed', b.id, err); }
    node.classList.toggle('og-hot', HOT.has(b.status));
    node.classList.toggle('og-quiet', ['idle', 'done', 'waiting', 'parked'].includes(b.status) && !['conflict', 'waiting-on-you'].includes(b.world && b.world.kind));
    node.setAttribute('aria-label', headlineOf(b).title || b.title);
    sig.set(b.id, signature(b));
  }
  function ensureCard(b, size) {
    let node = els.get(b.id);
    const fresh = !node;
    if (!node) {
      node = el('div', 'og-card');
      node.dataset.id = b.id;
      node.tabIndex = 0;
      node.setAttribute('role', 'button');
      node.dataset.size = size;
      cardsLayer.append(node);
      els.set(b.id, node);
      renderFace(b, node);
    } else if (sig.get(b.id) !== signature(b)) {
      node.dataset.size = size;
      renderFace(b, node);
    }
    node.dataset.size = size;
    return { node, fresh };
  }
  function widthFor(size, G) {
    if (isMobile()) return G.u;
    const s = SPAN[size] || 2;
    return s * G.u + (s - 1) * G.gap;
  }

  // ------------------------------------------------------------ layout
  function layout(board, { animate = false } = {}) {
    const G = g();
    const mobile = isMobile();
    const P = plan(board);
    const want = new Map();     // id → size
    for (const c of P.clusters) for (const b of c.shown) want.set(b.id, P.tier.get(b.id) === 'fold' ? 'compact' : P.tier.get(b.id));
    const born = [];
    // the week fits the room: a day is as wide as the room allows (empty days stay slim)
    let dayW = 0;
    if (P.pack === 'row' && !mobile) {
      const n = P.clusters.length, empties = P.clusters.filter((c) => !c.shown.length && !c.folded.length).length;
      const room = window.innerWidth - railW() - G.margin * 2 - (n - 1) * 10 - empties * 80;
      dayW = clamp(Math.floor(room / Math.max(1, n - empties)) - G.pad * 2, 148, 2 * G.u + G.gap);
    }
    const cardW = (size) => dayW || widthFor(size, G);
    for (const [id, size] of want) {
      const b = board.byId[id];
      const { node, fresh } = ensureCard(b, size);
      node.style.width = cardW(size) + 'px';
      if (fresh) born.push(id);
    }
    // retire cards that left the canvas
    for (const [id, node] of els) {
      if (want.has(id)) continue;
      els.delete(id);
      sig.delete(id);
      if (id === focusId || REDUCED || node.classList.contains('og-gone')) node.remove();
      else {
        node.style.pointerEvents = 'none';
        node.animate([{ opacity: 1 }, { opacity: 0, transform: node.style.transform + ' scale(.92)' }], { duration: 320, easing: EASE_OUT, fill: 'forwards' }).onfinish = () => node.remove();
      }
    }
    // measure (one read pass)
    const H = new Map();
    for (const id of want.keys()) H.set(id, els.get(id).offsetHeight || 80);
    const foldH = mobile ? 52 : 44;

    // inner masonry per cluster
    const items = new Map();
    const boxes = [];
    const roomW = window.innerWidth - railW() - G.margin * 2;
    for (const c of P.clusters) {
      const list = c.shown.map((b) => ({ id: b.id, span: mobile || dayW ? 1 : SPAN[want.get(b.id)], h: H.get(b.id) }));
      if (c.folded.length) list[P.pack === 'row' || c.lens === 'time' ? 'unshift' : 'push']({ id: 'fold:' + c.id, span: mobile || dayW ? 1 : 2, h: foldH, fold: true });
      if (!list.length && !c.keep) continue;
      // clusters are one or two card-columns wide (three on very wide screens), so two sit side by side
      let K = 1;
      if (!mobile) {
        const maxS = Math.max(2, ...list.map((x) => x.span));
        const wide = roomW >= 1650;
        K = list.length >= 3 || maxS >= 4 ? 4 : 2;
        if (wide && list.length >= 8) K = 6;
        if (P.pack === 'row') K = 1;                                   // a day is one card wide
        if (P.pack === 'col') K = clamp(2 * Math.floor((roomW - G.pad * 2 + G.gap) / (2 * (G.u + G.gap))), 2, 8);   // a section spans the page
      }
      // the "now" line sits between what happened today and what's still ahead
      if (c.nowAt != null && list.length) list.splice(c.nowAt + (c.folded.length ? 1 : 0) + (c.open ? c.folded.length : 0), 0, { id: 'now:' + c.id, span: K, h: 14, fold: true, now: true });
      const sky = new Array(K).fill(0);
      const pos = [];
      for (const it of list) {
        let best = 0, by = Infinity;
        for (let s = 0; s + it.span <= K; s++) {
          const y = Math.max(...sky.slice(s, s + it.span));
          if (y < by - 0.5) { by = y; best = s; }
        }
        pos.push({ ...it, cx: best * ((dayW || G.u) + G.gap), cy: by });
        for (let s = best; s < best + it.span; s++) sky[s] = by + it.h + G.gap;
      }
      const empty = !list.length;
      const innerW = empty && !mobile && P.pack === 'row' ? 80 - G.pad * 2 : dayW || K * G.u + (K - 1) * G.gap;   // a stacked week keeps empty days full width
      const innerH = empty ? (mobile ? 0 : 56) : Math.max(...sky) - G.gap;
      boxes.push({ c: { ...c, empty }, pos, w: innerW + G.pad * 2, h: G.labelH + innerH + (empty && mobile ? 2 : G.pad) });
    }
    // pack clusters: each keeps its previous spot unless a cluster placed before it now overlaps
    // it (so a poll never shuffles stable blocks); otherwise the lowest free spot, leftmost
    const availW = Math.max(...boxes.map((b) => b.w), 300, mobile ? window.innerWidth - G.margin * 2 : window.innerWidth - railW() - G.margin * 2);
    if (Math.abs(memo.packW - availW) > 1) memo.pos = new Map();
    memo.packW = availW;
    const COL = 8;
    const placedR = [];
    const hits = (x, y, w, h) => placedR.some((r) => x < r.x + r.w + G.cgap && x + w + G.cgap > r.x && y < r.y + r.h + G.cgap && y + h + G.cgap > r.y);
    let maxX = 0, maxY = 0;
    const clusters = [];
    let cursor = 0;
    for (const bx of boxes) {
      let x = 0, y = 0;
      const prev = memo.pos.get(bx.c.id);
      if (P.pack) {
        // a lens packs in reading order: days left to right (phone: top to bottom), sections down the page
        const down = P.pack === 'col' || mobile;
        if (down) { y = cursor; cursor += bx.h + (bx.c.empty ? 8 : G.cgap); if (mobile) bx.w = availW; }
        else { x = cursor; cursor += bx.w + 10; }
      } else if (prev && prev.x + bx.w <= availW + 1 && !hits(prev.x, prev.y, bx.w, bx.h)) ({ x, y } = prev);
      else {
        let best = null;
        for (let cx = 0; cx + bx.w <= Math.max(bx.w, availW); cx += COL) {
          let cy = 0;
          for (const r of placedR) if (cx < r.x + r.w + G.cgap && cx + bx.w + G.cgap > r.x) cy = Math.max(cy, r.y + r.h + G.cgap);
          if (!best || cy < best.y - 1) best = { x: cx, y: cy };
        }
        if (best) ({ x, y } = best);
      }
      placedR.push({ x, y, w: bx.w, h: bx.h });
      memo.pos.set(bx.c.id, { x, y });
      const cl = { ...bx.c, x, y, w: bx.w, h: bx.h };
      clusters.push(cl);
      for (const it of bx.pos) {
        const ix = x + G.pad + it.cx, iy = y + G.labelH + it.cy;
        const size = it.fold ? 'fold' : want.get(it.id);
        const rot = mobile || size === 'compact' || it.fold || P.pack ? 0 : (hash(it.id) - 0.5) * 1.6;
        items.set(it.id, { x: ix, y: iy, w: mobile ? G.u : it.now ? bx.w - G.pad * 2 : dayW || SPAN[size] * G.u + (SPAN[size] - 1) * G.gap, h: it.h, size, rot, cluster: cl.id, fold: !!it.fold });
      }
      maxX = Math.max(maxX, x + bx.w);
      maxY = Math.max(maxY, y + bx.h);
    }
    for (const b of P.placed) {}   // legacy freeform placements: nothing to render
    // remember tiers + order so the next poll doesn't reshuffle
    memo.tier = new Map(P.tier);
    memo.order = new Map(P.clusters.flatMap((c) => [...c.shown, ...c.folded]).map((b, i) => [b.id, i]));
    memo.clusters = P.clusters.map((c) => c.id);
    L = { items, clusters, bounds: { w: maxX, h: maxY }, plan: P };

    // The user's nudges (session-only, decay on reload) ride on top of the system position
    for (const [id, n] of nudges) {
      const it = items.get(id);
      if (it) { it.x = n.x; it.y = n.y; it.nudged = true; }
    }
    renderRegions(clusters, G);
    // an honest empty state: a lens with nothing in it says so in plain words (a sparse Hermes home,
    // or simply nothing waiting) instead of showing a blank desk
    {
      const none = lens !== 'time' && ![...items.values()].some((it) => !it.fold);
      let empty = stage.querySelector(':scope > .og-empty');
      if (none) {
        if (!empty) { empty = el('div', 'og-empty'); stage.append(empty); }
        empty.textContent = lens === 'inbox' ? 'nothing is waiting on you.' : 'nothing on the board yet. tell me what you’re in the middle of.';
      } else if (empty) empty.remove();
    }
    for (const [id, it] of items) {
      if (it.fold) continue;
      const node = els.get(id);
      node.style.setProperty('--rot', it.rot.toFixed(2) + 'deg');
      node.classList.toggle('og-nudged', !!it.nudged);
      node.classList.toggle('og-sel', OneGuy.selected.includes(id));
      node.classList.toggle('glide', animate && !born.includes(id));
      node.style.transform = `translate3d(${it.x}px, ${it.y}px, 0) rotate(var(--rot))`;
      node.dataset.cluster = it.cluster || '';
    }
    // rail: parked (the user) + dormant (quiet by status)
    const chip = (b, tag) => ({ id: b.id, label: headlineOf(b).title || b.title, status: b.status, kind: b.kind, tag, glyph: (OneGuy.kinds[b.kind] || {}).icon });
    rail.setManaged('parked', P.parked.map((b) => chip(b, 'parked')));
    rail.setManaged('dormant', []);
    if (view.level === 'projects') renderProjects();
    return { born, plan: P };
  }

  function renderRegions(clusters, G) {
    const prev = new Map([...regionsLayer.children].map((n) => [n.dataset.cluster, n]));
    const keep = new Set();
    // the briefing's own cards (rail + the broke line), by id — the same list the header counts
    const sh = OneGuy.briefing(OneGuy.board);
    const fyIds = new Set([...sh.forYou, ...sh.broke].map((b) => b.id));
    for (const c of clusters) {
      let r = prev.get(c.id);
      if (!r) {
        r = el('div', 'og-region');
        r.dataset.cluster = c.id;
        r.append(el('button', 'og-region-l'));
        regionsLayer.append(r);
        if (prev.size && !REDUCED) r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 520, delay: 160, easing: EASE_OUT, fill: 'backwards' });
      }
      keep.add(c.id);
      r.style.setProperty('--pc', c.color);
      r.style.transform = `translate3d(${c.x}px, ${c.y}px, 0)`;
      r.style.width = c.w + 'px';
      r.style.height = c.h + 'px';
      r.classList.toggle('og-region-system', c.id.startsWith('cluster:'));
      r.classList.toggle('og-region-lens', !!c.lens);
      r.classList.toggle('og-region-today', !!c.today);
      r.classList.toggle('og-region-empty', !!c.empty);
      r.dataset.rlens = c.lens || '';
      const lab = r.querySelector('.og-region-l');
      lab.type = 'button';
      lab.dataset.focus = c.block ? c.block.id : '';
      if (c.lensTo && !c.lens) lab.dataset.toLens = c.lensTo; else delete lab.dataset.toLens;   // never data-lens: only the tabs carry that
      // 'N for you' on a pile = how many of the briefing's cards live here, so piles add up to the header
      const hot = c.lens ? 0 : c.all.filter((b) => fyIds.has(b.id)).length;
      lab.replaceChildren(el('i', 'og-region-dot'), el('span', 'og-region-t', c.title.replace(/\s*\([^)]*\)\s*$/, '')));
      if (hot) lab.append(el('span', 'og-region-hot', `${hot} for you`));
      lab.append(el('span', 'og-region-n', c.lens ? (c.sub || (c.lens === 'inbox' ? String(c.all.length) : '')) : String(counts(c).total || '')));
      lab.title = c.lensTo ? `see the week` : c.block ? `open ${c.title}` : c.title;
      // a pile's own one-row menu: turn into a brief (frontier 6). Board piles of two or more cards.
      let bb = r.querySelector('.og-region-brief');
      const pileIds = c.lens || c.id === 'cluster:week' ? [] : c.all.filter((b) => b.kind !== 'project' && !(b.detail && b.detail.kind === 'brief')).map((b) => b.id);
      if (pileIds.length >= 2) {
        if (!bb) {
          bb = el('button', 'og-region-brief', 'turn into a brief');
          bb.type = 'button';
          bb.title = 'write one short brief of this pile (it lands here, linked to these cards)';
          r.append(bb);
        }
        const name = c.title.replace(/\s*\([^)]*\)\s*$/, '');
        const short = c.w < 420 || isMobile();        // a narrow pile: one word, so it never runs into the pile's name
        bb.textContent = short ? 'brief' : 'turn into a brief';
        r.classList.toggle('og-region-brief-s', short);
        bb.onclick = (e) => { e.stopPropagation(); turnIntoBrief(pileIds, c.id, name); };
      } else if (bb) { bb.remove(); bb = null; }
      r.classList.toggle('og-region-has-brief', !!bb);
      // the now line (time lens, today)
      let nowEl = r.querySelector('.og-now');
      const nowIt = L.items.get('now:' + c.id);
      if (nowIt) {
        if (!nowEl) { nowEl = el('div', 'og-now'); nowEl.append(el('span', 'og-now-t')); r.append(nowEl); }
        nowEl.style.transform = `translate3d(${nowIt.x - c.x}px, ${nowIt.y - c.y}px, 0)`;
        nowEl.style.width = nowIt.w + 'px';
        nowEl.firstChild.textContent = 'now · ' + time.clock(Date.now());
      } else if (nowEl) nowEl.remove();
      // the fold chip
      let fold = r.querySelector('.og-fold');
      const it = L.items.get('fold:' + c.id);
      if (it) {
        if (!fold) { fold = el('button', 'og-fold'); fold.type = 'button'; r.append(fold); }
        fold.dataset.cluster = c.id;
        fold.style.transform = `translate3d(${it.x - c.x}px, ${it.y - c.y}px, 0)`;
        fold.style.width = it.w + 'px';
        fold.style.height = it.h + 'px';
        const PL = { task: 'to-dos', session: 'chats', job: 'routines', proposal: 'ideas', crew: 'crew asks', goal: 'goals', media: 'clips', note: 'notes', project: 'projects' };
        // what's inside, so nothing looks lost: one kind reads "routines", a mix reads "8 chats, 6 clips"
        const tally = new Map();
        for (const b of c.folded) { const k = PL[b.kind] || b.kind; tally.set(k, (tally.get(k) || 0) + 1); }
        const top = [...tally].sort((x, y) => y[1] - x[1]).slice(0, 3);
        const kinds = top.length === 1 ? top[0][0] : top.map(([k, n]) => `${n} ${n === 1 ? k.replace(/s$/, '') : k}`).join(', ');
        fold.replaceChildren(el('span', 'og-fold-stack'), el('span', 'og-fold-t', c.open ? 'fold these back' : c.lens === 'time' ? (c.foldWords ? c.foldWords(c.folded.length) : `${c.folded.length} earlier today`) : `+${c.folded.length} more`), el('span', 'og-fold-k', c.open || c.lens ? '' : kinds));
        fold.classList.toggle('open', c.open);
      } else if (fold) fold.remove();
    }
    for (const [id, n] of prev) if (!keep.has(id)) n.remove();
  }

  function toggleCluster(cid) {
    if (expanded.has(cid)) expanded.delete(cid); else expanded.add(cid);
    store.setExpanded([...expanded]);
    const { born } = layout(OneGuy.board, { animate: true });
    born.forEach((id, i) => landCard(id, { delay: i * 35, soft: true }));
  }

  // ------------------------------------------------------------ motion helpers
  function baseTransform(id) {
    const it = L.items.get(id);
    return it ? `translate3d(${it.x}px, ${it.y}px, 0) rotate(${it.rot.toFixed(2)}deg)` : '';
  }
  function landCard(id, { delay = 0, soft = false } = {}) {
    const node = els.get(id);
    if (!node) return null;
    node.classList.remove('og-pre');
    if (REDUCED) return null;
    const base = baseTransform(id);
    const r = (hash(id) - 0.5) > 0 ? 7 : -7;
    const kf = soft
      ? [{ opacity: 0, transform: base + ' translateY(10px) scale(.96)' }, { opacity: 1, transform: base }]
      : [{ opacity: 0, transform: base + ` translateY(-14px) scale(.18) rotate(${r}deg)` }, { opacity: 1, offset: 0.35 }, { opacity: 1, transform: base }];
    return node.animate(kf, { duration: soft ? 420 : 700, delay, easing: soft ? EASE_OUT : springEase(9, 7), fill: 'backwards' });
  }
  function screenCenter(id) {
    const n = els.get(id);
    if (!n || !n.isConnected) return null;
    const r = n.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, r };
  }
  function inView(id) {
    const c = screenCenter(id);
    return c && c.x > 0 && c.x < window.innerWidth - railW() && c.y > topInset() - 20 && c.y < window.innerHeight - 60;
  }

  // ------------------------------------------------------------ entrance (blank screen → workbench)
  function entranceLine(board) {
    return OneGuy.briefing(board).line;
  }
  async function entrance(board, { quick, silent = false }) {
    entering = true;
    const all = [...els.keys()];
    const regions = [...regionsLayer.children];
    if (silent) { all.forEach((id) => els.get(id).classList.remove('og-pre')); entering = false; return; }
    if (quick || REDUCED) {
      orb.jump(...Object.values(orbHomePoint()));
      orb.show();
      document.body.classList.add('og-laid');
      regions.forEach((r) => r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400, easing: EASE_OUT }));
      all.forEach((id, i) => landCard(id, { delay: Math.min(500, i * 12), soft: true }));
      entering = false;
      return;
    }
    all.forEach((id) => els.get(id).classList.add('og-pre'));
    regions.forEach((r) => r.classList.add('og-pre'));
    const cx = (window.innerWidth - railW()) / 2, cy = window.innerHeight * 0.46;
    orb.jump(cx, cy);
    orb.follow(() => ({ x: cx, y: cy }));
    await sleep(350);
    orb.appear();
    await sleep(700);
    const line = entranceLine(board);
    orb.say(line, { ms: 3400 });
    await sleep(650);
    // he zips around placing the loudest cards one at a time, each landing with a soft settle
    const top = [...L.items.entries()].filter(([id, it]) => !it.fold && inView(id))
      .sort((a, b) => score(OneGuy.board.byId[b[0]]) - score(OneGuy.board.byId[a[0]])).slice(0, isMobile() ? 3 : 5).map(([id]) => id);
    for (const id of top) {
      const c = screenCenter(id);
      if (!c) continue;
      await orb.flyTo(c.x, c.y, { maxMs: 360 });
      landCard(id);
      orb.hop();
      await sleep(110);
    }
    document.body.classList.add('og-laid');
    regions.forEach((r, i) => { r.classList.remove('og-pre'); r.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 600, delay: i * 30, easing: EASE_OUT }); });
    const rest = all.filter((id) => !top.includes(id)).sort((a, b) => (L.items.get(a)?.y || 0) - (L.items.get(b)?.y || 0));
    rest.forEach((id, i) => landCard(id, { delay: Math.min(650, i * 22), soft: true }));
    orb.home();
    const firstHot = top.find((id) => HOT.has(OneGuy.board.byId[id]?.status));
    if (firstHot) { orb.pointAt(firstHot); setTimeout(() => orb.pointAt(null), 1800); }
    await sleep(500);
    entering = false;
  }

  // new blocks from a poll arrive with the orb placing them
  async function placeNew(ids) {
    const vis = ids.filter((id) => els.has(id));
    vis.forEach((id) => els.get(id).classList.add('og-pre'));
    let n = 0;
    for (const id of vis) {
      if (n < 3 && inView(id) && !bloom) {
        const c = screenCenter(id);
        await orb.flyTo(c.x, c.y, { maxMs: 500 });
        landCard(id);
        orb.hop();
        n++;
        await sleep(160);
      } else landCard(id, { soft: true });
    }
    if (n) orb.home();
    const b = OneGuy.board.byId[vis[0]];
    if (b && HOT.has(b.status)) orb.say('new: ' + (headlineOf(b).title || b.title).toLowerCase().slice(0, 60), { ms: 3200 });
  }

  // ------------------------------------------------------------ orb home
  function orbHomePoint() {
    const vw = window.innerWidth, vh = window.innerHeight;
    if (isMobile()) return { x: 28, y: vh - 41 - safeBottom() };   // beside the bar, not over the last cards
    const barW = Math.min(620, vw - railW() - 32);
    const left = (vw - railW()) / 2 - barW / 2;
    return { x: Math.max(28, left - 32), y: vh - 39 };
  }
  function safeBottom() {
    return parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--og-sab')) || 0;
  }

  // ------------------------------------------------------------ bloom (focus)
  function bloomRect() {
    const vw = window.innerWidth, vh = window.innerHeight;
    if (isMobile()) return { left: 0, top: 0, width: vw, height: vh };
    // the chat column takes the right edge: the bloom makes room instead of sliding under it
    // (same width column.js gives it: min(420px, 44vw)); beside the column the bloom keeps 56px on its left so
    // the orb that sits there is on screen at tablet widths too (it hung half off the edge)
    const colW = document.body.classList.contains('og-col-open') ? Math.min(420, Math.round(vw * 0.44)) : 0;
    const avail = vw - railW() - colW;
    const width = Math.max(320, Math.min(800, avail - (colW ? 112 : 96)));
    const top = Math.max(72, topInset() - 6);
    return { left: Math.round((avail - width) / 2), top, width, maxH: vh - top - 92 };
  }
  function openBloom(id, { srcEl = null, fromRect = null } = {}) {
    const b = OneGuy.board.byId[id];
    if (!b) return false;
    if (focusId === id && bloom) return true;
    if (bloom) { OneGuy._refocus = true; try { closeBloom({ instant: true, keepThread: true }); } finally { OneGuy._refocus = false; } }
    if (view.level !== 'blocks') setLevel('blocks');
    focusId = id;
    OneGuy.focused = b;
    const src = srcEl || els.get(id);
    // in the briefing the canvas is hidden, so its card isn't where the user is looking: grow from what he
    // tapped (a rail chip's rect), else his rail chip, else from the middle
    const srcSeen = src && src.isConnected && !(document.body.dataset.view === 'briefing' && !srcEl);
    const srcRect = fromRect || (srcSeen ? src.getBoundingClientRect() : (rail.chipRect(id) || null));

    const panel = el('section', 'bloom og-bloom');
    panel.dataset.id = id;
    panel.dataset.kind = b.kind;
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', headlineOf(b).title || b.title);
    panel.dataset.view = document.body.dataset.view || 'canvas';
    // what's IN the panel is the card grammar's (focus.js): the item, behind this, three slots
    const { hd, body, thread, foot } = compose(b, panel, { onClose: () => OneGuy.unfocus() });

    const R = bloomRect();
    Object.assign(panel.style, isMobile() ? {} : { left: R.left + 'px', top: R.top + 'px', width: R.width + 'px', maxHeight: R.maxH + 'px' });
    document.body.prepend(panel);   // first in the document: the focused item is THE thing (reading order, tab order, tools)
    bloom = { id, panel, src, srcEl };
    document.body.classList.add('og-focusing');
    backdrop.hidden = false;
    requestAnimationFrame(() => backdrop.classList.add('show'));
    const linked = new Set(b.links || []);
    for (const [bid, n] of els) {
      n.classList.toggle('og-related', linked.has(bid));
      n.classList.toggle('og-recede', bid !== id && !linked.has(bid));
    }
    if (src) src.classList.add('og-src-hidden');

    // glide to centre and bloom (FLIP from the card's rect)
    if (!REDUCED) {
      const fin = panel.getBoundingClientRect();
      const from = srcRect || { left: fin.left + fin.width * 0.3, top: fin.top + 40, width: fin.width * 0.4, height: 120 };
      const sx = from.width / fin.width, sy = Math.max(0.08, from.height / fin.height);
      panel.animate([
        { transform: `translate(${from.left - fin.left}px, ${from.top - fin.top}px) scale(${sx}, ${sy})`, opacity: 0.55, borderRadius: '14px' },
        { transform: 'none', opacity: 1, borderRadius: isMobile() ? '0px' : '20px' },
      ], { duration: 560, easing: springEase(8, 9) });
      [hd, body, thread, foot].forEach((n, i) => n.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
        { duration: 420, delay: 140 + i * 60, easing: EASE_OUT, fill: 'backwards' }));
    }
    // the one guy drifts beside it
    orb.follow(() => {
      const r = panel.getBoundingClientRect();
      return isMobile() ? { x: 26, y: r.top + 34 + safeTop() } : { x: r.left - 30, y: r.top + 30 };
    });
    orb.pointAt(null);
    OneGuy.emit('focus', { block: b });
    OneGuy.emit('bloom-open', { block: b, panel });
    panel.classList.toggle('og-bloom-talking', !!(OneGuy.column && OneGuy.column.has(id)));
    store.setFocus(id);   // remember where we were working (the user lost a chat to a reload)
    // focusing IS working with him: his thread for this item opens beside it (desktop: the column;
    // phone: the thread sits inside the panel, mounted by surface.js on bloom-open)
    if (!isMobile() && OneGuy.column && !OneGuy.column.has(id)) { try { OneGuy.column.open(b, { quiet: true }); } catch (e) { console.warn('[home] column', e); } }
    setTimeout(() => drawTendrils(true), 260);
    attachBloomFlick(panel, hd);
    panel.focus && panel.setAttribute('tabindex', '-1');
    return true;
  }
  const safeTop = () => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--og-sat')) || 0;

  function closeBloom({ instant = false, keepThread = false } = {}) {
    if (!bloom) return;
    const { id, panel, src, srcEl } = bloom;
    const b = OneGuy.board.byId[id] || { id };
    bloom = null;
    focusId = null;
    OneGuy.focused = null;
    // leaving focus leaves the work: his thread folds with it (one focus, one thread)
    store.setFocus(null);
    if (!keepThread && OneGuy.column && OneGuy.column.has(id)) { try { OneGuy.column.close({ silent: true }); } catch (e) { /* ignore */ } }
    OneGuy.emit('bloom-close', { block: b });
    OneGuy.emit('focus', { block: null });
    document.body.classList.remove('og-focusing');
    backdrop.classList.remove('show');
    setTimeout(() => { if (!bloom) backdrop.hidden = true; }, 320);
    for (const n of els.values()) n.classList.remove('og-related', 'og-recede');
    clearTendrils();
    orb.home();
    const card = srcEl && srcEl.isConnected ? srcEl : els.get(id);
    const seen = card && card.isConnected && !(document.body.dataset.view === 'briefing' && !srcEl);
    const done = () => {
      panel.remove();
      if (card) card.classList.remove('og-src-hidden');
      if (src && src !== card) src.classList.remove('og-src-hidden');
      // land: the card it folded into keeps the focus ring for a beat, so the user sees where it lives now
      if (seen && !instant && !REDUCED) {
        card.classList.remove('og-landed'); void card.offsetWidth; card.classList.add('og-landed');
        setTimeout(() => card.classList.remove('og-landed'), 1400);
      }
    };
    if (instant || REDUCED) { done(); return; }
    const fin = panel.getBoundingClientRect();
    const to = seen ? card.getBoundingClientRect() : null;
    const a = panel.animate(to ? [
      { transform: 'none', opacity: 1 },
      { transform: `translate(${to.left - fin.left}px, ${to.top - fin.top}px) scale(${to.width / fin.width}, ${Math.max(0.08, to.height / fin.height)})`, opacity: 0.4 },
    ] : [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(.96) translateY(16px)' }], { duration: 380, easing: EASE_IN_OUT, fill: 'forwards' });
    a.onfinish = done;
  }

  function attachBloomFlick(panel, hd) {
    let y0 = null, t0 = 0, id = null;
    hd.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      y0 = e.clientY; t0 = performance.now(); id = e.pointerId;
      hd.setPointerCapture(id);
    });
    hd.addEventListener('pointermove', (e) => {
      if (y0 == null) return;
      const d = Math.max(0, e.clientY - y0);
      panel.style.transform = `translateY(${d * 0.9}px)`;
      panel.style.opacity = String(1 - Math.min(0.5, d / 600));
    });
    const end = (e) => {
      if (y0 == null) return;
      const d = e.clientY - y0, v = d / Math.max(1, performance.now() - t0);
      y0 = null;
      if (d > 110 || (d > 30 && v > 0.7)) { panel.style.transform = ''; panel.style.opacity = ''; OneGuy.unfocus(); return; }
      panel.animate([{ transform: panel.style.transform }, { transform: 'none' }], { duration: 380, easing: springEase(8, 10) });
      panel.style.transform = '';
      panel.style.opacity = '';
    };
    hd.addEventListener('pointerup', end);
    hd.addEventListener('pointercancel', end);
  }

  // ------------------------------------------------------------ tendrils (teal, drawn on)
  const SVGNS = 'http://www.w3.org/2000/svg';
  function clearTendrils() {
    for (const p of [...tendrils.children]) {
      if (REDUCED) { p.remove(); continue; }
      p.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, easing: EASE_OUT, fill: 'forwards' }).onfinish = () => p.remove();
    }
  }
  function drawTendrils(animate) {
    if (!bloom || isMobile() || document.body.dataset.view === 'briefing') { tendrils.replaceChildren(); return; }
    const b = OneGuy.board.byId[bloom.id];
    if (!b) return;
    const pr = bloom.panel.getBoundingClientRect();
    const vw = window.innerWidth - railW();
    tendrils.replaceChildren();
    for (const r of regionsLayer.querySelectorAll('.og-related')) r.classList.remove('og-related');
    // a link lands on its card, else its cluster label (projects), its cluster's fold chip, or its rail chip
    const rectC = (n) => { if (!n) return null; const r = n.getBoundingClientRect(); return r.width ? { x: r.left + r.width / 2, y: r.top + r.height / 2, n } : null; };
    const target = (lid) => {
      const c = screenCenter(lid);
      if (c && !els.get(lid).classList.contains('og-src-hidden')) return c;
      const reg = regionsLayer.querySelector(`.og-region[data-cluster="${CSS.escape(lid)}"]`);
      if (reg) return rectC(reg.querySelector('.og-region-l .og-region-t') || reg);
      const cl = L.plan && L.plan.clusters.find((x) => !x.open && x.folded.some((f) => f.id === lid));
      if (cl) return rectC(regionsLayer.querySelector(`.og-fold[data-cluster="${CSS.escape(cl.id)}"]`));
      const chip = rail.chipRect(lid);
      return chip ? { x: chip.left + 8, y: chip.top + chip.height / 2 } : null;
    };
    const seen = new Set();
    let i = 0;
    for (const lid of b.links || []) {
      const c = target(lid);
      if (c && c.n) {
        if (seen.has(c.n)) continue;
        seen.add(c.n);
        const reg = c.n.closest('.og-region');
        if (reg) reg.classList.add('og-related');
      }
      if (!c || c.x < -40 || c.x > vw + 40 || c.y < -40 || c.y > window.innerHeight + 40) continue;
      if (c.x > pr.left && c.x < pr.right && c.y > pr.top && c.y < pr.bottom) continue;   // hidden under the panel
      if (c.x > window.innerWidth - railW() && !isMobile()) { c.x = Math.min(c.x, window.innerWidth - railW() + 30); }
      let ax, ay;
      if (c.x < pr.left) { ax = pr.left; ay = clamp(c.y, pr.top + 36, pr.bottom - 36); }
      else if (c.x > pr.right) { ax = pr.right; ay = clamp(c.y, pr.top + 36, pr.bottom - 36); }
      else { ax = clamp(c.x, pr.left + 40, pr.right - 40); ay = c.y < pr.top ? pr.top : pr.bottom; }
      const bx = c.x, by = c.y;
      const horiz = Math.abs(bx - ax) > Math.abs(by - ay);
      const wig = (hash(lid) - 0.5) * 50;
      const d = horiz
        ? `M${ax},${ay} C${ax + (bx - ax) * 0.45},${ay + wig} ${bx - (bx - ax) * 0.45},${by - wig} ${bx},${by}`
        : `M${ax},${ay} C${ax + wig},${ay + (by - ay) * 0.45} ${bx - wig},${by - (by - ay) * 0.45} ${bx},${by}`;
      const path = document.createElementNS(SVGNS, 'path');
      path.setAttribute('d', d);
      path.setAttribute('pathLength', '1');
      path.setAttribute('class', 'og-tendril');
      const dot = document.createElementNS(SVGNS, 'circle');
      dot.setAttribute('cx', bx); dot.setAttribute('cy', by); dot.setAttribute('r', '3.5');
      dot.setAttribute('class', 'og-tendril-end');
      tendrils.append(path, dot);
      if (animate && !REDUCED) {
        path.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 760, delay: i * 110, easing: EASE_OUT, fill: 'backwards' });
        dot.animate([{ opacity: 0, transform: 'scale(.2)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 420, delay: 600 + i * 110, easing: springEase(8, 11), fill: 'backwards' });
      }
      i++;
      if (i >= 8) break;
    }
  }

  // ------------------------------------------------------------ flick to the rail / summon back
  function flickToRail(id, v = { x: 1.2, y: 0 }) {
    const node = els.get(id);
    const b = OneGuy.board.byId[id];
    if (!node || !b) return Promise.resolve();
    const r = node.getBoundingClientRect();
    const target = rail.slotPoint('parked');
    const clone = node.cloneNode(true);
    clone.classList.remove('glide', 'og-lift', 'og-pre');
    clone.classList.add('og-flyer');
    const w = node.offsetWidth, h = node.offsetHeight;
    Object.assign(clone.style, { width: w + 'px', height: h + 'px', left: '0px', top: '0px', transform: 'none' });
    document.body.append(clone);
    node.classList.add('og-gone');
    const P0 = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    const D = 0.85;
    const T0 = { x: v.x * 1000 * D * 0.55, y: v.y * 1000 * D * 0.55 };
    const T1 = isMobile() ? { x: 60, y: 160 } : { x: 260, y: 0 };
    const spin = 14 + clamp(Math.hypot(v.x, v.y) * 4, 0, 10);
    const s0 = view.z, s1 = Math.min(0.5, 212 / w);
    const t0 = performance.now();
    store.set(id, { parked: true });
    return new Promise((resolve) => {
      const step = (now) => {
        const p = clamp((now - t0) / (D * 1000), 0, 1);
        const e = outCubic(p) + 0.03 * Math.sin(Math.PI * seg(p, 0.6, 1));
        const u = Math.min(e, 1), u2 = u * u, u3 = u2 * u;
        const h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2;
        let x = h00 * P0.x + h10 * T0.x + h01 * target.x + h11 * T1.x;
        let y = h00 * P0.y + h10 * T0.y + h01 * target.y + h11 * T1.y;
        if (e > 1) { x += (T1.x * 0.15) * (e - 1); }
        const rot = spin * Math.sin(Math.PI * Math.min(1, p * 1.15)) * (1 - p * 0.4);
        const sc = lerp(s0 * 1.04, s1, inOutCubic(p));
        clone.style.transform = `translate(${(x - w / 2).toFixed(1)}px, ${(y - h / 2).toFixed(1)}px) rotate(${rot.toFixed(2)}deg) scale(${sc.toFixed(3)})`;
        clone.style.opacity = (1 - inOutSine(seg(p, 0.6, 0.92))).toFixed(3);
        if (p < 1 && !REDUCED) requestAnimationFrame(step);
        else {
          clone.remove();
          layout(OneGuy.board, { animate: true });
          rail.pop(id);
          OneGuy.emit('park', { block: b });
          resolve();
        }
      };
      requestAnimationFrame(step);
    });
  }

  // Put a card back from the rail. { quiet: true } = no fly-in or flash: the caller is about to open
  // it (main.js openFromRail, the one rail → surface hook), so the card only has to be in place
  // (panned into view on the canvas) for the bloom to fold back into later.
  function summon(id, fromRect, { quiet = false } = {}) {
    const b = OneGuy.board.byId[id];
    if (!b) return;
    const pl = store.get(id) || {};
    const wasParked = !!pl.parked;
    const wasAuto = memo.tier.has(id) && !isDormant(b) && pl.x == null;
    if (pl.parked || isDormant(b) || !L.items.has(id)) {
      if (!wasAuto || isDormant(b)) {
        // it comes back where the user can see it: the open middle of the current view —
        // as a session nudge now, never a stored position (relational canvas)
        const G = g();
        const vw = window.innerWidth - railW();
        const n = [...nudges.values()].length;
        const w = widthFor('card', G);
        const pt = toWorld(isMobile() ? G.margin : vw / 2 - w / 2 + (n % 3) * 26, window.innerHeight * 0.32 + (n % 3) * 26);
        store.set(id, { parked: false });
        nudges.set(id, { x: Math.round(pt.x), y: Math.round(pt.y) });
      } else store.set(id, { parked: false });
    }
    layout(OneGuy.board, { animate: true });
    const node = els.get(id);
    if (quiet) {
      if (node && document.body.dataset.view !== 'briefing' && !inView(id)) panToItem(id);
      OneGuy.emit('summon', { block: b, wasParked });
      return;
    }
    if (!node) return;
    const it = L.items.get(id);
    const base = baseTransform(id);
    const fr = fromRect || rail.slotPoint('parked');
    const fx = fr.left != null ? fr.left + fr.width / 2 : fr.x, fy = fr.top != null ? fr.top + fr.height / 2 : fr.y;
    const wpt = toWorld(fx, fy);
    if (!REDUCED && it) {
      node.classList.remove('glide');
      const dx = wpt.x - (it.x + it.w / 2), dy = wpt.y - (it.y + it.h / 2);
      node.animate([
        { transform: base + ` translate(${dx}px, ${dy}px) scale(.45)`, opacity: 0 },
        { opacity: 1, offset: 0.25 },
        { transform: base, opacity: 1 },
      ], { duration: 720, easing: springEase(7, 9) });
    }
    node.classList.add('og-flash');
    setTimeout(() => node.classList.remove('og-flash'), 1600);
    orb.pointAt(id);
    setTimeout(() => orb.pointAt(null), 1400);
    OneGuy.emit('summon', { block: b, wasParked });
    if (!inView(id)) panToItem(id);
  }

  function panToItem(id) {
    const it = L.items.get(id);
    if (!it) return;
    const vw = window.innerWidth - railW(), vh = window.innerHeight;
    view.x = vw / 2 - (it.x + it.w / 2) * view.z;
    view.y = vh * 0.42 - (it.y + it.h / 2) * view.z;
    clampView();
    applyView(true);
  }

  OneGuy.on('column', ({ open, block } = {}) => {
    if (open || !block || !bloom || bloom.id !== block.id) return;
    // the thread folded: so does the focus — unless it only moved on to another item (handled below)
    setTimeout(() => { if (bloom && bloom.id === block.id && !(OneGuy.column && OneGuy.column.current())) closeBloom(); }, 0);
  });
  // One focus, one thread — both ways. If his thread opens for an item that isn't the one in focus
  // (the user steered him at something else, or the talk bar promoted a line into a conversation), the focus
  // follows the thread: a put-away item comes back first, and the trail grows.
  OneGuy.on('column', ({ open, block } = {}) => {
    if (!open || !block || !OneGuy.board.byId[block.id] || (bloom && bloom.id === block.id)) return;
    const p = store.get(block.id);
    if (p && p.parked) summon(block.id, null, { quiet: true });
    if (bloom && OneGuy.hop) OneGuy.hop(block.id); else OneGuy.focus(block.id);
  });
  OneGuy.on('column', ({ open, block } = {}) => requestAnimationFrame(() => {
    // the conversation is already open beside it: the bloom stops offering the way in
    if (bloom) bloom.panel.classList.toggle('og-bloom-talking', !!(OneGuy.column && OneGuy.column.has(bloom.id)));
    if (!bloom || isMobile()) return;
    const R = bloomRect();
    Object.assign(bloom.panel.style, { left: R.left + 'px', top: R.top + 'px', width: R.width + 'px', maxHeight: R.maxH + 'px' });
    setTimeout(() => drawTendrils(false), 360);
  }));

  // ------------------------------------------------------------ dealt with → it leaves (fly-off)
  // A decision (approve / keep / not now / done …) used to end in a fade on the next poll. Now the
  // thing the user acted on — the open bloom, else its card — takes a stamp and flies to the one guy.
  OneGuy.on('action-done', ({ block, action, result } = {}) => {
    if (!block || !action) return;
    const stamp = result && result.decided ? '✓ ' + String(result.decided).toLowerCase().slice(0, 28) : SENT[action.id];
    if (!stamp || (action.id === 'done' && block.status === 'done')) return;   // 'reopen' brings it back; nothing leaves
    const o = orb.el && orb.el.getBoundingClientRect();
    const to = o && o.width ? { x: o.left + o.width / 2, y: o.top + o.height / 2 } : { x: window.innerWidth / 2, y: window.innerHeight - 40 };
    const node = els.get(block.id);
    const hide = (n) => { if (!n) return; n.classList.add('og-gone'); setTimeout(() => n.classList.remove('og-gone'), 1600); };
    let p;
    if (bloom && bloom.id === block.id) {
      const brief = bloom.srcEl;
      p = flyOff(bloom.panel, to, { stamp });
      closeBloom({ instant: true });
      hide(node);
      if (brief && brief.isConnected) { brief.style.visibility = 'hidden'; setTimeout(() => { brief.style.visibility = ''; }, 1600); }
    } else if (node && document.body.dataset.view === 'canvas') {
      p = flyOff(node, to, { stamp });
      hide(node);
    }
    if (p) p.then(() => orb.hop && orb.hop());
  });

  // today → a lens: the briefing's cards are the same nodes too. Each canvas card that was on the
  // rail grows out of where its briefing card stood (rects: id → DOMRect taken before the switch).
  function flyFrom(rects) {
    if (REDUCED || !rects) return;
    let i = 0;
    for (const [id, from] of rects) {
      const node = els.get(id), it = L.items.get(id);
      if (!node || !it || !from.width) continue;
      const base = baseTransform(id);
      const now = node.getBoundingClientRect();
      if (!now.width) continue;
      const dx = (from.left - now.left) / view.z, dy = (from.top - now.top) / view.z;
      node.animate([
        { transform: `translate3d(${it.x + dx}px, ${it.y + dy}px, 0) scale(${(from.width / now.width).toFixed(3)})`, transformOrigin: '0 0', opacity: 1 },
        { transform: base, transformOrigin: '0 0', opacity: 1 },
      ], { duration: 760, delay: Math.min(240, i * 40), easing: springEase(6.5, 4.2), fill: 'backwards' });
      i++;
    }
  }

  // ------------------------------------------------------------ switching lenses is travel (FLIP)
  // FIRST = where each card sits now; the new lens lays everything out (LAST); every card that
  // exists in both glides from one to the other, staggered. Cards the new lens doesn't show fade
  // where they stand; cards only the new lens shows land softly once the travellers have left.
  function setLens(next, { animate = true } = {}) {
    next = ['board', 'time', 'inbox'].includes(next) ? next : 'board';
    if (next === lens) return false;
    if (view.level !== 'blocks') setLevel('blocks');
    const first = new Map();
    for (const [id, it] of L.items) if (!it.fold && els.has(id)) first.set(id, `translate3d(${it.x}px, ${it.y}px, 0) rotate(${it.rot.toFixed(2)}deg)`);
    lens = next;
    memo = memos[lens] || (memos[lens] = newMemo());
    nudges.clear();
    document.body.dataset.ogLens = lens;
    if (!OneGuy.board.blocks.length) return true;
    const { born } = layout(OneGuy.board, { animate: false });
    view.x = isMobile() ? 16 : 32;
    view.y = topInset();
    if (lens !== 'board' && !isMobile()) {   // centre a lens that's narrower than the room
      const room = window.innerWidth - railW();
      if (L.bounds.w * view.z < room - 64) view.x = Math.round((room - L.bounds.w * view.z) / 2);
    }
    clampView();
    applyView(animate && !REDUCED);
    const go = animate && !REDUCED;
    let i = 0;
    const order = [...L.items.entries()].filter(([id, it]) => !it.fold && els.has(id)).sort((a, b) => a[1].x - b[1].x || a[1].y - b[1].y);
    for (const [id] of order) {
      const node = els.get(id);
      node.classList.remove('og-pre', 'glide');
      if (!go) continue;
      if (first.has(id)) {
        // an in-out glide, not a spring: the card must be seen LEAVING its old spot ( — a
        // front-loaded ease had the new layout standing after 200ms and the switch read as a cut)
        node.animate([{ transform: first.get(id) }, { transform: baseTransform(id) }],
          { duration: 640, delay: Math.min(320, i * 24), easing: EASE_IN_OUT, fill: 'backwards' });
        i++;
      }
    }
    if (go) born.forEach((id, n) => landCard(id, { delay: 420 + Math.min(360, n * 30), soft: true }));
    if (bloom) relinkBloom(null); else orb.home();
    OneGuy.emit('lens', { lens, plan: L.plan });
    return true;
  }

  // The focused panel outlives a change of lens or view: point it at wherever its card lives NOW,
  // so the rest recedes around it and unfocus folds it back into that spot (highlighted).
  function relinkBloom(srcEl) {
    if (!bloom) return;
    const id = bloom.id;
    for (const n of document.querySelectorAll('.og-src-hidden')) n.classList.remove('og-src-hidden');
    bloom.srcEl = srcEl || null;
    bloom.src = srcEl || els.get(id) || null;
    if (bloom.src) bloom.src.classList.add('og-src-hidden');
    const linked = new Set((OneGuy.board.byId[id] || {}).links || []);
    for (const [bid, n] of els) {
      n.classList.toggle('og-related', linked.has(bid));
      n.classList.toggle('og-recede', bid !== id && !linked.has(bid));
    }
    setTimeout(() => drawTendrils(true), 700);
  }
  // ------------------------------------------------------------ preview before yes
  // A proposal's moves, shown before the user says yes: the SAME layout engine runs with the moves held in
  // `pv` (never saved, never logged), so the cards glide to exactly where a yes would put them, and
  // a thin line runs back to where each one was. end puts the layout memory back as it was;
  // end(true) is the yes: the page has stored the placements, the cards simply stay.
  let pvMemo = null;
  const pvLines = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  pvLines.setAttribute('class', 'og-pv-lines');
  pvLines.setAttribute('aria-hidden', 'true');
  const centers = (ids) => new Map(ids.map((id) => [id, L.items.get(id)]).filter(([, it]) => it).map(([id, it]) => [id, { x: it.x + it.w / 2, y: it.y + it.h / 2 }]));
  function relayout() {
    if (!OneGuy.board.blocks.length || document.body.dataset.view === 'briefing') return;
    const { born } = layout(OneGuy.board, { animate: true });
    born.forEach((id, i) => landCard(id, { delay: i * 35, soft: true }));
  }
  OneGuy.preview = {
    get on() { return !!pvMemo; },
    pile: (cid) => String(clusterMeta(OneGuy.board).info(cid).title || '').replace(/\s*\([^)]*\)\s*$/, ''),
    relayout,
    start(moves, anchor, pt) {
      if (pvMemo) this.end();
      const ids = Object.keys(moves || {}).filter((id) => OneGuy.board.byId[id]);
      if (!ids.length || lens !== 'board' || document.body.dataset.view === 'briefing' || view.level !== 'blocks') return;
      pvMemo = { tier: new Map(memo.tier), order: new Map(memo.order), clusters: [...memo.clusters], pos: new Map(memo.pos), packW: memo.packW, lens };
      const was = centers(ids), a0 = anchor && L.items.get(anchor);
      for (const id of ids) pv.set(id, moves[id]);
      relayout();
      const now = centers(ids);
      let a1 = anchor && L.items.get(anchor);
      // the card whose verb is under the pointer must not slide out from under it: if the new layout
      // moved it, the whole desk shifts the other way, on the same curve as the cards' own glide
      const W = window.innerWidth - railW(), Hh = window.innerHeight - 120, top = topInset() + 8;
      if (a0 && a1) {
        pvMemo.view = { x: view.x, y: view.y, z: view.z };
        // P = the point that must not move on screen (the verb under the pointer), carried with its card
        const P = pt || { x: view.x + view.z * a0.x, y: view.y + view.z * a0.y };
        const off = { x: (P.x - view.x) / view.z - a0.x, y: (P.y - view.y) / view.z - a0.y };
        a1 = { x: a1.x + off.x, y: a1.y + off.y };
        const z = view.z;      // the zoom is the user's: a preview never pulls the desk back (tried; it reads worse)
        const to = { x: P.x - z * a1.x, y: P.y - z * a1.y, z };
        if (Math.abs(to.x - view.x) > 0.5 || Math.abs(to.y - view.y) > 0.5 || to.z !== view.z) {
          Object.assign(view, to);
          applyView(!REDUCED);
          if (!REDUCED) world.style.transition = 'transform 720ms var(--spring)';
        }
      }
      // which of the moved cards the user can actually see where they land (the caller names the rest in words)
      const seen = new Set(ids.filter((id) => {
        const it = L.items.get(id);
        if (!it) return false;
        const cx = view.x + view.z * (it.x + it.w / 2), cy = view.y + view.z * (it.y + it.h / 2);
        return cx > 0 && cx < W && cy > top && cy < Hh + 60;
      }));
      pvLines.replaceChildren();
      for (const [id, a] of was) {
        const b = now.get(id);
        if (!b || Math.hypot(a.x - b.x, a.y - b.y) < 8) continue;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M${a.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)}`);
        const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        dot.setAttribute('cx', a.x.toFixed(1)); dot.setAttribute('cy', a.y.toFixed(1)); dot.setAttribute('r', '3.5');
        pvLines.append(path, dot);
      }
      world.append(pvLines);
      return { seen };
    },
    end(keep, moved = []) {
      const snap = pvMemo;
      pvMemo = null;
      pv.clear();
      pvLines.remove();
      if (keep) {
        // a yes: open the pile's fold if a card he just moved would otherwise vanish into it
        if (lens === 'board') for (const c of plan(OneGuy.board).clusters) if (!c.open && c.folded.some((b) => moved.includes(b.id))) expanded.add(c.id);
        store.setExpanded([...expanded]);
      } else if (!snap) return;
      else if (snap.lens === lens) Object.assign(memo, { tier: snap.tier, order: snap.order, clusters: snap.clusters, pos: snap.pos, packW: snap.packW });
      relayout();
      // he let go: the desk goes back to where the user had it (unless he has moved it himself meanwhile)
      if (snap && snap.view && snap.lens === lens) {
        const at = { x: view.x, y: view.y, z: view.z };
        const home = () => {
          if (pvMemo || lens !== snap.lens || view.x !== at.x || view.y !== at.y || view.z !== at.z) return;
          Object.assign(view, snap.view);
          applyView(!REDUCED);
          if (!REDUCED) world.style.transition = 'transform 720ms var(--spring)';
        };
        if (!keep) home();
      }
    },
  };

  // where an item lives in the lens you're looking at (for the trail): its pile, day or section
  OneGuy.placeOf = (id) => {
    if (document.body.dataset.view === 'briefing') return null;
    const it = L.items.get(id);
    const c = it && L.clusters.find((x) => x.id === it.cluster);
    return c ? String(c.title || '').replace(/\s*\([^)]*\)\s*$/, '') : null;
  };

  // the pile a card lives in, by id, so a frame can sit on the pile when the card itself is tucked
  // away (its fold on the canvas, its place on today)
  OneGuy.pileOf = (id) => {
    const it = L.items.get(id);
    if (it && it.cluster) return it.cluster;
    const cl = L.plan && L.plan.clusters.find((x) => (x.all || x.folded || []).some((f) => f.id === id));
    return cl ? cl.id : null;
  };

  // ------------------------------------------------------------ zoom levels
  function counts(c) {
    const xs = c.id === 'cluster:quiet' ? c.all : c.all.filter((b) => b.kind !== 'project');
    return {
      hot: xs.filter((b) => HOT.has(b.status)).length,
      active: xs.filter((b) => b.status === 'active').length,
      total: xs.length,
    };
  }
  function renderProjects() {
    const grid = el('div', 'og-pgrid');
    const clusters = [...L.plan.clusters].sort((a, b) => counts(b).hot - counts(a).hot || 0);
    for (const c of L.plan.clusters.length ? clusters : []) {
      const n = counts(c);
      const card = el('button', 'og-pcard');
      card.type = 'button';
      card.dataset.cluster = c.id;
      card.style.setProperty('--pc', c.color);
      const top = el('div', 'og-pcard-top');
      top.append(el('i', 'og-pdot'), el('span', 'og-pcard-t', c.title));
      const SYS = { 'cluster:routines': 'scheduled jobs that run on their own', 'cluster:loose': 'things with no project yet', 'cluster:quiet': 'projects with nothing open right now', 'cluster:goals': '', 'cluster:housekeeping': 'to-do, calendar, email — the furniture', 'cluster:week': 'things that happen at a time · open the week' };
      const firstTitles = c.all.filter((b) => b.kind !== 'project' || c.id === 'cluster:quiet').slice(0, 3).map((b) => headlineOf(b).title || b.title).join(' · ');
      const sum = el('div', 'og-pcard-s', (c.block && c.block.summary) || SYS[c.id] || firstTitles);
      const row = el('div', 'og-pcard-n');
      if (n.hot) row.append(el('span', 'hot', `${n.hot} for you`));
      if (n.active) row.append(el('span', 'act', `${n.active} moving`));
      row.append(el('span', '', `${n.total} thing${n.total === 1 ? '' : 's'}`));
      card.append(top, sum);
      const hotB = c.all.find((b) => HOT.has(b.status) && b.kind !== 'project');
      if (hotB) card.append(el('div', 'og-pcard-need', '→ ' + (headlineOf(hotB).title || hotB.title)));
      card.append(row);
      card.onclick = () => setLevel('blocks', { cluster: c.id });
      grid.append(card);
    }
    projectsEl.replaceChildren(grid);
  }
  function setLevel(level, { cluster } = {}) {
    if (level === view.level && !cluster) return;
    const prev = view.level;
    view.level = level;
    document.body.dataset.level = level;
    store.setView({ level });
    OneGuy.emit('level', { level });
    if (level === 'projects') {
      if (bloom) closeBloom({ instant: true });
      renderProjects();
      projectsEl.hidden = false;
      if (!REDUCED) {
        world.animate([{ opacity: 1, transform: world.style.transform }, { opacity: 0, transform: world.style.transform + ' scale(.94)' }], { duration: 320, easing: EASE_OUT });
        [...projectsEl.querySelectorAll('.og-pcard')].forEach((n, i) => n.animate(
          [{ opacity: 0, transform: 'scale(1.06) translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: 480, delay: 80 + i * 28, easing: springEase(8, 10), fill: 'backwards' }));
      }
      orb.say(`${words(L.plan.clusters.length)} piles.`, { ms: 1800 });
    } else {
      projectsEl.hidden = true;
      if (prev === 'projects' && view.z < 0.7) view.z = 1;
      if (cluster) {
        const c = L.clusters.find((x) => x.id === cluster);
        if (c) {
          const vw = window.innerWidth - railW();
          view.x = Math.max(g().margin, vw / 2 - (c.x + c.w / 2) * view.z);
          if (c.w * view.z > vw - 64 || isMobile()) view.x = g().margin - c.x * view.z;
          view.y = topInset() + 12 - c.y * view.z;
          clampView();
          const r = regionsLayer.querySelector(`.og-region[data-cluster="${CSS.escape(cluster)}"]`);
          if (r) { r.classList.add('og-flash'); setTimeout(() => r.classList.remove('og-flash'), 1500); }
        }
      }
      applyView(false);
      if (!REDUCED && prev === 'projects') world.animate([{ opacity: 0, transform: world.style.transform + ' scale(.96)' }, { opacity: 1, transform: world.style.transform }], { duration: 420, easing: EASE_OUT });
    }
  }

  // ------------------------------------------------------------ judgments + tidy (Nov 9)
  // A drag is never stored as pixels. It is one of: a cluster judgment (persisted
  // relationally in placements.cluster), a nudge (session-only), a park (rail), or a
  // tidy (reset to system layout). Every judgment logs to the ledger (/api/judgment).
  function logJudgment(kind, block, target) {
    try {
      OneGuy.api('/api/judgment', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, block: block ? block.id : null, target: target || null }) })
        .then((r) => { if (r && !r.ok) console.warn('[home] judgment refused', kind, r.status); })   // the ledger never blocks the gesture, but a refusal is loud
        .catch(() => {});
    } catch (err) { /* the ledger never blocks the gesture */ }
  }
  function clusterUnderPoint(sx, sy, exceptNode) {
    if (lens !== 'board') return null;   // days and inbox sections aren't piles: a drop there is only a nudge
    // the dragged card is z-raised under the cursor: walk the stack, skip it
    for (const hit of document.elementsFromPoint(sx, sy)) {
      const reg = hit.closest && hit.closest('.og-region');
      if (reg && !(exceptNode && reg.contains(exceptNode))) return reg.dataset.cluster;
    }
    return null;
  }
  function nudge(id, wx, wy) {
    nudges.set(id, { x: Math.round(wx), y: Math.round(wy) });
    const node = els.get(id);
    if (node) { node.classList.add('og-nudged'); node.style.transform = `translate3d(${Math.round(wx)}px, ${Math.round(wy)}px, 0)`; }
    logJudgment('nudge', OneGuy.board.byId[id]);
  }
  function clusterMove(id, cid) {
    store.set(id, { cluster: cid, parked: false });
    nudges.delete(id);
    logJudgment('cluster', OneGuy.board.byId[id], cid);
    layout(OneGuy.board, { animate: true });
  }
  function messy() {
    return nudges.size > 0 || Object.values(store.data.placements || {}).some((p) => p && (p.x != null || p.cluster));
  }
  function reset() {
    nudges.clear();
    for (const [id, p] of Object.entries(store.data.placements || {})) {
      if (p && (p.x != null || p.cluster)) store.drop(id);
    }
    OneGuy.emit('tidy', {});
    layout(OneGuy.board, { animate: true });
    logJudgment('reset');
    orb.say('table’s tidy.', { ms: 2200 });
  }

  // ------------------------------------------------------------ the selection (lasso to ask, frontier 1)
  // The cards the user drew a box round (gestures.js; the ids live in core.js OneGuy.select) wear a soft
  // outline, and one row of plain words rides above them: ask about these (the talk bar: the turn
  // opens a new thread about them, talk.js) · group (each one a cluster judgment, the same as a drop
  // on a pile) · put away (the same put-away as a flick, one judgment each).
  // a pile's 'turn into a brief' is quiet until the pointer is in that pile, on its ground or on one of
  // its cards (five of them always on were the most repeated words on the desk). Touch keeps
  // them: there is no hover there (home.css).
  let overPile = null;
  document.addEventListener('pointerover', (e) => {
    const n = e.target && e.target.closest ? e.target.closest('#og-cards [data-cluster], .og-region') : null;
    const cid = (n && n.dataset.cluster) || null;
    if (cid === overPile) return;
    overPile = cid;
    for (const r of regionsLayer.children) r.classList.toggle('og-region-over', !!cid && r.dataset.cluster === cid);
  }, { passive: true, capture: true });

  const selMenu = el('div', 'og-sel-menu');
  selMenu.hidden = true;
  selMenu.setAttribute('role', 'toolbar');
  selMenu.setAttribute('aria-label', 'the cards you picked');
  const selBtn = (words, title, fn) => {
    const b = el('button', 'og-sel-b', words);
    b.type = 'button';
    b.title = title;
    b.onclick = (e) => { e.stopPropagation(); fn(); };
    selMenu.append(b);
    return b;
  };
  selBtn('ask about these', 'ask one thing about all of them (a new conversation)', () => OneGuy.talk && OneGuy.talk.focus());
  const selGroup = selBtn('group', 'put them in one pile', () => groupSelected());
  selBtn('put away', 'put them all away (they wait on the rail)', () => putAwaySelected());
  const selBrief = selBtn('turn into a brief', 'write one short brief of these cards (it keeps a link back to each)', () => briefSelected());
  document.body.append(selMenu);
  let selRaf = 0;
  function placeSelMenu() {
    selRaf = 0;
    const nodes = OneGuy.selected.map((id) => els.get(id)).filter((n) => n && n.isConnected && !n.classList.contains('og-gone'));
    if (!nodes.length || bloom || document.body.dataset.view === 'briefing') { selMenu.hidden = true; if (OneGuy.selected.length) selRaf = requestAnimationFrame(placeSelMenu); return; }
    selGroup.hidden = lens !== 'board';          // piles only show on the board
    selBrief.textContent = isMobile() ? 'brief' : 'turn into a brief';     // one row at 390
    selMenu.hidden = false;
    let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
    for (const n of nodes) { const q = n.getBoundingClientRect(); l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom); }
    const w = selMenu.offsetWidth, h = selMenu.offsetHeight;
    const roomR = window.innerWidth - (isMobile() ? 0 : railW()) - 8;
    const top = clamp(t - h - 10, topInset() + 6, window.innerHeight - h - 96);    // above the cards, never under the header or the talk bar
    selMenu.style.transform = `translate(${Math.round(clamp((l + r) / 2 - w / 2, 8, Math.max(8, roomR - w)))}px, ${Math.round(top)}px)`;
    selRaf = requestAnimationFrame(placeSelMenu);      // it rides the cards while the desk pans
  }
  OneGuy.on('select', () => {
    const sel = new Set(OneGuy.selected);
    for (const [id, node] of els) node.classList.toggle('og-sel', sel.has(id));
    document.body.classList.toggle('og-selecting', sel.size > 0);
    cancelAnimationFrame(selRaf);
    if (sel.size) placeSelMenu(); else selMenu.hidden = true;
  });
  // the pile most of them are already in (a real project pile wins a tie); the rest join it
  function groupSelected() {
    const meta = clusterMeta(OneGuy.board);
    const ids = OneGuy.selected.filter((id) => OneGuy.board.byId[id]);
    const at = new Map(ids.map((id) => [id, meta.clusterOf(OneGuy.board.byId[id])]));
    const votes = new Map();
    for (const cid of at.values()) if (cid) votes.set(cid, (votes.get(cid) || 0) + 1);
    const cid = [...votes.keys()].sort((a, b) => votes.get(b) - votes.get(a) || a.startsWith('cluster:') - b.startsWith('cluster:'))[0];
    const moving = ids.filter((id) => at.get(id) !== cid);
    if (!cid || !moving.length) { orb.say('they’re already in one pile.', { ms: 2400 }); return; }
    const was = new Map(moving.map((id) => [id, store.get(id) ? { ...store.get(id) } : null]));
    for (const id of moving) {
      store.set(id, { cluster: cid, parked: false });
      nudges.delete(id);
      logJudgment('cluster', OneGuy.board.byId[id], cid);
    }
    OneGuy.select.clear();
    layout(OneGuy.board, { animate: true });
    const pile = String(meta.info(cid).title || 'one pile').replace(/\s*\([^)]*\)\s*$/, '');
    const n = moving.length;
    orb.say(`${n === 1 ? 'one card' : n + ' cards'} moved to ${pile}.`, { ms: 2600 });
    if (OneGuy.talk && OneGuy.talk.showUndo) {
      OneGuy.talk.showUndo(() => {
        for (const [id, p] of was) {
          if (p) store.set(id, { cluster: p.cluster, parked: p.parked }); else store.drop(id);
          if (OneGuy.board.byId[id]) OneGuy.judge('undo', OneGuy.board.byId[id], 'cluster');
        }
        layout(OneGuy.board, { animate: true });
        return { ok: true };
      }, `grouped in ${pile}`);
    }
  }
  // ------------------------------------------------------------ turn into a brief (frontier 6)
  // A pile, or the cards he picked, become one short brief: the server gathers the cards and asks
  // the model once (board/brief.py). While it writes, the claim frame reads `drafting…` on the pile
  // (or on the picked cards). The brief lands as a card in that pile, linked to its sources both
  // ways (the connections strip). No pixels: where it sits is the pile it was written from.
  let drafting = false;
  function briefSelected() {
    const meta = clusterMeta(OneGuy.board);
    const ids = OneGuy.selected.filter((id) => OneGuy.board.byId[id]);
    const votes = new Map();
    for (const id of ids) { const cid = meta.clusterOf(OneGuy.board.byId[id]); if (cid) votes.set(cid, (votes.get(cid) || 0) + 1); }
    const cid = [...votes.keys()].sort((a, b) => votes.get(b) - votes.get(a))[0] || null;   // it lands in the pile most of them are in
    return turnIntoBrief(ids, cid, null);
  }
  async function turnIntoBrief(ids, pile, name) {
    ids = (ids || []).filter((id) => OneGuy.board.byId[id] && OneGuy.board.byId[id].kind !== 'project');
    if (!ids.length) return null;
    if (drafting) { orb.say('one brief at a time. this one’s nearly there.', { ms: 2600 }); return null; }
    drafting = true;
    const picked = !name;
    const c = OneGuy.surface && OneGuy.surface.claim ? OneGuy.surface.claim.open() : null;
    if (c) c.frame(ids, 'drafting…', picked ? null : pile);
    OneGuy.select.clear();
    let r = null;
    try {
      const res = await OneGuy.api('/api/brief', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids, pile, name }) });
      r = res ? await res.json() : null;
    } catch (err) { r = null; }
    drafting = false;
    if (!r || !r.ok) {
      if (c && !c.cancelled) { if (r && r.sample) c.end(); else c.fail(); }
      orb.say(r && r.sample ? 'this is the sample desk, so nothing gets written here. on your own desk i’d write it.' : 'couldn’t write that brief. nothing changed.', { ms: 3600 });
      return null;
    }
    if (c) { if (c.cancelled) c.stuck(); else c.land(); }      // he said stop, but it was already written: say so
    OneGuy.judge('select', OneGuy.board.byId[ids[0]], { ids: r.sources || ids }, 'turned into a brief');
    briefNew = r.block_id;
    await OneGuy.refresh({ quiet: true });
    const node = els.get(r.block_id);
    if (node) {
      if (lens === 'board' && document.body.dataset.view !== 'briefing' && !inView(r.block_id)) panToItem(r.block_id);
      node.classList.remove('og-landed'); void node.offsetWidth; node.classList.add('og-landed');
      setTimeout(() => node.classList.remove('og-landed'), 1400);
    }
    const n = (r.sources || ids).length;
    orb.say(r.by === 'plain'
      ? `no model to write with, so i put the brief together from the ${n} cards as they are. it’s ${name ? 'in ' + name : 'on the desk'}.`
      : `the brief is ${name ? 'in ' + name : 'on the desk'}, linked to ${n === 1 ? 'its card' : 'all ' + n + ' cards'}.`, { ms: 4200 });
    return r;
  }
  function putAwaySelected() {
    const ids = OneGuy.selected.filter((id) => OneGuy.board.byId[id]);
    OneGuy.select.clear();
    ids.forEach((id, i) => setTimeout(() => OneGuy.park(id), i * 90));     // one after another, each its own park judgment
  }

  const api = {
    els, view,
    get memo() { return memo; },
    get lens() { return lens; },
    setLens, flyFrom, relinkBloom,
    get layoutState() { return L; },
    get focusId() { return focusId; },
    get bloom() { return bloom; },
    get entering() { return entering; },
    layout, entrance, placeNew, applyView, clampView, toWorld,
    openBloom, closeBloom, drawTendrils,
    fitBloom() {
      if (!bloom) return;
      const R = bloomRect();
      if (isMobile()) Object.assign(bloom.panel.style, { left: '', top: '', width: '', maxHeight: '' });
      else Object.assign(bloom.panel.style, { left: R.left + 'px', top: R.top + 'px', width: R.width + 'px', maxHeight: R.maxH + 'px' });
      drawTendrils(false);
    },
    flickToRail, summon, toggleCluster, setLevel, panToItem,
    nudge, clusterMove, clusterUnderPoint, messy, reset, groupSelected, putAwaySelected,
    logJudgment,
    // an item's id changed under it (a tasklist anchor): the same card element carries on
    rekey(from, to) {
      const node = els.get(from);
      if (!node || from === to || els.has(to)) return;
      els.delete(from); els.set(to, node); node.dataset.id = to;
      sig.delete(from);
      const it = L.items.get(from); if (it) { L.items.delete(from); L.items.set(to, it); }
      if (focusId === from) focusId = to;
    },
    orbHomePoint, screenCenter, baseTransform,
    itemOf: (id) => L.items.get(id),
    counts,
    isDormant,
  };
  time.bind(api);   // the Time lens's doors re-lay the week through it
  return api;
}
