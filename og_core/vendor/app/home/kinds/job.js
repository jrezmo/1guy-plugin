// job: a cron job.
// Face  = health light + headline + why, schedule in words, next run.
// Bloom = headline, why; if it broke: what broke in one plain sentence plus the
//         last output; otherwise when it runs and the last output. Raw cron,
//         ids and the numbers fold into 'details'.
import { html, face, dots, sec, kv, ago, until, clock, dayLabel, cronWords,
  headline, isBriefing, briefFace, details, bloomV11 } from './util.js';

export const icon = '↻';

export function health(d, block) {
  const s = String(d.last_status || '').toLowerCase();
  if (block.status === 'failed' || /err|fail|timeout/.test(s)) return 'bad';
  if (/^(ok|success|done|0)$/.test(s)) return 'ok';
  return 'unknown';
}
const HEALTH_WORD = { ok: 'healthy', bad: 'failing', unknown: 'unknown' };

// The error, in words a person uses.
export function cause(excerpt) {
  const s = String(excerpt || '').toLowerCase();
  if (/invalid_grant|expired|revoked|reauth|re-auth|token/.test(s)) return 'its login expired';
  if (/timed? ?out|timeout|deadline/.test(s)) return 'it timed out';
  if (/\b(401|403)\b|unauthori[sz]ed|forbidden|permission denied/.test(s)) return 'it was refused access';
  if (/enospc|no space|disk full/.test(s)) return 'the disk filled up';
  if (/rate.?limit|\b429\b|quota/.test(s)) return 'it hit a rate limit';
  if (/connection|network|dns|resolve|unreachable/.test(s)) return "it couldn't reach the network";
  if (/not found|no such file|enoent|\b404\b/.test(s)) return 'something it needs has gone missing';
  return 'it errored';
}
// what fixes it, plainly; whether waiting helps
export function fixOf(excerpt) {
  const c = cause(excerpt);
  if (c === 'its login expired') return { text: "it needs signing back in. it won't fix itself.", waits: false };
  if (c === 'it was refused access') return { text: "it needs its access sorted. it won't fix itself.", waits: false };
  if (c === 'the disk filled up') return { text: 'it needs some space freed up.', waits: false };
  if (c === 'something it needs has gone missing') return { text: 'it needs a look at what moved.', waits: false };
  if (c === 'it errored') return { text: 'it needs a look.', waits: false };
  return { text: 'it may pass on the next run.', waits: true };
}
const norm = (x) => String(x || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
export function brokeSentence(d) {
  const at = d.last_run ? ` at ${clock(d.last_run)} ${dayLabel(d.last_run)}` : '';
  const next = d.next_run && fixOf(d.last_output_excerpt).waits ? ` it tries again ${until(d.next_run)}.` : '';
  return `${cause(d.last_output_excerpt)}, so the run${at} failed.${next}`;
}
// fallback headline why for a job: plain words instead of the raw summary
function jobH(block) {
  const d = block.detail || {};
  const H = headline(block);
  if (!H.has && health(d, block) === 'bad' && !block.headline?.why) H.why = brokeSentence(d);
  return H;
}

export function card(block, el) {
  const d = block.detail || {};
  const h = health(d, block);
  const light = html`<span class="kf-light h-${h}" title="${HEALTH_WORD[h]}" aria-hidden="true"></span>`;
  if (isBriefing(el)) {
    const orig = block.headline;
    if (!orig?.why && h === 'bad') block = { ...block, headline: { ...(orig || {}), why: brokeSentence(d) } };
    briefFace(block, el, { icon, kicker: h === 'bad' ? 'this broke' : 'a scheduled job', touch: html`<span class="kf-st h-${h}">${light}<span>${HEALTH_WORD[h]}</span></span>` });
    return;
  }
  const H = jobH(block);
  face(block, el, {
    icon, kindLabel: 'job',
    title: H.title,
    // top right carries the rhythm; the light carries the health
    status: html`<span class="kf-st h-${h}"><span>${cronWords(d.schedule, true)}</span></span>`,
    lead: light,
    line: H.why, prose: h === 'bad',
    foot: dots(d.next_run && `next ${until(d.next_run)}`, h !== 'unknown' && `last run: ${h === 'ok' ? 'ok' : 'failed'}`),
  });
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const h = health(d, block);
  const H = jobH(block);
  const words = cronWords(d.schedule);
  // no headline: the why already is the plain 'what broke'; with one, add just the when
  // (the headline's why already says what broke and when; only add a retry if waiting will help)
  const fix = fixOf(d.last_output_excerpt);
  const broke = fix.waits && d.next_run && !/tries again/.test(H.why) ? `it tries again ${until(d.next_run)}.` : '';
  // has the why already named the cause? (the no-headline why is built from it)
  const ck = cause(d.last_output_excerpt).split(' ').slice(-1)[0];
  const causeSaid = norm(H.why).includes(norm(cause(d.last_output_excerpt))) || norm(H.why).includes(ck);
  const out = d.last_output_excerpt
    ? html`<pre class="kb-out mono h-${h}">${d.last_output_excerpt}</pre>`
    : html`<p class="muted">it didn't say anything</p>`;
  bloomV11(block, bodyEl, html`
    ${h === 'bad'
      ? html`<section class="kb-sec kb-broke">
          <h4 class="kb-h">what broke</h4>
          <p class="kb-plain">${causeSaid ? '' : html`<b>${cause(d.last_output_excerpt)}.</b> `}${fix.text}${broke ? ` ${broke}` : ''}</p>
          <p class="kb-cap2">what it said</p>${out}
        </section>`
      : html`<section class="kb-sec">
          <p class="kb-plain"><span class="kf-light h-${h}" aria-hidden="true"></span> runs ${words}${d.next_run ? html`, next ${until(d.next_run)}` : ''}.</p>
          ${d.last_run ? html`<p class="kb-cap2">last run, ${ago(d.last_run)}</p>` : ''}${out}
        </section>`}
    ${details(html`
      <div class="kb-stats n3">
        <div class="kb-stat"><span class="kb-num sm h-${h}"><i class="kf-light h-${h}"></i>${HEALTH_WORD[h]}</span><span class="kb-cap">last status: ${d.last_status || '–'}</span></div>
        <div class="kb-stat"><span class="kb-num sm">${d.last_run ? `${clock(d.last_run)} ${dayLabel(d.last_run)}` : '–'}</span><span class="kb-cap">last run${d.last_run ? ` · ${ago(d.last_run)}` : ''}</span></div>
        <div class="kb-stat"><span class="kb-num sm">${d.next_run ? `${clock(d.next_run)} ${dayLabel(d.next_run)}` : '–'}</span><span class="kb-cap">next run${d.next_run ? ` · ${until(d.next_run)}` : ''}</span></div>
      </div>
      ${sec('schedule', html`<p>${words}</p>${words !== d.schedule ? html`<code class="kb-code">${d.schedule}</code>` : ''}`)}
      ${sec('ids', kv([['job', d.job_id && html`<span class="mono">${d.job_id}</span>`], ['source', block.source?.type]]), 'kb-quiet')}
    `)}
  `);
}
