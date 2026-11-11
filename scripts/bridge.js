// 1guy bridge: the canvas runs in a sandboxed frame with an opaque origin, so it cannot reach
// the plugin backend itself. Every same-origin request (/api/...) is handed to the host page by
// postMessage; the host answers through ctx.rest (which carries the app's own auth). The canvas
// code is unchanged: it still calls fetch('/api/board').
(() => {
  window.ONEGUY_EMBED = true;   // core.js: housed in the desktop app (pane doors on)
  // A data: document has no storage at all (reading the property throws).
  // localStorage starts from what the host remembered (ONEGUY_LOCAL, set by the loader) and
  // every change is handed back to the host to keep; sessionStorage is plain memory.
  let saveT = null;
  const memory = (seed, persist) => {
    const m = new Map(Object.entries(seed || {}));
    const changed = () => {
      if (!persist || window.ONEGUY_SAMPLE === true) return;   // the sample desk saves nothing, not even where its cards sit
      clearTimeout(saveT);
      saveT = setTimeout(() => window.parent.postMessage({ type: 'oneguy:local', data: Object.fromEntries(m) }, '*'), 400);
    };
    const api = { getItem: (k) => (m.has(String(k)) ? m.get(String(k)) : null), setItem: (k, v) => { m.set(String(k), String(v)); changed(); },
      removeItem: (k) => { m.delete(String(k)); changed(); }, clear: () => { m.clear(); changed(); }, key: (i) => [...m.keys()][i] ?? null, get length() { return m.size; } };
    return new Proxy(api, {
      get: (t, k) => (k in t ? t[k] : t.getItem(k) ?? undefined),
      set: (t, k, v) => { t.setItem(k, v); return true; },
    });
  };
  for (const name of ['localStorage', 'sessionStorage']) {
    try { void window[name].length; } catch (e) { Object.defineProperty(window, name, { value: memory(name === 'localStorage' ? window.ONEGUY_LOCAL : null, name === 'localStorage'), configurable: true }); }
  }
  // Remote eyes: this page tells the host what goes wrong in it (script errors, rejected
  // promises, requests its policy refused, backend calls that failed) and how long loading
  // took. The host files them with the plugin backend; nothing leaves the machine.
  const T0 = performance.now();
  let diagN = 0;
  const seen = new Set();
  const diag = (kind, message, where, ms) => {
    const key = kind + '|' + message + '|' + (where || '');
    if (diagN >= 30 || seen.has(key)) return;
    seen.add(key);
    diagN += 1;
    try { window.parent.postMessage({ type: 'oneguy:diag', entry: { kind, message: String(message).slice(0, 300), where: where ? String(where).slice(0, 160) : undefined, ms } }, '*'); } catch (e) { /* host gone */ }
  };
  window.addEventListener('error', (e) => diag('error', e.message || 'error', (e.filename ? 'canvas' : '') + (e.lineno ? ':' + e.lineno + ':' + (e.colno || 0) : '')));
  window.addEventListener('unhandledrejection', (e) => diag('rejection', (e.reason && (e.reason.message || e.reason)) || 'unhandled rejection'));
  document.addEventListener('securitypolicyviolation', (e) => diag('csp', (e.violatedDirective || 'policy') + ' refused ' + (e.blockedURI || 'inline'), 'line ' + (e.lineNumber || 0)));
  let firstBoard = false;
  const real = window.fetch ? window.fetch.bind(window) : null;
  const waiting = new Map();
  let seq = 0;
  window.addEventListener('message', (e) => {
    const d = e.data;
    if (e.source !== window.parent || !d || d.type !== 'oneguy:rest-reply') return;
    const w = waiting.get(d.id);
    if (!w) return;
    waiting.delete(d.id);
    if (!d.ok && (d.status || 502) >= 500) diag('relay', (d.status || 502) + ' ' + (d.error || 'request failed'), d.path);
    if (d.ok && !firstBoard && String(d.path || '').startsWith('/board')) { firstBoard = true; diag('t:first_board', 'first board answer', '', performance.now() - T0); }
    const body = JSON.stringify(d.ok ? d.data : { error: d.error || 'request failed' });
    w(new Response(body, { status: d.ok ? 200 : (d.status || 502), headers: { 'Content-Type': 'application/json' } }));
  });
  window.fetch = (input, init = {}) => {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!url.startsWith('/')) return real ? real(input, init) : Promise.reject(new TypeError('no fetch'));
    if (!url.startsWith('/api/')) {   // static siblings do not exist in the one-document page
      return Promise.resolve(new Response('{"error":"not available in the plugin"}', { status: 404, headers: { 'Content-Type': 'application/json' } }));
    }
    let body;
    try { body = init.body ? JSON.parse(init.body) : undefined; } catch (e) { body = undefined; }
    const id = ++seq;
    return new Promise((resolve) => {
      waiting.set(id, resolve);
      window.parent.postMessage({ type: 'oneguy:rest', id, method: (init.method || 'GET').toUpperCase(), path: url.slice(4), body }, '*');
      setTimeout(() => {
        if (!waiting.delete(id)) return;
        diag('relay', 'no answer in 90s', url.slice(4, 80));
        resolve(new Response('{"error":"the plugin did not answer"}', { status: 504, headers: { 'Content-Type': 'application/json' } }));
      }, 90000);
    });
  };
  // Links: the frame can go nowhere itself. Sessions and web links are handed to the host;
  // anything else (the old server's own pages) is swallowed.
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    const href = a.getAttribute('href') || '';
    e.preventDefault();
    const m = /^hermes(?:app)?:\/\/(?:open|session)\/([^?#/]+)/.exec(href);
    if (m) window.parent.postMessage({ type: 'oneguy:open-session', session_id: decodeURIComponent(m[1]), intent: 'stack' }, '*');
    else if (/^https?:\/\//.test(href)) window.parent.postMessage({ type: 'oneguy:open-url', url: href }, '*');
  }, true);
  // menu rows that pointed at the old server's other pages do not exist here
  document.addEventListener('DOMContentLoaded', () => {
    // "painted" = the briefing has cards on screen
    const t = setInterval(() => {
      if (!document.querySelector('#og-brief .og-fy-card, #og-brief [data-id], #og-cards > *')) return;
      clearInterval(t);
      diag('t:first_cards', 'first cards on screen', '', performance.now() - T0);
    }, 100);
    setTimeout(() => clearInterval(t), 60000);
    document.querySelectorAll('#og-menu a[href^="/"]').forEach((a) => a.remove());
  });
})();
