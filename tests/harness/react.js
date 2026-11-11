// Tiny React stand-in for the browser harness (NOT the app's React).
// Just enough for plugin.js: useState / useRef / mount-only useEffect, and a
// DOM renderer that patches in place so the iframe is not recreated per render.
const roots = []

function makeRoot(container, render) {
  const root = { container, render, hooks: [], idx: 0, pending: false, effects: [] }
  roots.push(root)
  return root
}

let current = null

export function useState(init) {
  const r = current
  const i = r.idx++
  if (!(i in r.hooks)) r.hooks[i] = typeof init === 'function' ? init() : init
  const set = v => {
    r.hooks[i] = typeof v === 'function' ? v(r.hooks[i]) : v
    schedule(r)
  }
  return [r.hooks[i], set]
}

export function useRef(init) {
  const r = current
  const i = r.idx++
  if (!(i in r.hooks)) r.hooks[i] = { current: init }
  return r.hooks[i]
}

export function useEffect(fn) {
  const r = current
  const i = r.idx++
  if (!(i in r.hooks)) {
    r.hooks[i] = true
    r.effects.push(fn)
  }
}

function schedule(r) {
  if (r.pending) return
  r.pending = true
  queueMicrotask(() => {
    r.pending = false
    draw(r)
  })
}

function draw(r) {
  current = r
  r.idx = 0
  const tree = r.render()
  current = null
  patch(r.container, 0, tree)
  const fx = r.effects.splice(0)
  fx.forEach(f => f())
}

function patch(parent, index, node) {
  const kids = Array.from(parent.childNodes)
  const old = kids[index]
  if (node === false || node === null || node === undefined) return index
  if (typeof node === 'string' || typeof node === 'number') {
    if (old && old.nodeType === 3) {
      if (old.textContent !== String(node)) old.textContent = String(node)
    } else {
      const t = document.createTextNode(String(node))
      old ? parent.replaceChild(t, old) : parent.appendChild(t)
    }
    return index + 1
  }
  let el = old
  if (!el || el.nodeType !== 1 || el.tagName.toLowerCase() !== node.type) {
    el = document.createElement(node.type)
    old ? parent.replaceChild(el, old) : parent.appendChild(el)
  }
  const { children, ref, onClick, className, ...attrs } = node.props
  if (className !== undefined) el.className = className
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'key') continue
    if (el.getAttribute(k) !== String(v)) el.setAttribute(k, v)
  }
  el.onclick = onClick || null
  if (ref) ref.current = el
  let n = 0
  for (const c of [].concat(children ?? []).flat(Infinity)) n = patch(el, n, c)
  while (el.childNodes.length > n) el.removeChild(el.lastChild)
  return index + 1
}

export function mount(container, render) {
  draw(makeRoot(container, render))
}

export function expand(type, props) {
  return typeof type === 'function' ? type(props) : { type, props }
}

export default { useState, useRef, useEffect }
