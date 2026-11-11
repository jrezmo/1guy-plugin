// Harness stand-in for react/jsx-runtime: components expand eagerly.
import { expand } from './react.js'
export const jsx = (type, props) => expand(type, props || {})
export const jsxs = jsx
export const Fragment = 'div'
