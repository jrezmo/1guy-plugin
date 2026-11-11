// plugin: one of the user's Hermes plugin surfaces, as a tile under project:hermes.
// The card says the human name and the one-line what/why. Opening it is the card's
// `open` action (focus.js puts it in the ⋯ slot); a surface with no url says so here.
// Faces go through headline so no raw ids/paths land on the canvas.
import { html, mount, plain, headline } from './util.js';

export const icon = '◫';

const hasOpen = (block) => (block.actions || []).some((a) => a.id === 'open' && a.href);

export function card(block, el) {
  const H = headline(block);
  const line = H.why || plain(block.detail?.what) || '';
  mount(el, html`<div class="kp st-${block.status}" data-kp>
    <div class="kp-name"><i class="kf-ico" aria-hidden="true">${icon}</i><span>${H.title}</span></div>
    <p class="kp-what">${line}</p>
    ${hasOpen(block) ? '' : html`<div class="kp-foot"><span class="kp-hint">open in desktop</span></div>`}
  </div>`);
  return el;
}

// bloom: the what/why in full.
export function bloom(block, bodyEl) {
  const d = block.detail || {};
  mount(bodyEl, html`<div class="kb"><section class="kb-sec">
    <p class="kb-note">${d.wired ? 'this surface opens right here.' : 'this one lives in the desktop app — no url from this box.'}</p>
    ${d.desktop ? html`<p class="kb-note">it has a desktop pane; the mac carries its other half.</p>` : ''}
  </section></div>`);
}

export default { card, bloom, icon };
