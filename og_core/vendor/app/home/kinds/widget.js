// widget: the housekeeping glances (docs/unified-audit.md §5.7, lab glance.html).
// The card IS the glance: one number + one next thing, plain words. Tapping it
// blooms (the canvas FLIPs from the card — lab glance ↔ bloom) into the workable
// surface. The to-do glance is the tasklist. Email and calendar come from the user's
// own Hermes (a scheduled job it runs; board/mailcal.py): real data, an offer to set
// it up, or a plain "your hermes can't read email yet". No fabricated data, ever.
// The inbox is next actions, not a checklist (mailBloom): a row opens in place, one at a time,
// with one verb row: talk about this · later · not mine. Read-only toward the mailbox.
import { html, raw, mount, plain, dueText, dayLabel, cutWords, OG } from './util.js';

const WIDGETS = {
  todo: { icon: '☐', label: 'to-do' },
  calendar: { icon: '▦', label: 'calendar' },
  email: { icon: '✉', label: 'email' },
};

const meta = (block) => WIDGETS[block.detail?.widget] || { icon: '•', label: plain(block.title) || 'widget' };
const nextWords = (n) => {
  if (!n?.text) return '';
  return cutWords(plain(n.text).replace(/[.?!,;]+$/, ''), 4);
};

// ---------------------------------------------------------------- the glance
export function card(block, el) {
  const d = block.detail || {};
  const m = meta(block);
  const wired = !!d.wired && !d.error;
  const n = wired ? d.count : null;
  const nxt = wired ? (d.next || null) : null;
  const due = nxt?.due ? dueText(nxt.due) : null;
  const num = n == null ? '–' : String(n);
  if (d.state || isMail(d)) { digestCard(block, el, m); return; }
  mount(el, html`<div class="kg ${wired ? 'kg-wired' : 'kg-unwired'} st-${block.status === 'active' ? 'active' : 'idle'}" data-kg>
    <div class="kg-top"><i class="kf-ico" aria-hidden="true">${m.icon}</i><span class="kg-k">${m.label}</span></div>
    <span class="kg-n" aria-label="${n == null ? 'no count' : n + ' open'}">${num}</span>
    <span class="kg-next">${nxt ? html`${nextWords(nxt)}<small>${due ? due.text : (wired ? 'next up' : '')}</small>`
      : html`<small>${d.error ? (d.widget === 'email' ? 'the inbox isn’t answering' : d.widget === 'calendar' ? 'the calendar isn’t answering' : 'the list isn’t answering') : (wired ? 'nothing open' : 'not wired yet')}</small>`}</span>
  </div>`);
}

// ------------------------------------------------- email + calendar from the user's own Hermes
// (app/board/mailcal.py). Three honest states: real data · an offer to set it up · can't yet.
// Plus the two in between: the first read is on its way · the last read failed (it says what).
const clock = (iso) => {
  const t = new Date(iso);
  if (!iso || String(iso).length <= 10 || isNaN(t)) return '';
  return t.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase().replace(':00', '').replace(/\s/g, '');
};
const whenWords = (n) => {
  if (!n?.due) return '';
  if (n.all_day) return `${dayLabel(n.due)}, all day`;
  const day = dayLabel(n.due);
  return (day === 'today' ? '' : day + ' ') + clock(n.due);
};
const quietWords = (d) => d.state === 'offer' ? (d.declined ? 'not watching. tap to set it up' : 'your hermes can read it. tap to set it up')
  : d.state === 'cant' ? (d.widget === 'email' ? 'your hermes can’t read email yet' : 'your hermes can’t read your calendar yet')
    : d.state === 'waiting' ? 'the first read is on its way' : (d.error || 'the last read failed');

function digestCard(block, el, m) {
  const d = block.detail || {};
  const ready = d.state === 'ready' || (!d.state && isMail(d));
  const n = ready ? d.count : null;
  const nxt = ready ? d.next : null;
  const sub = nxt ? (d.widget === 'calendar' ? whenWords(nxt) : (nxt.waiting || (n ? d.count_label : 'unread'))) : '';
  mount(el, html`<div class="kg ${ready ? 'kg-wired' : 'kg-unwired'} kg-${d.state || 'ready'} st-${block.status === 'active' ? 'active' : 'idle'}" data-kg>
    <div class="kg-top"><i class="kf-ico" aria-hidden="true">${m.icon}</i><span class="kg-k">${m.label}</span></div>
    <span class="kg-n" aria-label="${n == null ? 'no count' : `${n} ${d.count_label || ''}`}">${n == null ? '–' : String(n)}</span>
    <span class="kg-next">${nxt ? html`${cutWords(plain(nxt.text), 6)}<small>${sub}${d.as_of_text ? ` · ${d.as_of_text}` : ''}</small>`
      : html`<small>${ready ? `${d.empty || 'nothing waiting'}${d.as_of_text ? ` · ${d.as_of_text}` : ''}` : quietWords(d)}</small>`}</span>
  </div>`);
}

// the offer's two answers: yes creates the user's own Hermes job and runs it once; not now is remembered
export async function answerOffer(yes, { onDone } = {}) {
  try {
    const r = await OG().api.post('/api/board/mailcal', { op: yes ? 'setup' : 'not-now' });
    if (!r || r.ok === false) throw new Error((r && r.error) || 'didn’t go through');
    if (yes) OG().toast?.(r.say || 'on it.');
    OG().emit?.('action-done', { block: { id: 'widget:email' }, action: { id: yes ? 'decide' : 'not-now' }, result: r });
    await OG().refresh?.();
    if (onDone) onDone(r);
    return r;
  } catch (err) {
    OG().toast?.(`didn’t go through: ${err.message || err}`, true);
    return null;
  }
}

function digestBloom(block, bodyEl, m) {
  const d = block.detail || {};
  const stamp = d.as_of_text || '';
  if (d.state !== 'ready') {
    const line = d.state === 'offer' ? d.offer?.text : (d.error || d.note);
    mount(bodyEl, html`<div class="kb kb-widget kg-unwired">
      <section class="kb-sec"><p class="kb-note kg-say">${line}</p>
      ${d.state === 'offer' ? html`<p class="kb-note">it sets up one scheduled job in your own hermes. it only reads: it never sends, replies, archives, accepts or declines anything.</p>
        <p class="kg-offer"><button type="button" class="kb-btn" data-kg-yes>${d.offer?.yes || 'yes'}</button>
        ${d.declined ? '' : html`<button type="button" class="kb-btn kb-btn-quiet" data-kg-no>${d.offer?.no || 'not now'}</button>`}</p>` : ''}
      ${d.state === 'failed' ? html`<p class="kb-note">nothing is shown until a read works. i won’t guess.</p>` : ''}</section>
    </div>`);
    const yes = bodyEl.querySelector('[data-kg-yes]'), no = bodyEl.querySelector('[data-kg-no]');
    const lock = (on) => [yes, no].forEach((b) => { if (b) b.disabled = on; });
    if (yes) yes.onclick = async (e) => { e.stopPropagation(); lock(true); if (!await answerOffer(true)) lock(false); };
    if (no) no.onclick = async (e) => { e.stopPropagation(); lock(true); if (!await answerOffer(false)) lock(false); };
    return;
  }
  if (isMail(d)) { mailBloom(block, bodyEl); return; }
  const rows = d.items || [];
  mount(bodyEl, html`<div class="kb kb-widget">
    <div class="kb-kicker">from your hermes · read only${stamp ? ` · ${stamp}` : ''}</div>
    ${d.last_error ? html`<p class="kb-note">${d.last_error}. this is the read before it.</p>` : ''}
    <section class="kb-sec kg-rows">${rows.length ? rows.map((it) => d.widget === 'email'
      ? html`<div class="kg-row kg-row-ro"><span class="kg-t"><b>${plain(it.from)}</b> ${cutWords(plain(it.why || it.text), 14)}</span><span class="kg-m">${it.needs_reply ? 'needs a reply' : ''}</span></div>`
      : html`<div class="kg-row kg-row-ro"><span class="kg-t">${cutWords(plain(it.text), 14)}</span><span class="kg-m">${whenWords({ due: it.due, all_day: String(it.due || '').length <= 10 })}</span></div>`)
      : html`<p class="kb-note">${d.empty || 'nothing here.'}</p>`}</section>
  </div>`);
}

// ------------------------------------------------- the inbox as next actions (not a checklist)
// Both mail paths (the Hermes digest and the direct glance) carry the same rows (board/mailcal.py
// mail_detail): people first, oldest first. A row is a button that opens in place: who, how long
// it has waited, the subject, the snippet the source gave. ONE row is open at a time. The open row
// has one verb row, and each verb is a judgment in the ledger (POST /api/board/mailcal):
//   talk about this  a thread seeded with that email (the column, as a chat card's verb does)
//   later            hidden until tomorrow
//   not mine         hidden for good
// Nothing here touches the mailbox: no archive, no mark-read, no send.
const isMail = (d) => d?.widget === 'email' && d.mail === true && Array.isArray(d.items);
let openMail = null;                       // the id of the one open row (survives a repaint)
const gone = new Set();                    // rows he just judged away, until the board catches up
const safeId = (id) => String(id).replace(/[^A-Za-z0-9_.-]+/g, '-').slice(0, 60);
const mailRows = (block) => (block.detail?.items || []).filter((it) => !gone.has(String(it.id)));
const openRow = (block) => mailRows(block).find((it) => String(it.id) === openMail) || null;

// the thread's subject: the email itself, as a block the surface can carry (from, subject, snippet)
const mailThread = (it) => ({
  id: 'mail:' + safeId(it.id), kind: 'note', title: plain(it.subject) || 'an email', status: 'idle', tags: ['email'],
  summary: `email from ${plain(it.from)}${it.snippet ? ': ' + plain(it.snippet) : ''}`,
  detail: { from: it.from, subject: it.subject, snippet: it.snippet || '', received: it.received || null, waiting: it.waiting || '', readonly: true },
});
const judgeMail = (op, it) => OG().api.post('/api/board/mailcal', { op, id: it.id, from: it.from, subject: it.subject });

async function talkAbout(it) {
  judgeMail('talk', it).catch(() => {});                       // the ledger never blocks the gesture
  const card = OG().board?.byId?.['note:mail-' + safeId(it.id)];   // the digest already made it a card: that card's thread
  if (card) { OG().focus(card.id); setTimeout(() => OG().talk?.focus?.(), 360); return; }
  if (!OG().column) { OG().toast?.('the talk bar isn’t loaded yet', true); return; }
  OG().unfocus?.();
  await OG().column.open(mailThread(it), { why: 'about this email' });
  OG().talk?.focus?.();
}

async function putAway(op, it, root) {
  const row = root?.querySelector(`[data-mail="${CSS.escape(String(it.id))}"]`);
  row?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    const r = await judgeMail(op, it);
    if (r && r.ok === false) throw new Error(r.error || 'didn’t go through');
    gone.add(String(it.id));
    if (openMail === String(it.id)) openMail = null;
    row?.remove();
    syncMail(root);
    OG().toast?.(op === 'later' ? 'later. it comes back tomorrow.' : 'not yours. it won’t come back.');
    OG().refresh?.();
  } catch (err) {
    row?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    OG().toast?.(`didn’t go through: ${err.message || err}`, true);
  }
}

// one row open at a time; the bottom bar's main verb follows it (talk about this, not look)
function syncMail(root) {
  if (!root) return;
  root.querySelectorAll('[data-mail]').forEach((row) => {
    const on = row.dataset.mail === openMail;
    row.classList.toggle('kg-mail-open', on);
    row.querySelector('.kg-mail-h').setAttribute('aria-expanded', String(on));
    row.querySelector('.kg-mail-b').hidden = !on;
  });
  const left = root.querySelectorAll('[data-mail]').length;
  const none = root.querySelector('[data-mail-none]');
  if (none) none.hidden = left > 0;
  const auto = root.querySelector('.kg-mail-sep');
  if (auto && !root.querySelector('.kg-mail-auto')) auto.remove();
  const slot = root.closest('.og-bloom')?.querySelector('.fx-slot[data-action="primary"] .fx-slot-l');
  if (slot) slot.textContent = openMail ? 'talk about this' : 'open';
}
function setOpen(root, id) {
  openMail = id && openMail !== String(id) ? String(id) : null;
  syncMail(root);
}

function mailBloom(block, bodyEl) {
  const d = block.detail || {};
  const rows = mailRows(block);
  if (openMail && !rows.some((it) => String(it.id) === openMail)) openMail = null;
  const firstAuto = rows.findIndex((it) => !it.person);
  const src = d.state ? 'from your hermes · ' : '';
  mount(bodyEl, html`<div class="kb kb-widget kb-mail" data-mail-list>
    <div class="kb-kicker">${src}read only${d.as_of_text ? ` · ${d.as_of_text}` : ''}</div>
    ${d.last_error ? html`<p class="kb-note">${d.last_error}. this is the read before it.</p>` : ''}
    <section class="kb-sec kg-rows kg-mails">${rows.map((it, i) => html`
      ${i === firstAuto ? html`<div class="kg-mail-sep">${firstAuto ? 'not from a person' : 'nothing from a person. the rest:'}</div>` : ''}
      <div class="kg-mail ${it.person ? '' : 'kg-mail-auto'}" data-mail="${it.id}">
        <button type="button" class="kg-mail-h" aria-expanded="false">
          <span class="kg-mail-from">${plain(it.from)}</span>
          <span class="kg-mail-subj">${plain(it.subject || it.text)}</span>
          <span class="kg-m">${it.waiting || ''}</span>
        </button>
        <div class="kg-mail-b" hidden>
          <p class="kg-mail-who">from <b>${plain(it.from)}</b>${it.waiting ? html` · <span class="kg-mail-wait">${it.waiting}</span>` : ''}</p>
          <p class="kg-mail-s">${plain(it.subject || it.text)}</p>
          ${it.snippet ? html`<p class="kg-mail-snip">${plain(it.snippet)}</p>` : ''}
          <p class="kg-mail-verbs">
            <button type="button" class="kb-btn" data-mail-verb="talk">talk about this</button>
            <button type="button" class="kb-btn kb-btn-quiet" data-mail-verb="later">later</button>
            <button type="button" class="kb-btn kb-btn-quiet" data-mail-verb="not-mine">not mine</button>
          </p>
        </div>
      </div>`)}
      <p class="kb-note" data-mail-none ${rows.length ? raw('hidden') : ''}>${d.empty || 'nothing waiting on you.'}</p>
    </section>
  </div>`);
  const root = bodyEl.firstElementChild;
  root.addEventListener('click', (e) => {
    const row = e.target.closest('[data-mail]');
    if (!row) return;
    const it = mailRows(block).find((x) => String(x.id) === row.dataset.mail);
    const verb = e.target.closest('[data-mail-verb]');
    if (!it) return;
    e.stopPropagation();
    if (verb) { if (verb.dataset.mailVerb === 'talk') talkAbout(it); else putAway(verb.dataset.mailVerb, it, root); return; }
    if (e.target.closest('.kg-mail-h')) setOpen(root, it.id);
  });
  syncMail(root);
  queueMicrotask(() => syncMail(root));     // the bar is built after the body: set its verb once it exists
  setTimeout(() => syncMail(root), 0);
}

// Esc closes the open row first (then Esc is "back" as everywhere else). On window, capture: the
// column's and the shell's own Esc (document) would otherwise walk back out of the inbox first.
window.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !openMail) return;
  const root = document.querySelector('.og-bloom [data-mail-list]');
  const a = document.activeElement;
  if (!root || (a && /^(INPUT|TEXTAREA)$/.test(a.tagName)) || document.querySelector('.og-ask-scrim')) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const was = root.querySelector('.kg-mail-open .kg-mail-h');
  setOpen(root, null);
  was?.focus();
}, true);

// the bottom bar stays honest: an open email's main verb is `talk about this`, and `later` puts
// THAT email off; with no row open the verb opens the one that has waited longest
export function grammar(block) {
  const d = block?.detail || {};
  if (!isMail(d) || d.error || (d.state && d.state !== 'ready') || !mailRows(block).length) return {};
  const root = () => document.querySelector('.og-bloom [data-mail-list]');
  return {
    primary: { label: openRow(block) ? 'talk about this' : 'open', run: () => {
      const it = openRow(block);
      if (it) return talkAbout(it);
      const first = mailRows(block)[0];
      if (first) { setOpen(root(), first.id); root()?.querySelector('.kg-mail-open .kg-mail-h')?.focus(); }
      return null;
    } },
    enter: null,
    later: { label: 'later', run: (x) => { const it = openRow(block); return it ? putAway('later', it, root()) : OG().park(x.id); } },
  };
}

// ------------------------------------------------------- the workable surface
function todoRows(root, block, rows) {
  const sub = root.querySelector('[data-kg-sub]');
  const list = root.querySelector('[data-kg-rows]');
  let open = block.detail?.count ?? rows.length;
  rows.forEach((it) => {
    const row = document.createElement('div');
    row.className = 'kg-row';
    mount(row, html`<button type="button" class="kg-box" aria-label="mark done"></button>
      <span class="kg-t">${cutWords(plain(it.text), 18)}</span>
      <span class="kg-m">${it.due ? `due ${dayLabel(it.due)}` : ''}</span>`);
    const box = row.querySelector('.kg-box');
    const tick = async (e) => {
      e.stopPropagation();
      if (box.disabled) return;
      box.disabled = true;
      try {
        const r = await OG().api.post('/api/board/task', { id: it.id, op: 'toggle' });
        if (r && r.ok === false) throw new Error(r.error || 'didn’t go through');
        row.classList.add('done'); box.textContent = '✓';
        open = Math.max(0, open - 1);
        if (sub) { sub.hidden = false; sub.textContent = open === 1 ? 'one left. it saves to the list.' : `${open} left. it saves to the list.`; }
        OG().emit?.('action-done', { block, action: { id: 'done' }, result: r });
        OG().refresh?.();
      } catch (err) {
        box.disabled = false;
        OG().toast?.(`didn’t go through: ${err.message || err}`, true);
      }
    };
    row.addEventListener('click', tick);
    list.append(row);
  });
}

export function bloom(block, bodyEl) {
  const d = block.detail || {};
  const m = meta(block);
  if (d.state) { digestBloom(block, bodyEl, m); return; }
  if (isMail(d) && !d.error) { mailBloom(block, bodyEl); return; }
  if (!d.wired || d.error) {
    const note = d.error ? 'the list isn’t answering right now. it’ll be back.'
      : (d.note || 'no live feed for this one yet.');
    mount(bodyEl, html`<div class="kb kb-widget kg-unwired">
      <section class="kb-sec"><p class="kb-note">${note}</p>
      <p class="kb-note">the glance stays here, quiet, until there's a real source to wire. nothing is made up in the meantime.</p></section>
      ${block.actions?.find((a) => a.href) ? html`<section class="kb-sec">
        <a class="kb-btn" href="${block.actions.find((a) => a.href).href}" target="_blank" rel="noopener">open ${m.label} where it lives ↗</a></section>` : ''}
    </div>`);
    return;
  }
  const rows = (d.items || []).filter((it) => !it.done);
  mount(bodyEl, html`<div class="kb kb-widget">
    <div class="kb-kicker">housekeeping</div>
    <p class="kb-note" data-kg-sub hidden></p>
    <section class="kb-sec kg-rows" data-kg-rows></section>
    ${block.actions?.length ? html`<section class="kb-sec">
      ${block.actions.map((a) => a.href ? html`<a class="kb-btn" href="${a.href}" target="_blank" rel="noopener">${a.label} ↗</a>` : '')}
    </section>` : ''}
  </div>`);
  const root = bodyEl.firstElementChild;
  todoRows(root, block, rows.length ? rows : (d.items || []));
}
