// media: a clip or the reel.
// Face  = poster thumbnail + headline + why.
// Bloom = the headline, why, and the video, big, playing in place. Outputs,
//         folder and links fold into 'details'.
import { html, face, dots, sec, kv, ago, duration,
  headline, plain, same, isBriefing, briefFace, details, bloomV11 } from './util.js';

export const icon = '▶';

// Media must be same-origin (no external resources): absolute paths or relative.
export function localUrl(u) {
  const s = String(u ?? '').trim();
  if (!s || /^[a-z][a-z0-9+.-]*:/i.test(s) || s.startsWith('//')) return '';
  return s;
}
const isReel = (block) => block.id === 'media:reel' || block.source?.ref === 'reel';
const outUrl = (d, name) => localUrl(/^\//.test(name) ? name : `/${String(d.path || '').replace(/^\/+|\/+$/g, '')}/${name}`);
const thumb = (d, poster) => html`<span class="kf-thumb"><img src="${poster}" alt="" loading="lazy" decoding="async" draggable="false"><i class="kf-play" aria-hidden="true">▶</i>${d.duration ? html`<span class="kf-dur">${duration(d.duration)}</span>` : ''}</span>`;

export function card(block, el) {
  const d = block.detail || {};
  const poster = localUrl(d.poster);
  if (isBriefing(el)) {
    briefFace(block, el, { icon, kicker: isReel(block) ? 'the reel' : 'a clip', media: poster ? html`<div class="kfb-media">${thumb(d, poster)}</div>` : '' });
    return;
  }
  const H = headline(block);
  face(block, el, {
    icon, kindLabel: isReel(block) ? 'the reel' : 'clip',
    title: H.title,
    lead: poster ? thumb(d, poster) : '',
    line: H.why, prose: true,
    foot: dots(!poster && d.duration && duration(d.duration), ago(block.updated)),
  });
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const H = headline(block);
  const poster = localUrl(d.poster);
  const video = localUrl(d.video);
  const live = localUrl(block.source?.url);
  const reel = isReel(block);
  const links = [];
  if (reel && !(block.actions || []).some((a) => a.href === '/reel')) links.push(html`<a class="kb-chip kb-a" href="/reel">open the reel ↗</a>`);
  if (!reel && live && /\.html?$/.test(live)) links.push(html`<a class="kb-chip kb-a" href="${live}" target="_blank" rel="noopener">watch it live ↗</a>`);
  const root = bloomV11(block, bodyEl, html`
    <div class="kb-lead kb-media">
      ${video
        ? html`<video class="kb-video" controls playsinline preload="metadata" ${poster ? html`poster="${poster}"` : ''} src="${video}"></video>`
        : poster ? html`<img class="kb-video" src="${poster}" alt="">` : html`<div class="kb-video empty">no video yet</div>`}
    </div>
    ${details(html`
      ${links.length ? html`<div class="kb-links">${links}</div>` : ''}
      <div class="kb-grid">
        ${(d.outputs || []).length ? sec('outputs', html`<ul class="kb-files">${d.outputs.map((n) => {
          const u = outUrl(d, n);
          return html`<li>${u ? html`<a class="kb-a mono" href="${u}" target="_blank" rel="noopener">${n}</a>` : html`<span class="mono">${n}</span>`}</li>`;
        })}</ul>`) : ''}
        ${sec('about', kv([
          ['what', block.summary && !same(plain(block.summary), H.why) ? block.summary : ''],
          ['length', d.duration && duration(d.duration)],
          ['folder', d.path && html`<span class="mono">${d.path}</span>`],
          ['updated', ago(block.updated)],
        ]))}
      </div>
    `)}
  `);
  const v = root.querySelector('video');
  if (v) {
    // keep the shell's gestures off the transport controls
    ['pointerdown', 'mousedown', 'touchstart', 'wheel'].forEach((t) => v.addEventListener(t, (e) => e.stopPropagation(), { passive: true }));
  }
}
