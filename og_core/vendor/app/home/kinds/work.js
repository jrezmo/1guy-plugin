// project · goal · note: the brain's blocks.
// project bloom = headline, why, its pieces on a lane ending in the empty "next"
// slot pulsing amber (the-song's bridge slot); goal, horizon, links in 'details'.
import { html, face, dots, sec, kv, miniCard, projectMeta, ago, tagChips, plain, same,
  headline, isBriefing, briefFace, details, bloomV11 } from './util.js';

const okColor = (c) => (/^#[0-9a-f]{3,8}$/i.test(c || '') ? c : '');
const colorOf = (block) => okColor(projectMeta(block.id)?.color || projectMeta(block.project)?.color);
const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const tint = (block, el) => { const c = colorOf(block); if (c) el.style.setProperty('--pc', c); };

// ---------- project ----------
export const project = {
  icon: '◆',
  card(block, el) {
    const d = block.detail || {};
    const kids = d.children || [];
    if (isBriefing(el)) {
      briefFace(block, el, { icon: project.icon, kicker: 'project', touch: html`<span class="kf-st">${kids.length ? count(kids.length, 'piece', 'pieces') : ''}</span>` });
      tint(block, el.querySelector('[data-kf]'));
      return;
    }
    const H = headline(block);
    const goal = plain(d.goal);
    face(block, el, {
      icon: project.icon, kindLabel: 'project',
      title: H.title, line: H.why, prose: true,
      extra: goal && !same(goal, H.why) && html`<span class="kf-goal">${goal}</span>`,
      foot: dots(kids.length ? count(kids.length, 'piece', 'pieces') : 'no pieces yet', plain(d.next) && html`<span class="kf-next">next: ${plain(d.next)}</span>`),
    });
    tint(block, el.querySelector('[data-kf]'));
  },
  bloom(block, bodyEl) {
    const d = block.detail || {};
    const H = headline(block);
    const kids = d.children || [];
    const goal = d.goal && !same(plain(d.goal), H.why) ? d.goal : '';
    const root = bloomV11(block, bodyEl, html`
      <section class="kb-sec">
        <h4 class="kb-h">the pieces, and what's next</h4>
        <div class="kb-lane">
          ${kids.map((id) => miniCard(id))}
          <div class="kb-slot ${d.next ? 'next' : ''}"><span class="sn">next</span><span class="kb-slot-t">${d.next || 'nothing staged'}</span></div>
        </div>
      </section>
      ${details(html`
        ${sec('the point', kv([
          ['goal', goal || ''],
          ['horizon', d.horizon && html`<span class="kb-chip">${d.horizon}</span>`],
          ['summary', block.summary && !same(plain(block.summary), H.why) ? block.summary : ''],
          ['touched', ago(block.updated)],
        ]))}
      `)}
    `);
    tint(block, root);
  },
};

// ---------- goal ----------
function evidenceItem(e) {
  if (e && typeof e === 'object') {
    return html`<li><span class="kb-src">${e.type || 'note'}</span>${e.quote ? html`<q>${e.quote}</q>` : ''}${e.ref ? html` <span class="mono muted">${e.ref}</span>` : ''}</li>`;
  }
  return html`<li>${e}</li>`;
}
function goalH(block) {
  const H = headline(block);
  const w = plain(block.detail?.why);
  if (!block.headline?.why && w && !same(w, H.title)) H.why = w;
  return H;
}
export const goal = {
  icon: '◎',
  card(block, el) {
    const d = block.detail || {};
    const n = (block.links || []).length;
    if (isBriefing(el)) {
      briefFace({ ...block, headline: { ...(block.headline || {}), why: goalH(block).why } }, el, { icon: goal.icon, kicker: 'a goal' });
      return;
    }
    const H = goalH(block);
    face(block, el, {
      icon: goal.icon, kindLabel: 'goal',
      title: H.title, line: H.why, prose: true,
      foot: dots(d.horizon && plain(d.horizon), n ? `${count(n, 'thing', 'things')} feeding it` : 'nothing feeding it yet'),
    });
  },
  bloom(block, bodyEl) {
    const d = block.detail || {};
    const H = goalH(block);
    const ev = d.evidence || [];
    bloomV11(block, bodyEl, html`
      ${(block.links || []).length ? '' : html`<p class="kb-callout">nothing feeds it yet. that's the gap.</p>`}
      ${details(html`
        ${sec('how it\'s going', ev.length ? html`<ul class="kb-evidence">${ev.map(evidenceItem)}</ul>` : html`<p class="muted">no signs yet</p>`)}
        ${sec('about', kv([
          ['horizon', d.horizon && html`<span class="kb-chip">${d.horizon}</span>`],
          ['summary', block.summary && !same(plain(block.summary), H.why) ? block.summary : ''],
          ['why, as written', d.why && !same(plain(d.why), H.why) ? d.why : ''],
        ]), 'kb-quiet')}
      `)}
    `);
  },
};

// ---------- note ----------
export const note = {
  icon: '¶',
  card(block, el) {
    const d = block.detail || {};
    const lines = String(d.text || block.summary || '').split('\n').map((s) => s.trim()).filter(Boolean);
    if (isBriefing(el)) { briefFace(block, el, { icon: note.icon, kicker: 'a note' }); return; }
    const H = headline(block);
    face(block, el, {
      icon: note.icon, kindLabel: 'note',
      title: H.title,
      line: H.has ? H.why : lines[0] || H.why,
      extra: lines.length > 1 ? html`<span class="kf-verse">${lines.slice(H.has ? 0 : 1, 4).join('\n')}</span>` : '',
      foot: block.updated && `written ${ago(block.updated)}`,
    });
  },
  bloom(block, bodyEl) {
    const d = block.detail || {};
    const text = d.text || block.summary || '';
    bloomV11(block, bodyEl, html`
      <div class="kb-lead"><div class="kb-paper">${text}</div></div>
      ${details(html`
        ${sec('about', kv([
          ['written', ago(block.updated)],
          ['kept in', block.source?.ref && html`<span class="mono">${block.source.ref}</span>`],
          ['tags', (block.tags || []).length && tagChips(block.tags)],
        ]))}
      `)}
    `);
  },
};
