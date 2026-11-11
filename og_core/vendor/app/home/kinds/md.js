// A tiny, sanitising markdown renderer. No libraries. Every character of the
// source is escaped first; only the constructs below produce tags, and links
// only survive with a safe scheme (http/https/mailto/relative).
//
// Supports: paragraphs, # headings, > quotes, - / * / 1. lists, - [ ] / - [x]
// task items, ``` fences, `code`, **bold**, *em* / _em_, [text](url), bare URLs.
import { esc, safeUrl } from './util.js';

function inline(src) {
  const slots = [];
  const keep = (h) => `\u0000${slots.push(h) - 1}\u0000`;
  let s = String(src);
  s = s.replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${esc(c)}</code>`));
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, t, u) => {
    const url = safeUrl(u);
    return url ? keep(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(t)}</a>`) : m;
  });
  s = s.replace(/\bhttps?:\/\/[^\s<>()"']+[^\s<>()"'.,;:!?]/g, (u) => keep(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a>`));
  // repo paths (media/reel/vo-you-record.md) read as code
  s = s.replace(/(^|[\s(])((?:[\w.-]+\/)+[\w.-]+\.[a-z0-9]{1,5})(?=[\s).,;:!?]|$)/gi, (_, pre, p) => pre + keep(`<code>${esc(p)}</code>`));
  s = esc(s);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(])\*([^*\s][^*\n]*)\*(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[\s(])_([^_\s][^_\n]*)_(?=[\s).,;:!?]|$)/g, '$1<em>$2</em>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[+i]);
}

export function md(src) {
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let para = [], list = null, quote = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}${list.task ? ' class="md-tasks"' : ''}>${list.items.join('')}</${list.tag}>`); list = null; } };
  const flushQuote = () => { if (quote.length) { out.push(`<blockquote>${inline(quote.join(' '))}</blockquote>`); quote = []; } };
  const flushAll = () => { flushPara(); flushList(); flushQuote(); };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;
    if (/^\s*```/.test(line)) {
      flushAll();
      const body = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      out.push(`<pre><code>${esc(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (!line.trim()) { flushAll(); continue; }
    if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
      flushAll();
      const lvl = Math.min(6, m[1].length + 3); // h4..h6 inside a card
      out.push(`<h${lvl > 6 ? 6 : lvl} class="md-h">${inline(m[2])}</h${lvl > 6 ? 6 : lvl}>`);
      continue;
    }
    if ((m = /^\s*>\s?(.*)$/.exec(line))) { flushPara(); flushList(); quote.push(m[1]); continue; }
    if ((m = /^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/.exec(line))) {
      flushPara(); flushQuote();
      if (!list || list.tag !== 'ul' || !list.task) { flushList(); list = { tag: 'ul', task: true, items: [] }; }
      const done = m[1].toLowerCase() === 'x';
      list.items.push(`<li class="${done ? 'done' : ''}"><span class="md-box" aria-hidden="true">${done ? '✓' : ''}</span><span>${inline(m[2])}</span></li>`);
      continue;
    }
    if ((m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line))) {
      flushPara(); flushQuote();
      const tag = /\d/.test(m[1]) ? 'ol' : 'ul';
      if (!list || list.tag !== tag || list.task) { flushList(); list = { tag, items: [] }; }
      list.items.push(`<li>${inline(m[2])}</li>`);
      continue;
    }
    if (list && /^\s{2,}\S/.test(line)) { // continuation of a list item
      const last = list.items.pop();
      list.items.push(last.replace(/<\/li>$/, ` ${inline(line.trim())}</li>`));
      continue;
    }
    flushList(); flushQuote();
    para.push(line.trim());
  }
  flushAll();
  return out.join('\n');
}
