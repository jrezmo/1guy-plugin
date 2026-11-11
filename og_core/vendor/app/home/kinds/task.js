// task: a tasklist item.
// Face  = the one guy's headline + why and the due countdown (top right).
// Bloom = content only: the question / when it's due, the raw text, where it came from,
//         tags. focus.js owns the header, the connections and every control.
import { html, face, dots, sec, kv, boardBlock,
  dueText, dayLabel, ago, tagChips, statusLabel, tok,
  headline, isBriefing, briefFace, details, bloomV11, same, plain, questionOf } from './util.js';

export const icon = '○';

const isQuestion = (d) => /question|issue/i.test(d.section || '');
const originText = (o) => (o?.label ? `from ${plain(o.label).replace(/[-_]+/g, ' ')}` : '');
const PRIO_WORD = { 1: 'top priority', 2: 'soon-ish', 3: 'whenever' };

function dueChip(block) {
  const due = block.due ? dueText(block.due) : null;
  return due ? html`<span class="kf-st due-st"><span class="due ${due.tone}">${due.text}</span></span>` : null;
}

export function card(block, el) {
  const d = block.detail || {};
  const H = headline(block);
  if (isBriefing(el)) {
    briefFace(block, el, { icon, kicker: isQuestion(d) ? 'a question for you' : 'to do', touch: dueChip(block) || '' });
    return;
  }
  const pr = block.priority;
  const sub = originText(d.origin) || (['active', 'idle'].includes(block.status) ? '' : statusLabel(block.status));
  face(block, el, {
    icon, kindLabel: isQuestion(d) ? 'open question' : 'task',
    title: H.title,
    status: dueChip(block) ?? undefined,
    // no origin and nothing to say but 'moving'/'idle': say nothing (the world line says what's moving when something is)
    line: H.why || (sub ? html`<span class="origin">${sub}</span>` : ''),
    prose: !!H.why,
    foot: dots(PRIO_WORD[pr] && html`<span class="prio p${tok(pr)}"><i class="pdot"></i>${PRIO_WORD[pr]}</span>`,
      block.updated && `touched ${ago(block.updated)}`),
  });
}

function originSession(o) {
  if (!o?.session_id) return '';
  if (o.session_id === 'cron') return html`<span class="muted">a scheduled job</span>`;
  const b = boardBlock(`session:${o.session_id}`);
  if (b) return html`<span>${plain(b.title) || b.title}</span>`;
  return html`<span class="mono">${o.session_id}</span>`;
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const H = headline(block);
  const text = d.text || block.title || '';
  const due = block.due ? dueText(block.due) : null;
  // no headline ask, but the raw title ends in a question ("… — keep or drop?"): say it plainly
  const q = !H.ask && questionOf(block.title);
  const showQ = q && !same(q, H.why) && !same(q, H.title);
  const when = due || block.priority === 1;
  const showText = !(same(text, H.title) || same(plain(text), H.full));
  bloomV11(block, bodyEl, html`
    ${showQ ? html`<p class="kb-callout">${q}</p>` : ''}
    ${!H.ask && when ? html`<div class="kb-when">
      ${due ? html`<span class="kb-pill"><span class="due ${due.tone}">${due.text}</span>${same(dayLabel(block.due), due.text.replace(/^due /, '')) ? '' : html`<span class="muted"> · ${dayLabel(block.due)}</span>`}</span>` : ''}
      ${block.priority === 1 ? html`<span class="kb-pill"><span class="prio p1"><i class="pdot"></i>top priority</span></span>` : ''}
    </div>` : ''}
    ${details(html`
      ${showText ? sec('as written', html`<p class="kb-raw">${text}</p>`) : ''}
      <div class="kb-grid">
        ${sec('where it came from', html`${kv([
          ['from', d.origin?.label],
          ['session', originSession(d.origin)],
          ['section', d.section],
        ])}${d.handoff_hint ? html`<p class="kb-note">a work surface ${d.handoff_hint}</p>` : ''}`)}
        ${sec('the rest', kv([
          ['due', due ? html`<span class="due ${due.tone}">${due.text}</span> <span class="muted">${dayLabel(block.due)}</span>` : html`<span class="muted">no date</span>`],
          ['priority', block.priority ? html`<span class="prio p${tok(block.priority)}"><i class="pdot"></i>p${block.priority}</span>` : html`<span class="muted">none</span>`],
          ['tags', (block.tags || []).length ? tagChips(block.tags) : html`<span class="muted">none</span>`],
          ['status', statusLabel(block.status)],
          ['touched', ago(block.updated)],
        ]))}
      </div>
    `)}
  `);
}
