// session: a live Hermes conversation.
// Face  = headline + why, a live pulse when it moved in the last 15 min, the user's last line.
// Bloom = headline, why, where it left off (the user's last line, the reply) and the way
//         back in. Numbers, model, ids fold into 'details'.
import { html, face, dots, sec, kv, ago, clock, dayLabel, minutesSince, tok, plain,
  headline, isBriefing, briefFace, details, bloomV11 } from './util.js';

export const icon = '◉';
const LIVE_MIN = 15;

export function modelShort(m) {
  let s = String(m || '').split('/').pop();
  s = s.replace(/^claude-/, '').replace(/-(\d+)-(\d+)$/, ' $1.$2');
  return s;
}
const isLive = (block) => minutesSince(block.updated) < LIVE_MIN;

function statusTouch(block) {
  if (block.status === 'wrapped')
    return html`<span class="kf-st st-wrapped"><i class="sdot"></i><span>wrapped</span></span>`;
  return isLive(block)
    ? html`<span class="kf-st live"><i class="pulse"></i><span>live</span></span>`
    : html`<span class="kf-st st-${tok(block.status)}"><i class="sdot"></i><span>${ago(block.updated)}</span></span>`;
}

export function card(block, el) {
  const d = block.detail || {};
  const live = isLive(block);
  if (isBriefing(el)) {
    briefFace(block, el, { icon, kicker: 'a conversation', touch: statusTouch(block) });
    if (live) el.classList.add('kc-live');
    return;
  }
  const H = headline(block);
  const n = d.message_count;
  const lastJ = plain(d.last_user);
  const wrapped = block.status === 'wrapped' ? plain(block.summary || d.outcome) : null;
  // a chat with no headline of its own has no why here: the stock "idle · N messages · last: …" repeats the chip,
  // the count and the quote below
  const why = /^(live|idle) · \d+ messages/.test(plain(H.why || '')) ? '' : H.why;
  const quote = html`<span class="kf-quote"><b>you</b> ${lastJ}</span>`;
  const meta = dots(n != null && `${n} messages`, d.source && `in ${plain(d.source)}`);
  face(block, el, {
    icon, kindLabel: 'session', status: statusTouch(block),
    title: H.title,
    // no headline of its own: the last thing he said IS the line (every size shows it), the counts go in the foot
    line: wrapped || why || (lastJ ? quote : meta),
    prose: !!(wrapped || why),
    extra: (wrapped || why) && lastJ ? quote : '',
    foot: (wrapped || why || lastJ) ? meta : '',
  });
  if (live) el.classList.add('kc-live');
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const live = isLive(block);
  bloomV11(block, bodyEl, html`
    ${sec(live ? 'where it is now' : 'where it left off', html`<div class="kb-thread">
      ${d.last_user ? html`<div class="kb-msg j"><span class="who">you</span><p>${d.last_user}</p></div>` : ''}
      ${d.last_assistant ? html`<div class="kb-msg a"><span class="who">reply</span><p>${d.last_assistant}</p></div>` : ''}
      ${!d.last_user && !d.last_assistant ? html`<p class="muted">no messages to show</p>` : ''}
    </div>`)}
    ${details(html`
      <div class="kb-stats">
        <div class="kb-stat"><span class="kb-num">${d.message_count ?? '–'}</span><span class="kb-cap">messages</span></div>
        <div class="kb-stat">${live ? html`<span class="kb-num live"><i class="pulse"></i>live</span>` : html`<span class="kb-num sm">${ago(block.updated)}</span>`}<span class="kb-cap">${live ? `moved ${ago(block.updated)}` : 'last moved'}</span></div>
        <div class="kb-stat"><span class="kb-num sm">${modelShort(d.model) || '–'}</span><span class="kb-cap">model</span></div>
        <div class="kb-stat"><span class="kb-num sm">${d.source || '–'}</span><span class="kb-cap">started ${dayLabel(d.started)} ${clock(d.started)}</span></div>
      </div>
      ${sec('ids', html`${kv([['session', d.session_id && html`<span class="mono">${d.session_id}</span>`]])}${d.open_in_desktop_hint ? html`<p class="kb-note">in the desktop app: ${d.open_in_desktop_hint}</p>` : ''}`, 'kb-quiet')}
    `)}
  `);
}
