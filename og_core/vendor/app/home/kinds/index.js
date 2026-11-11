// 1guy home · kinds (WS-A2). Importing this module registers a card face and a
// bloom workspace for every block kind through OneGuy.registerKind, and loads
// kinds.css. It exports nothing and defines no globals of its own.
import { ICONS, html, mount, face, plain } from './util.js';
import * as task from './task.js';
import * as session from './session.js';
import * as crew from './crew.js';
import * as proposal from './proposal.js';
import * as job from './job.js';
import * as media from './media.js';
import * as artifact from './artifact.js';
import * as widget from './widget.js';
import * as plugin from './plugin.js';
import * as talk from './talk.js';
import { project, goal, note } from './work.js';

const KINDS = { task, project, goal, session, crew, proposal, job, media, note, artifact, talk, widget, plugin };

function loadCss() {
  if (document.querySelector('link[data-oneguy-kinds]')) return;
  const l = document.createElement('link');
  l.rel = 'stylesheet';
  l.href = new URL('./kinds.css', import.meta.url).href;
  l.dataset.oneguyKinds = '1';
  document.head.appendChild(l);
}

// One bad block must never take the board down: fall back to a plain face/body.
function guardCard(kind, fn) {
  return (block, el) => {
    try { fn(block, el); } catch (err) {
      console.warn(`[kinds] ${kind} card failed for ${block?.id}`, err);
      face(block || {}, el, { icon: ICONS[kind] || '•', kindLabel: kind, title: plain(block?.title) || block?.title, line: plain(block?.summary) });
    }
  };
}
function guardBloom(kind, fn) {
  return (block, bodyEl) => {
    try { fn(block, bodyEl); } catch (err) {
      console.warn(`[kinds] ${kind} bloom failed for ${block?.id}`, err);
      mount(bodyEl, html`<div class="kb"><section class="kb-sec"><p>${block?.summary || block?.title || ''}</p><p class="kb-note">couldn't lay this one out: ${String(err.message || err)}</p></section></div>`);
    }
  };
}

function registerAll(OG) {
  for (const [kind, def] of Object.entries(KINDS)) {
    ICONS[kind] = def.icon;
    OG.registerKind(kind, { icon: def.icon, card: guardCard(kind, def.card), bloom: guardBloom(kind, def.bloom) });
  }
}

loadCss();
const ready = () => window.OneGuy && typeof window.OneGuy.registerKind === 'function';
if (ready()) {
  registerAll(window.OneGuy);
} else {
  // core.js should define OneGuy before main.js imports us; tolerate the other order.
  await new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      if (ready()) { registerAll(window.OneGuy); resolve(); }
      else if (Date.now() - t0 > 10000) { console.warn('[kinds] OneGuy never appeared; kinds not registered'); resolve(); }
      else setTimeout(tick, 25);
    };
    tick();
  });
}
