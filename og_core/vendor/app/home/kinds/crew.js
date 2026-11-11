// crew: an issue a crew agent filed for the user (needs-j / decision).
// Face  = headline + why, a 'needs you' badge.
// Bloom = headline, why, then the one-line ask (approve / steer / reject live in
//         the shell's footer, so they're not duplicated here), or the headline's
//         options as big buttons. The issue body, number, link and thread fold
//         into 'details'.
import { html, raw, face, dots, sec, kv, safeUrl, ago, plain,
  headline, isBriefing, briefFace, details, bloomV11 } from './util.js';
import { md } from './md.js';

export const icon = '⚑';

const labelsOf = (block) => (block.detail?.labels || block.tags || []).map((l) => String(l).toLowerCase());
function badges(block) {
  const labels = labelsOf(block);
  const out = [];
  if (labels.includes('needs-j')) out.push(html`<span class="kf-badge hot">needs you</span>`);
  if (labels.includes('decision')) out.push(html`<span class="kf-badge">your call</span>`);
  return out;
}

export function card(block, el) {
  const d = block.detail || {};
  const v = d.verdict;
  const b = badges(block);
  const status = v
    ? html`<span class="kf-st st-done"><i class="sdot"></i><span>you said ${plain(v)}</span></span>`
    : (b.length ? html`<span class="kf-badges">${b}</span>` : null);
  if (isBriefing(el)) {
    const hot = !v && labelsOf(block).includes('needs-j');
    briefFace(block, el, { icon, kicker: 'from the crew', touch: v ? status : hot ? html`<span class="kf-badge hot">needs you</span>` : '' });
    return;
  }
  const H = headline(block);
  face(block, el, {
    icon, kindLabel: 'crew', status: status ?? undefined,
    title: H.title, line: H.why, prose: true,
    foot: dots(d.comments ? `${d.comments} comment${d.comments > 1 ? 's' : ''}` : '', ago(block.updated)),
  });
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const H = headline(block);
  const url = safeUrl(d.url || block.source?.url);
  const verbs = (block.actions || []).filter((a) => !a.surface && !a.href).map((a) => a.label || a.id);
  let lead = '';
  if (d.verdict) lead = html`<p class="kb-callout done">you said <b>${plain(d.verdict)}</b>. it went back to the crew.</p>`;
  else if (!H.ask && verbs.length) {
    // say what the footer's verbs mean here, in one line
    const has = (id) => (block.actions || []).some((a) => a.id === id);
    const parts = [has('approve') && html`<b>approve</b> says yes, ship what they made`, has('steer') && html`<b>steer</b> sends them a note on what to change`, has('reject') && html`<b>reject</b> sends it back`].filter(Boolean);
    lead = html`<p class="kb-callout">the crew made this and wants your yes or no.${parts.length ? html` ${parts.map((x, i) => (i ? html`; ${x}` : x))}.` : ''}</p>`;
  }
  bloomV11(block, bodyEl, html`
    ${lead}
    ${details(html`
      ${sec('what the crew wrote', d.body ? html`<div class="kb-md">${raw(md(d.body))}</div>` : html`<p class="muted">no body</p>`)}
      ${sec('the issue', kv([
        ['issue', d.number != null && html`#${d.number}`],
        ['on github', url && html`<a class="kb-a" href="${url}" target="_blank" rel="noopener noreferrer">open it ↗</a>`],
        ['comments', String(d.comments ?? 0)],
        ['filed', ago(block.updated)],
        ['labels', labelsOf(block).length && html`<span class="kb-tags">${labelsOf(block).map((l) => html`<span class="kb-tag">${l}</span>`)}</span>`],
      ]))}
    `)}
  `);
}
