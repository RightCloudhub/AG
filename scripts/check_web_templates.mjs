#!/usr/bin/env node
// Compile every Vue string template in web/static/js with the vendored Vue
// browser build so template syntax errors fail before a browser is involved.
// Dev/CI verification only (rules.md §8): the browser still loads raw ESM.
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const STATIC = new URL(`file://${ROOT}/web/static/`);
/** Components with a `default.template`. */
const VIEWS = ['views/chat', 'admin/graph', 'admin/knowledge', 'admin/review', 'admin/ops'];
/** Modules whose string exports are template fragments (compiled inside a wrapper). */
const FRAGMENTS = ['shell/tpl_topbar', 'shell/tpl_notebook', 'shell/tpl_inspector', 'chat/tpl_page', 'chat/tpl_thread', 'chat/tpl_composer'];

/** Minimal DOM so the browser build imports and decodes entities. */
const decode = raw => raw
  .replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
const element = () => {
  let raw = '';
  return {
    style: {}, childNodes: [], dataset: {}, setAttribute() {}, appendChild() {}, cloneNode: element,
    content: { firstChild: null, childNodes: [] },
    set innerHTML(value) { raw = value; },
    get innerHTML() { return raw; },
    get textContent() { return decode(raw); },
    get children() {
      return [{ getAttribute: () => decode(raw.replace(/^<div foo="/, '').replace(/">$/, '')) }];
    },
  };
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.window = { innerWidth: 1400, location: { hash: '' }, addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }) };
globalThis.document = {
  activeElement: null, visibilityState: 'visible', documentElement: element(), getElementById: () => null, querySelector: () => null,
  createElement: element, createElementNS: element, createTextNode: () => ({}), createComment: () => ({}),
};

const describe = error => `${error?.constructor?.name ?? typeof error}: ${String(error?.message ?? error).slice(0, 300)}`;
const { compile } = await import(new URL('vendor/vue.esm-browser.prod.js', STATIC));
const check = (name, template) => {
  const render = compile(template, { onError(error) { throw error; } });
  if (typeof render !== 'function') throw new Error(`compile returned ${typeof render}`);
  console.log(`OK   ${name} (${template.length} chars)`);
};

let failures = 0;
for (const view of VIEWS) {
  const path = `js/${view}.js`;
  try { check(path, (await import(new URL(path, STATIC))).default.template); }
  catch (error) { failures += 1; console.log(`FAIL ${path} ${describe(error)}`); }
}
for (const fragment of FRAGMENTS) {
  const path = `js/${fragment}.js`;
  try {
    const mod = await import(new URL(path, STATIC));
    for (const [name, value] of Object.entries(mod)) if (typeof value === 'string' && name.endsWith('_TEMPLATE')) check(`${fragment}/${name}`, `<div>${value.replace(/\$\{THREAD_ROWS\}/g, '')}</div>`);
  } catch (error) { failures += 1; console.log(`FAIL ${path} ${describe(error)}`); }
}
process.exit(failures ? 1 : 0);
