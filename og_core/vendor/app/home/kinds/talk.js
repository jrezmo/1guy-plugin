// talk: one of the talk bar's own interactions (request-router). A conversation that
// became real work: fresh thread, fresh session, a card on the board. Tapping resumes
// that key's own session (the column path).
import { html, face, tok, ago, headline, isBriefing, briefFace, bloomV11 } from './util.js';

export const icon = '✎';

function statusTouch(block) {
  const live = block.status === 'active';
  return live
    ? html`<span class="kf-st live"><i class="pulse"></i><span>live</span></span>`
    : html`<span class="kf-st st-${tok(block.status)}"><i class="sdot"></i><span>${ago(block.updated)}</span></span>`;
}

export function card(block, el) {
  if (isBriefing(el)) {
    briefFace(block, el, { icon, kicker: 'a conversation', touch: statusTouch(block) });
    return;
  }
  const H = headline(block);   // face goes through headline/plain — never raw title
  face(block, el, {
    icon, kindLabel: 'talk', status: statusTouch(block),
    title: H.title,
    line: H.why || 'a conversation you started',
    prose: !!H.why,
  });
  if (block.status === 'active') el.classList.add('kc-live');
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  bloomV11(block, bodyEl, html`
    <section class="kb-sec">
      <p class="muted">this one started in the talk bar${d.turns ? ` · ${d.turns} turns so far` : ''}.
      keep going here — it has its own thread.</p>
    </section>
  `);
}

export const KIND = { card, bloom, icon };
