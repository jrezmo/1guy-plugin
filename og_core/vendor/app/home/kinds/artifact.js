// artifact: something the crew made for the user — a doc, page, report, cut, or data
// file registered in ~/.hermes/1guy/artifacts.jsonl. The card says what it is
// and when; the bloom is one big "open it" link, plus the origin: which session
// made it (tasklist's ↤ convention — a local session deep-links in).
import { html, face, dots, ago, plain, same, headline, isBriefing, briefFace,
  details, sec, kv, bloomV11, safeUrl } from './util.js';

export const icon = '✦';

function openUrl(block) {
  const u = String(block.source?.url || '').trim();
  if (!u || u.startsWith('~') || u.startsWith('/home/')) return '';
  return safeUrl(u);   // never a javascript: (or any other scheme) link from board data
}

// tasklist-style origin: "↤ label", deep-linking when the session lives here.
// A face never carries a link (focus.js owns its one verb), so there it is plain text.
export function originEl(block, linked = true) {
  const o = (block.detail || {}).origin;
  if (!o || !o.label) return '';
  if (linked && o.local && o.session_id) {
    return html`<a class="kb-chip kb-a kf-origin" href="hermes://open/${o.session_id}"
      title="open the ${plain(o.label)} session">↤ ${plain(o.label)}</a>`;
  }
  return html`<span class="kb-chip kf-origin" title="from the ${plain(o.label)} thread">↤ ${plain(o.label)}</span>`;
}

// A brief (board/brief.py) carries its own words: the short markdown it was written in, shown
// as plain headings, bullets and sentences. Only those three shapes; everything is escaped.
function briefBody(md) {
  const out = [];
  let list = [];
  const bold = (s) => html`${String(s).split(/\*\*(.+?)\*\*/).map((p, i) => (i % 2 ? html`<b>${p}</b>` : p.replace(/[*_`]/g, '')))}`;
  const flush = () => { if (list.length) { out.push(html`<ul class="kb-brief-ul">${list}</ul>`); list = []; } };
  for (const raw of String(md || '').split('\n')) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(line), li = /^[-*•]\s+(.*)$/.exec(line);
    if (li) { list.push(html`<li>${bold(li[1])}</li>`); continue; }
    flush();
    if (h) { if (h[1].length > 1) out.push(html`<h4 class="kb-h">${h[2].replace(/[*_`]/g, '')}</h4>`); continue; }   // the title is the card's own
    out.push(html`<p>${bold(line)}</p>`);
  }
  flush();
  return out;
}

export function card(block, el) {
  const d = block.detail || {};
  const H = headline(block);
  const label = plain(d.kind) || 'made for you';
  if (isBriefing(el)) {
    briefFace(block, el, { icon, kicker: 'made for you', touch: html`<span class="kf-st">open ↗</span>` });
    return;
  }
  face(block, el, {
    icon, kindLabel: label,
    title: H.title,
    line: H.why, prose: true,
    foot: dots(originEl(block, false), ago(block.updated)),
  });
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const H = headline(block);
  const url = openUrl(block);
  const path = plain(d.path);
  const root = bloomV11(block, bodyEl, html`
    ${d.kind === 'brief' ? html`<section class="kb-sec kb-brief">${d.body ? briefBody(d.body) : html`<p class="kb-note">the brief's file isn't here any more.</p>`}</section>` : ''}
    <section class="kb-sec">
      ${url ? html`<p><a class="kb-chip kb-a" href="${url}" target="_blank" rel="noopener">open it ↗</a> ${originEl(block)}</p>`
            : path && d.kind !== 'brief' ? html`<p class="kb-note">${path}</p>` : ''}
      ${block.summary && !same(plain(block.summary), H.why) ? html`<p>${plain(block.summary)}</p>` : ''}
    </section>
    ${details(sec('where it came from', kv([
      ['origin', originEl(block)],
      ['made by', plain(d.by)],
      ['kind', plain(d.kind)],
      ['when', ago(block.updated)],
      ['file', !url && path ? path : ''],
    ])))}
  `);
  return root;
}
