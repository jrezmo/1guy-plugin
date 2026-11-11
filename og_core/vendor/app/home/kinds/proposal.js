// proposal: the one guy suggesting something (watcher or retro). His voice:
// dashed amber edge, a small orb, warm text.
// Bloom = 'he suggests', the headline, why, and the draft preview. keep / dismiss /
//         build it come from block.actions in the shell's footer. Evidence quotes,
//         who wrote it and links fold into 'details'.
import { html, raw, face, dots, sec, kv, ago, tagChips, plain,
  headline, isBriefing, briefFace, details, bloomV11, same } from './util.js';
import { md } from './md.js';

export const icon = '✦';
const ORB = raw('<span class="orbdot" aria-hidden="true"></span>');
const isInfra = (block) => (block.tags || []).includes('infra') || block.detail?.writer === 'retro';

// his why: headline.why, else his own words from the proposal, else the summary
function propH(block) {
  const H = headline(block);
  const d = block.detail || {};
  if (!block.headline?.why) {
    const w = plain(d.why);
    if (w && !same(w, H.title)) H.why = H.why && !same(H.why, w) ? H.why : w;
  }
  return H;
}

export function card(block, el) {
  const d = block.detail || {};
  if (isBriefing(el)) {
    briefFace(block, el, { icon: ORB, kicker: 'he suggests' });
    return;
  }
  const H = headline(block);
  face(block, el, {
    icon: ORB, kindLabel: 'the one guy suggests',
    status: html``,
    title: H.title, line: H.why, prose: true,
    foot: dots(isInfra(block) ? 'for me to build' : 'for you', ago(d.created || block.updated)),
  });
}

function quote(e) {
  if (!e || typeof e !== 'object') return html`<li class="kb-quote"><q>${e}</q></li>`;
  return html`<li class="kb-quote"><span class="kb-src">${e.type || 'source'}</span><q>${e.quote || ''}</q>${e.ref ? html`<span class="mono muted kb-ref">${e.ref}</span>` : ''}</li>`;
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const H = propH(block);
  const ev = d.evidence || [];
  const infra = isInfra(block);
  const fullWhy = d.why && !same(plain(d.why), H.why) ? d.why : '';
  const summary = block.summary && !same(plain(block.summary), H.why) && !same(plain(block.summary), H.title) ? block.summary : '';
  bloomV11(block, bodyEl, html`
    <div class="kb-kicker">${ORB}<span>he suggests</span></div>
    ${d.draft ? html`<section class="kb-sec">
        <h4 class="kb-h">${infra ? 'what i\'d build' : 'what i\'d add to your list'}</h4>
        ${infra ? html`<pre class="kb-draft mono">${d.draft}</pre>` : html`<div class="kb-draft kb-md">${raw(md(d.draft))}</div>`}
      </section>` : ''}
    ${details(html`
      ${fullWhy || summary ? sec('his reasoning', html`${fullWhy ? html`<p class="kb-voice sm">${fullWhy}</p>` : ''}${summary ? html`<p class="kb-sub">${summary}</p>` : ''}`) : ''}
      ${ev.length ? sec('what i saw', html`<ul class="kb-quotes">${ev.map(quote)}</ul>`) : ''}
      ${sec('about', kv([
        ['from', d.writer === 'retro' ? 'my retro' : d.writer ? 'watching your week' : ''],
        ['written', ago(d.created || block.updated)],
        ['tags', (block.tags || []).length && tagChips(block.tags)],
      ]), 'kb-quiet')}
    `)}
  `);
}
