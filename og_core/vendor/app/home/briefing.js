// 1guy home · the briefing (WS-S, v1.2). The default first view: the one guy talks first and
// shows everything for you as ONE infinitely scrollable horizontal card rail (the user: "instead
// of three things today"). His line (board.briefing.line, or a templated one), the rail of
// "for you" cards in plain words, one small amber line if a routine broke, then a calm grid of
// project tiles. Tap a card to work on it; flick it right to put it away; tap a tile to open
// that project's cluster on the canvas; "show everything" opens the whole canvas.
import OneGuy, { el, clamp, hash, springEase, EASE_OUT, REDUCED, store, sleep, headlineOf, words, HOT } from './core.js';
import { faceVerb } from './focus.js';
import { createDoors } from './doors.js';

const HINT_KEY = 'og.hint.v11';
const HINT = 'tap one and we’ll work on it. flick it right to put it away.';
const SYS_TILE = { 'cluster:routines': 'routines', 'cluster:loose': 'loose ends', 'cluster:goals': 'goals', 'cluster:quiet': 'quiet projects' };
const cleanTitle = (t) => String(t || '').replace(/\s*\([^)]*\)\s*$/, '');

export function createBriefing({ root, canvas, rail, orb, isMobile, onShowAll, onTile }) {
  let sheet = null;               // current briefing data
  let lineText = '';
  let speakT = null;
  const cards = new Map();        // id → element

  root.replaceChildren();
  const col = el('div', 'og-brief-col');
  const head = el('div', 'og-brief-head');
  const line = el('h1', 'og-brief-line');
  line.setAttribute('aria-live', 'polite');
  const hint = el('p', 'og-brief-hint');
  head.append(line, hint);
  const grid = el('div', 'og-fy-grid');
  // it's a rail, not an overflow: while there's more to the right the edge fades and a button
  // pages one card along (the next card always peeks out from under the fade)
  const wrap = el('div', 'og-fy-wrap');
  const next = el('button', 'og-fy-next', '→');
  next.type = 'button';
  next.setAttribute('aria-label', 'more for you');
  next.onclick = () => {
    const step = (grid.firstElementChild ? grid.firstElementChild.getBoundingClientRect().width : 380) + 20;
    grid.scrollBy({ left: step, behavior: REDUCED ? 'auto' : 'smooth' });
  };
  wrap.append(grid, next);
  const edge = () => {
    const more = grid.scrollWidth - grid.clientWidth - grid.scrollLeft > 12;
    wrap.classList.toggle('og-fy-more', more);
    wrap.classList.toggle('og-fy-scrolled', grid.scrollLeft > 12);
    const n = grid.children.length;
    next.title = more && n > 1 ? `${n} for you — keep going` : '';
  };
  grid.addEventListener('scroll', edge, { passive: true });
  window.addEventListener('resize', edge);
  // the housekeeping glances (widgets): one number + one next thing each
  const glances = el('div', 'og-glances');
  const offer = el('div', 'og-offer');   // his offer to keep an eye on email + calendar (renderOffer)
  offer.hidden = true;
  // made for you: the newest things the crew created (docs, pages, cuts, reports)
  const madeHd = el('div', 'og-made-hd');
  madeHd.append(el('span', 'og-tiles-l', 'made for you'));
  const made = el('div', 'og-made');
  const broke = el('div', 'og-broke-row');
  const tilesHd = el('div', 'og-tiles-hd');
  const tilesL = el('span', 'og-tiles-l', 'everything else');
  const all = el('button', 'og-showall', 'show everything →');
  all.type = 'button';
  all.onclick = () => onShowAll && onShowAll();
  tilesHd.append(tilesL, all);
  const tiles = el('div', 'og-tiles');
  col.append(head, wrap, glances, offer, tilesHd, tiles, madeHd, made, broke);   // the rooms sit above 'made for you' so they're on the first screen
  root.append(col);
  // the first run's two doors (doors.js): door B's box sits above the rail, his last line under it
  const doors = createDoors({ col, wrap });

  // ------------------------------------------------------------ faces
  // The shell composes the briefing card: project dot, headline title, why, the question if it's
  // a choice. A kind that declares `headlines: true` when it registers renders its own face here
  // instead (K): it gets el.dataset.view = 'briefing' and data-size = 'hero'.
  function face(b, node) {
    const h = headlineOf(b);
    const k = OneGuy.kinds[b.kind];
    node.dataset.view = 'briefing';
    node.dataset.size = 'hero';
    if (k && k.headlines && k.card) {
      try {
        k.card(b, node);
        if (b.fy_why) node.append(el('div', 'og-fy-whytag', b.fy_why));
        faceVerb(b, node);
        return;
      }
      catch (err) { console.warn('[brief] kind face failed', b.id, err); }
    }
    node.replaceChildren();
    const proj = b.project && (OneGuy.board.projects || []).find((p) => p.id === b.project);
    const top = el('div', 'og-fy-top');
    const dot = el('i', 'og-fy-dot');
    if (proj && proj.color) dot.style.background = proj.color;
    top.append(dot, el('span', 'og-fy-proj', proj ? cleanTitle(proj.title) : (b.kind === 'job' ? 'a routine' : 'loose end')));
    node.append(top, el('div', 'og-fy-t', h.title || b.title));
    if (b.fy_why) node.append(el('div', 'og-fy-whytag', b.fy_why));   // phase 3.5: the why badge
    if (h.why) node.append(el('div', 'og-fy-why', h.why));
    const ask = h.ask && h.ask.question ? h.ask.question : '';
    if (ask) node.append(el('div', 'og-fy-ask', ask));
    node.setAttribute('aria-label', (h.title || b.title) + (b.fy_why ? ' — ' + b.fy_why : '') + (h.why ? '. ' + h.why : ''));
    faceVerb(b, node);
  }

  function cardFor(b, i) {
    let node = cards.get(b.id);
    if (!node) {
      node = el('div', 'og-fy');
      node.dataset.id = b.id;
      node.tabIndex = 0;
      node.setAttribute('role', 'button');
      cards.set(b.id, node);
      attachCardGestures(node);
    }
    node.style.setProperty('--rot', isMobile() ? '0deg' : ((hash(b.id) - 0.5) * 1.2).toFixed(2) + 'deg');
    node.style.setProperty('--i', i);
    const sig = JSON.stringify([b.title, b.summary, b.status, b.headline, b.changed && b.changed.what]);
    if (node.dataset.sig !== sig) { face(b, node); node.dataset.sig = sig; }
    node.classList.toggle('og-hot', HOT.has(b.status));
    return node;
  }

  // ------------------------------------------------------------ tiles (one per canvas cluster)
  function tileList(sh = sheet) {
    const P = canvas.layoutState && canvas.layoutState.plan;
    if (!P) return [];
    // the cards above already show some waiting things: tiles count only the rest, so the tiles
    // add up to the header's "N more waiting" instead of double-counting the cards
    const shown = new Set(sh ? [...sh.forYou, ...sh.broke].map((b) => b.id) : []);
    return P.clusters.map((c) => {
      const n = { ...canvas.counts(c) };
      n.hot = Math.max(0, n.hot - c.all.filter((b) => shown.has(b.id) && HOT.has(b.status)).length);
      return { c, n };
    })
      .filter(({ n }) => n.total > 0)
      .sort((a, b) => (a.c.id.startsWith('cluster:') - b.c.id.startsWith('cluster:')) || b.n.hot - a.n.hot || b.n.total - a.n.total);
  }
  // ------------------------------------------------------------ places (lab/place, graduated)
  // "Everything else" is a little street of rooms, one per pile: an illustrated place with a few
  // of its things inside as tags. It glows teal while the crew is working in it and red when
  // something in it broke. Tap = step inside: the room opens and its cards fill the view; Esc
  // (or ×, or the dark around it) steps back out. Things drop in when they arrive.
  const ART = { 'cluster:routines': 'engine', 'cluster:housekeeping': 'house', 'cluster:loose': 'workshop', 'cluster:quiet': 'library', 'cluster:goals': 'library' };
  const SPARE = ['workshop', 'records', 'library', 'house', 'newsstand'];
  const PLACE_NAME = { 'cluster:routines': 'the engine room', 'cluster:housekeeping': 'the house', 'cluster:loose': 'loose ends', 'cluster:quiet': 'quiet projects', 'cluster:goals': 'goals' };
  const artOf = (cid) => OneGuy.api.url(`/app/lab/places/${ART[cid] || SPARE[Math.floor(hash(cid) * SPARE.length) % SPARE.length]}.webp`);
  const nameOf = (c) => PLACE_NAME[c.id] || cleanTitle(c.title);
  const isLive = (b) => !!(OneGuy.live && OneGuy.live.get && OneGuy.live.get(b.id)) || (b.kind === 'session' && b.status === 'active');
  const inside = (c) => c.all.filter((b) => b.kind !== 'project' || c.id === 'cluster:quiet');
  const rank = (b) => (b.status === 'failed' ? 3 : HOT.has(b.status) ? 2 : isLive(b) ? 1 : 0) + (b.attention || 0);
  const short = (t) => { const w = String(t || '').split(/\s+/); const o = w.slice(0, 3).join(' '); return o.length > 24 ? o.slice(0, 23) + '…' : o + (w.length > 3 ? '…' : ''); };
  const seenTags = new Set();      // tags the user has already seen: only new arrivals drop in
  let room = null;                 // {cid, el} while he's inside one

  function renderTiles() {
    const list = tileList();
    const prev = new Map([...tiles.children].map((n) => [n.dataset.cluster, n]));
    const first = !seenTags.size;
    tiles.replaceChildren();
    for (const { c, n } of list) {
      const xs = inside(c).sort((a, b) => rank(b) - rank(a));
      const t = prev.get(c.id) || el('button', 'og-place og-tile');
      t.type = 'button';
      t.dataset.cluster = c.id;
      t.style.setProperty('--pc', c.color || '#8b8d94');
      const broke = xs.some((b) => b.status === 'failed'), busy = xs.some(isLive);
      // the room says what you'd find inside it: every card waiting on you, including the ones that
      // are also on the rail (n.hot leaves those out so the HEADER's "more waiting" adds up)
      const waiting = xs.filter((b) => b.status === 'needs-you').length;
      t.classList.toggle('og-place-alarm', broke);
      t.classList.toggle('og-place-busy', busy && !broke);
      let img = t.querySelector('img');
      if (!img) { img = el('img', 'og-place-art'); img.alt = ''; img.loading = 'lazy'; img.draggable = false; img.src = artOf(c.id); t.append(img); }
      const tags = el('span', 'og-place-tags');
      xs.slice(0, 2).forEach((b, i) => {
        const tag = el('span', 'og-place-tag' + (b.status === 'failed' ? ' bad' : HOT.has(b.status) ? ' you' : ''));
        if (isLive(b)) tag.append(el('i', 'og-place-dot'));
        tag.append(short(headlineOf(b).title || b.title));
        if (!first && !seenTags.has(b.id) && !REDUCED) { tag.classList.add('og-place-drop'); tag.style.animationDelay = i * 90 + 'ms'; }
        seenTags.add(b.id);
        tags.append(tag);
      });
      const nm = el('span', 'og-place-nm og-tile-t', nameOf(c));
      const ct = el('span', 'og-place-ct og-tile-n' + (waiting ? ' you' : ''),
        broke ? 'something broke' : waiting ? `${waiting} waiting on you` : busy ? 'crew working' : `${n.total} inside`);
      for (const old of t.querySelectorAll('.og-place-tags, .og-place-nm, .og-place-ct')) old.remove();
      t.append(tags, nm, ct);
      t.setAttribute('aria-label', `${nameOf(c)}: ${ct.textContent}. step inside`);
      t.onclick = () => enter(c.id, t);
      tiles.append(t);
    }
    tilesHd.hidden = !list.length;
    if (room) fillRoom();          // the board moved while he's inside: the room keeps up
  }

  function fillRoom() {
    const P = canvas.layoutState && canvas.layoutState.plan;
    const c = P && P.clusters.find((x) => x.id === room.cid);
    if (!c) return exit();
    const xs = inside(c).sort((a, b) => rank(b) - rank(a));
    const list = room.el.querySelector('.og-room-list');
    const had = new Map([...list.children].map((n) => [n.dataset.id, n]));
    const fresh = !had.size;
    list.replaceChildren();
    xs.forEach((b, i) => {
      let node = had.get(b.id);
      const sig = JSON.stringify([b.title, b.status, b.headline, b.updated]);
      if (!node) {
        node = el('div', 'og-room-it');
        node.dataset.id = b.id;
        node.tabIndex = 0;
        node.setAttribute('role', 'button');
        const open = () => OneGuy.view('briefing', { focus: b.id, srcEl: node });
        node.onclick = (e) => { if (!e.target.closest('button, a, input, label')) open(); };
        node.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
        if (!REDUCED) node.animate([{ opacity: 0, transform: 'translateY(-26px) scale(.9)' }, { opacity: 1, offset: 0.6 }, { opacity: 1, transform: 'none' }],
          { duration: 520, delay: fresh ? 160 + i * 60 : 0, easing: springEase(8, 8), fill: 'backwards' });
      }
      if (node.dataset.sig !== sig) {
        node.dataset.sig = sig;
        node.dataset.size = 'card';
        const h = headlineOf(b), k = OneGuy.kinds[b.kind];
        try {
          if (k && k.card) k.card({ ...b, title: h.title || b.title, summary: h.why || '', tags: [], raw_title: b.title }, node);
          else node.replaceChildren(el('div', 'og-fy-t', h.title || b.title));
        } catch (err) { node.replaceChildren(el('div', 'og-fy-t', h.title || b.title)); }
        node.classList.toggle('og-hot', HOT.has(b.status));
        faceVerb(b, node);
      }
      list.append(node);
    });
    const brokeN = xs.filter((b) => b.status === 'failed').length, waitN = xs.filter((b) => b.status === 'needs-you').length;
    room.el.querySelector('.og-room-sub').textContent = `${xs.length} thing${xs.length === 1 ? '' : 's'} inside` + (brokeN ? ` · ${brokeN} broke` : '') + (waitN ? ` · ${waitN} waiting on you` : '');
  }

  function enter(cid, tile) {
    if (room) exit(true);
    const P = canvas.layoutState && canvas.layoutState.plan;
    const c = P && P.clusters.find((x) => x.id === cid);
    if (!c) return;
    const wrap = el('div', 'og-inside');
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-label', nameOf(c));
    const box = el('div', 'og-room');
    const img = el('img', 'og-room-art');
    img.alt = '';
    img.src = artOf(cid);
    const col = el('div', 'og-room-col');
    const x = el('button', 'og-room-x', '×');
    x.type = 'button';
    x.setAttribute('aria-label', 'step back out');
    x.onclick = () => exit();
    const onBoard = el('button', 'og-room-board', 'see it on the board →');
    onBoard.type = 'button';
    onBoard.onclick = () => { exit(true); onTile && onTile(cid); };
    col.append(el('h2', 'og-room-t', nameOf(c)), el('div', 'og-room-sub'), el('div', 'og-room-list'), onBoard);
    box.append(x, img, col);
    wrap.append(box);
    wrap.addEventListener('pointerdown', (e) => { if (e.target === wrap) exit(); });
    root.append(wrap);
    room = { cid, el: wrap, tile };
    document.body.classList.add('og-in-room');
    fillRoom();
    requestAnimationFrame(() => wrap.classList.add('on'));
    // the room grows out of its tile (FLIP on the picture)
    const from = tile && tile.querySelector('img') && tile.querySelector('img').getBoundingClientRect();
    const to = img.getBoundingClientRect();
    if (from && from.width && to.width && !REDUCED) {
      img.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width})`, transformOrigin: '0 0' }, { transform: 'none', transformOrigin: '0 0' }],
        { duration: 560, easing: springEase(8, 9) });
    }
    OneGuy.emit('place', { open: true, cluster: cid });
    OneGuy.orb.say(`${nameOf(c)}.`, { ms: 1600 });
  }
  function exit(instant) {
    if (!room) return;
    const { el: w, cid } = room;
    room = null;
    document.body.classList.remove('og-in-room');
    w.classList.remove('on');
    if (instant === true || REDUCED) w.remove(); else setTimeout(() => w.remove(), 320);
    OneGuy.emit('place', { open: false, cluster: cid });
  }
  // Esc steps back out — before the shell's own Esc (which would leave the view)
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !room || canvas.bloom || document.querySelector('.og-ask-scrim')) return;
    e.stopPropagation();
    e.preventDefault();
    exit();
  }, true);
  OneGuy.on('view', () => exit(true));
  OneGuy.on('live', () => { if (sheet) renderTiles(); });

  // ------------------------------------------------------------ made for you
  function renderMade(board) {
    const arts = (board.blocks || [])
      .filter((b) => b.kind === 'artifact' && b.source && /^(https?:\/\/|\/(?!\/))/i.test(String(b.source.url || '')))   // web or own-site links only
      .sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')))
      .slice(0, 6);
    made.replaceChildren();
    for (const b of arts) {
      const a = el('a', 'og-made-item');
      a.href = b.source.url;
      a.target = '_blank';
      a.rel = 'noopener';
      a.append(
        el('span', 'og-made-k', (b.detail && b.detail.kind) || 'made'),
        el('span', 'og-made-t', b.title),
        b.detail && b.detail.origin && b.detail.origin.label
          ? el('span', 'og-made-k', '↤ ' + b.detail.origin.label) : '',
        el('span', 'og-made-w', b.updated ? new Date(b.updated).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : ''),
      );
      made.append(a);
    }
    madeHd.hidden = made.hidden = !arts.length;
  }

  // ------------------------------------------------------------ the glances
  // Housekeeping furniture (widgets source): to-do / calendar / email as one
  // number + one next thing. Tap one and the bloom FLIPs out of the glance
  // (canvas.openBloom grows from srcEl). Never a "for you" card.
  function renderGlances(board) {
    const ws = (board.blocks || []).filter((b) => b.kind === 'widget').slice(0, 3);
    glances.replaceChildren();
    for (const b of ws) {
      const btn = el('button', 'og-glance');
      btn.type = 'button';
      btn.dataset.id = b.id;
      try {
        const k = OneGuy.kinds[b.kind];
        if (k && k.card) k.card(b, btn); else btn.textContent = b.title;
      } catch (err) { console.warn('[brief] glance face failed', b.id, err); btn.textContent = b.title; }
      btn.onclick = () => OneGuy.view('briefing', { focus: b.id, srcEl: btn });
      glances.append(btn);
    }
    glances.hidden = !ws.length;
    renderOffer(ws);
  }

  // ------------------------------------------------------------ his offer: email + calendar
  // When this Hermes can read mail/calendar and the job isn't set up, he asks once, in his voice,
  // right under the tiles (board/mailcal.py). yes = the user's own Hermes job is created and run
  // once; not now is remembered and the tiles stay quiet. On door B it waits for the brain dump:
  // it is the second offer, never the first thing on a fresh desk.
  let offerBusy = false;
  function renderOffer(ws) {
    const b = ws.find((w) => w.detail && w.detail.state === 'offer' && !w.detail.declined && w.detail.offer);
    const held = doors.which === 'b' && !(doors.lines > 0);
    if (!b || held || OneGuy.board.sample) { offer.hidden = true; return; }
    if (offerBusy) return;
    const o = b.detail.offer;
    if (offer.dataset.text === o.text && !offer.hidden) return;
    offer.dataset.text = o.text;
    const say = el('span', 'og-offer-t', o.text);
    const yes = el('button', 'og-offer-b og-offer-yes', o.yes || 'yes');
    const no = el('button', 'og-offer-b', o.no || 'not now');
    yes.type = no.type = 'button';
    const answer = (op) => async () => {
      offerBusy = true;
      yes.disabled = no.disabled = true;
      try {
        const r = await OneGuy.api.post(o.post || '/api/board/mailcal', { op });
        if (!r || r.ok === false) throw new Error((r && r.error) || 'didn’t go through');
        offer.hidden = true;
        if (op === 'setup') OneGuy.toast(r.say || 'on it.');
      } catch (err) {
        yes.disabled = no.disabled = false;
        OneGuy.toast(`didn’t go through: ${err.message || err}`, true);
      }
      offerBusy = false;
      OneGuy.refresh({ quiet: true });
    };
    yes.onclick = answer('setup');
    no.onclick = answer('not-now');
    offer.replaceChildren(say, yes, no);
    offer.hidden = false;
  }

  // ------------------------------------------------------------ render
  function render(board, { animate = false } = {}) {
    sheet = OneGuy.briefing(board);
    renderMade(board);
    doors.render(board);
    renderGlances(board);   // after the doors: the email/calendar offer waits for door B's first line
    lineText = opener();
    if (doors.which) hint.hidden = true;  // the door says what to do; the first-visit tip would be a second voice
    if (!speakT && !line.classList.contains('og-typing') && !root.classList.contains('og-brief-pre')) line.textContent = lineText;
    const keep = new Set();
    const nodes = doors.rail(sheet.forYou).map((b, i) => { keep.add(b.id); return cardFor(b, i); });
    for (const [id, n] of cards) {
      if (keep.has(id)) continue;
      cards.delete(id);
      if (REDUCED || !n.isConnected) n.remove();
      else n.animate([{ opacity: 1 }, { opacity: 0, transform: 'scale(.94)' }], { duration: 320, easing: EASE_OUT, fill: 'forwards' }).onfinish = () => n.remove();
    }
    nodes.forEach((n, i) => { if (grid.children[i] !== n) grid.insertBefore(n, grid.children[i] || null); n.classList.toggle('og-fy-first', i === 0 && nodes.length > 1); });
    grid.dataset.n = String(nodes.length);
    grid.hidden = wrap.hidden = !nodes.length;
    requestAnimationFrame(edge);
    // the broke line: small, amber, tap to see
    broke.replaceChildren();
    for (const b of sheet.broke) {
      const h = headlineOf(b);
      const btn = el('button', 'og-broke');
      // his headline names it in plain words; without one, a calm generic line (no job ids)
      const t = h.fallback ? (b.kind === 'job' ? 'one routine didn’t run' : `${h.title} broke`) : h.title;
      btn.title = cleanTitle(b.title);
      btn.type = 'button';
      btn.dataset.id = b.id;
      btn.append(el('i', 'og-broke-dot'), el('span', '', t.replace(/[.;]\s*$/, '') + '; tap to see'));
      btn.onclick = () => OneGuy.view('briefing', { focus: b.id, srcEl: btn });
      broke.append(btn);
    }
    broke.hidden = !sheet.broke.length;
    // nothing on the rail but something broke: it is the first thing to tap, so it sits right under his line
    const lead = !nodes.length && sheet.broke.length > 0;
    broke.classList.toggle('og-broke-lead', lead);
    if (lead) head.after(broke); else if (broke !== col.lastElementChild) col.append(broke);
    renderTiles();
    if (animate && !REDUCED) {
      nodes.forEach((n, i) => n.animate([{ opacity: 0, transform: 'translateY(10px) scale(.97)' }, { opacity: 1, transform: 'none' }],
        { duration: 460, delay: i * 60, easing: EASE_OUT, fill: 'backwards' }));
    }
    return sheet;
  }

  // what moved while he was away leads the opener, counted; tapping the line walks them (focus.js)
  function movedLead() {
    const n = OneGuy.moved ? OneGuy.moved().length : 0;
    line.classList.toggle('og-brief-line-go', n > 0);
    if (n) { line.tabIndex = 0; line.setAttribute('role', 'button'); line.title = 'tap to walk through what moved'; }
    else { line.removeAttribute('tabindex'); line.removeAttribute('role'); line.removeAttribute('title'); }
    return n ? `${n === 1 ? 'one thing' : words(n) + ' things'} moved while you were away. ` : '';
  }
  // one opener at a time: on a door-A desk where something moved, the door's long count
  // gives way to its last sentence ("three need you.") so his line is two short sentences, not four
  function opener() {
    const lead = movedLead();
    let rest = doors.line || sheet.line || '';
    if (lead && doors.which === 'a') { const last = rest.match(/[^.?!]+[.?!]+\s*$/); if (last) rest = last[0].trim(); }
    return lead + rest;
  }
  line.addEventListener('click', () => { if (line.classList.contains('og-brief-line-go')) OneGuy.walkMoved(); });
  line.addEventListener('keydown', (e) => { if (e.key === 'Enter' && line.classList.contains('og-brief-line-go')) { e.preventDefault(); e.stopPropagation(); OneGuy.walkMoved(); } });
  OneGuy.on('moved', () => {            // he looked at one: the count drops at once
    if (!sheet) return;
    lineText = opener();
    if (!speakT && !line.classList.contains('og-typing') && !root.classList.contains('og-brief-pre')) swap(lineText);
  });

  // ------------------------------------------------------------ his voice lives in the line
  function speak(text, ms = 2600) {
    if (!text) return;
    clearTimeout(speakT);
    const t = String(text);
    swap(t.charAt(0).toLowerCase() + t.slice(1));   // lowercase only the first letter: 'sure, the sync retries PR'
    speakT = setTimeout(() => { speakT = null; swap(lineText); }, ms);
  }
  function swap(t) {
    if (line.textContent === t) return;
    if (REDUCED) { line.textContent = t; return; }
    line.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: EASE_OUT }).onfinish = () => {
      line.textContent = t;
      line.animate([{ opacity: 0, transform: 'translateY(3px)' }, { opacity: 1, transform: 'none' }], { duration: 300, easing: EASE_OUT });
    };
  }
  async function typeLine(t) {
    line.textContent = '';
    line.classList.add('og-typing');
    if (REDUCED) { line.textContent = t; line.classList.remove('og-typing'); return; }
    for (let i = 1; i <= t.length; i++) {
      line.textContent = t.slice(0, i);
      await sleep(t[i - 1] === ' ' ? 34 : 22);
    }
    line.classList.remove('og-typing');
  }

  // where he waits when his line is not on screen: beside the talk bar on a phone (canvas.js orbHomePoint)
  function orbDock() {
    if (!isMobile()) return { x: 40, y: 80 };
    const sab = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--og-sab')) || 0;
    return { x: 28, y: window.innerHeight - 41 - sab };
  }
  // where the one guy sits in the briefing: just left of his line
  function orbHome() {
    const r = line.getBoundingClientRect();
    if (!r.width) return null;
    const x = r.left + (isMobile() ? 14 : 16);
    const y = r.top + (isMobile() ? 15 : 19);
    if (isMobile()) {
      // phone: he sits by his line while it is on screen under the tabs; once the page scrolls it away he
      // docks beside the talk bar (the room the bar leaves on its left), never over the cards
      const tabs = document.getElementById('og-lens');
      const top = tabs && tabs.offsetParent ? tabs.getBoundingClientRect().bottom : 98;
      if (y - 18 < top || y > window.innerHeight - 110 || document.body.classList.contains('og-kb')) return null;
      return { x, y };
    }
    return { x, y: clamp(y, 60, window.innerHeight - 90) };
  }

  // ------------------------------------------------------------ entrance (blank → his line → cards)
  async function entrance({ quick }) {
    const fy = [...grid.children];
    const rest = [...doors.nodes(), glances, offer, tilesHd, tiles, broke];
    const first = !doors.which && fy.length > 0 && !localStorage.getItem(HINT_KEY);   // nothing to tap: no 'tap one…', and the hint isn't spent
    hint.textContent = first ? HINT : '';
    hint.hidden = !first;
    if (first) { try { localStorage.setItem(HINT_KEY, String(Date.now())); } catch (e) { /* private mode */ } }
    if (quick || REDUCED) {
      line.textContent = lineText;
      orb.jump(...Object.values(orbHome() || orbDock()));
      orb.show();
      document.body.classList.add('og-laid');
      doors.focus();
      return;
    }
    root.classList.add('og-brief-pre');
    fy.forEach((n) => n.classList.add('og-pre'));
    rest.forEach((n) => n.classList.add('og-pre'));
    hint.classList.add('og-pre');
    line.textContent = '';
    const cx = window.innerWidth / 2 - (isMobile() ? 0 : 0), cy = window.innerHeight * 0.42;
    orb.jump(cx, cy);
    orb.follow(() => ({ x: cx, y: cy }));
    await sleep(300);
    orb.appear();
    await sleep(650);
    root.classList.remove('og-brief-pre');
    // he moves to his spot and says it
    const h = orbHome();
    if (h) await orb.flyTo(h.x, h.y, { zip: false, maxMs: 700 });
    orb.follow(() => orbHome() || orbDock());
    await typeLine(lineText);
    await sleep(200);
    // he places the first cards one at a time; the rest of the rail just fades in (a long rail
    // can't take a 120ms pause per card)
    const PLACE = 8;
    for (const n of fy.slice(0, PLACE)) {
      const r = n.getBoundingClientRect();
      await orb.flyTo(r.left + Math.min(60, r.width / 3), r.top + 26, { maxMs: 380 });
      n.classList.remove('og-pre');
      if (!REDUCED) n.animate([{ opacity: 0, transform: 'translateY(-12px) scale(.3) rotate(var(--rot))' }, { opacity: 1, offset: 0.35 }, { opacity: 1, transform: 'rotate(var(--rot))' }],
        { duration: 680, easing: springEase(9, 7) });
      orb.hop();
      await sleep(120);
    }
    orb.follow(() => orbHome() || orbDock());
    for (const n of fy.slice(PLACE)) {   // the rest of the rail: fade in as a group
      n.classList.remove('og-pre');
      if (!REDUCED) n.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 480, delay: 260, easing: EASE_OUT, fill: 'backwards' });
    }
    document.body.classList.add('og-laid');
    rest.forEach((n, i) => {
      n.classList.remove('og-pre');
      if (!REDUCED) n.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }], { duration: 520, delay: 120 + i * 90, easing: EASE_OUT, fill: 'backwards' });
    });
    hint.classList.remove('og-pre');
    if (first && !REDUCED) hint.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 600, delay: 600, easing: EASE_OUT, fill: 'backwards' });
    if (fy[0]) { orb.pointAt(() => { const r = fy[0].getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + 30 }; }); setTimeout(() => orb.pointAt(null), 1800); }
    doors.focus();
    await sleep(400);
  }

  // ------------------------------------------------------------ gestures on the cards
  // tap = bloom (from this card) · swipe/drag right = put it away (fly into the rail tab)
  function attachCardGestures(node) {
    let g = null;
    node.addEventListener('pointerdown', (e) => {
      if (e.button > 0 || document.body.classList.contains('og-focusing')) return;
      if (e.target.closest('button, a, input, textarea')) return;
      g = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), mode: 'maybe', samples: [{ x: e.clientX, t: performance.now() }] };
      try { node.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    });
    node.addEventListener('pointermove', (e) => {
      if (!g || e.pointerId !== g.id) return;
      const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
      const now = performance.now();
      g.samples.push({ x: e.clientX, t: now });
      while (g.samples.length > 2 && now - g.samples[0].t > 100) g.samples.shift();
      if (g.mode === 'maybe') {
        if (Math.hypot(dx, dy) < 8) return;
        g.mode = dx > 0 && Math.abs(dx) > Math.abs(dy) * 1.2 ? 'swipe' : 'none';
        if (g.mode === 'none') { try { node.releasePointerCapture(g.id); } catch (err) { /* ignore */ } g = null; return; }
        node.classList.add('og-swiping');
      }
      if (g.mode === 'swipe') {
        const d = Math.max(0, dx);
        node.style.transform = `translateX(${d.toFixed(1)}px) rotate(calc(var(--rot) + ${(d * 0.025).toFixed(2)}deg))`;
        node.style.opacity = String(1 - clamp(d / 700, 0, 0.35));
      }
    });
    const end = (e) => {
      if (!g || e.pointerId !== g.id) return;
      const s = g.samples, a = s[0], b = s[s.length - 1];
      const v = (b.x - a.x) / Math.max(8, b.t - a.t);
      const dx = e.clientX - g.x0;
      const mode = g.mode;
      g = null;
      node.classList.remove('og-swiping');
      if (mode === 'maybe' && e.type === 'pointerup') { OneGuy.view('briefing', { focus: node.dataset.id, srcEl: node }); return; }
      if (mode === 'swipe') {
        if (e.type === 'pointerup' && (dx > 140 || (dx > 40 && v > 0.5))) putAway(node.dataset.id, v);
        else {
          node.animate([{ transform: node.style.transform, opacity: node.style.opacity || 1 }, { transform: 'rotate(var(--rot))', opacity: 1 }], { duration: 420, easing: springEase(8, 10) });
          node.style.transform = ''; node.style.opacity = '';
        }
      }
    };
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', end);
    node.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); OneGuy.view('briefing', { focus: node.dataset.id, srcEl: node }); }
    });
  }

  // fly the card into "put away" (the slim tab or the open rail), then park it
  function putAway(id, v = 1) {
    const node = cards.get(id);
    const b = OneGuy.board.byId[id];
    if (!node || !b) return;
    const r = node.getBoundingClientRect();
    const target = rail.slotPoint('parked');
    const clone = node.cloneNode(true);
    clone.classList.add('og-flyer', 'og-fy-flyer');
    Object.assign(clone.style, { position: 'fixed', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', margin: 0, transform: node.style.transform || 'none' });
    document.body.append(clone);
    node.style.visibility = 'hidden';
    const dx = target.x - (r.left + r.width / 2), dy = target.y - (r.top + r.height / 2);
    const done = () => {
      clone.remove();
      store.set(id, { parked: true });
      cards.delete(id);
      node.remove();
      canvas.layout(OneGuy.board, { animate: false });
      render(OneGuy.board, { animate: false });
      rail.pop(id);
      OneGuy.emit('park', { block: b });
      OneGuy.orb.say('put away. it’s on the right.', { ms: 2200 });
    };
    if (REDUCED) { done(); return; }
    clone.animate([
      { transform: clone.style.transform, opacity: 1 },
      { transform: `translate(${dx * 0.55}px, ${dy * 0.35 - 30}px) rotate(14deg) scale(.6)`, opacity: 0.9, offset: 0.55 },
      { transform: `translate(${dx}px, ${dy}px) rotate(20deg) scale(.12)`, opacity: 0 },
    ], { duration: 760, easing: 'cubic-bezier(.3,.7,.3,1)', fill: 'forwards' }).onfinish = done;
  }

  return {
    root, render, entrance, speak, orbHome, putAway, doors,
    moreWaiting: (board) => tileList(OneGuy.briefing(board || OneGuy.board)).reduce((a, t) => a + t.n.hot, 0),   // = the tiles' "waiting on you" sum
    cardEl: (id) => cards.get(id) || null,
    get sheet() { return sheet; },
    get line() { return lineText; },
    hideHint() { hint.hidden = true; },
  };
}
