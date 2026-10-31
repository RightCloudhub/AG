// @ts-check
/** Question pages (§4.4): group the message list into pages — a question, the rows under it, its answer. */
import { MessageRole } from '../contracts.js';

/** @typedef {import('./types.js').ChatMessage} ChatMessage */
/** @typedef {import('./types.js').PageModel} PageModel */

/** @param {ChatMessage} m @param {number} index @param {number} page @returns {PageModel} */
function openPage(m, index, page) {
  return { key: `${m.ts}-${index}`, index: page, question: m.role === MessageRole.USER ? m.text ?? '' : '', userIndex: m.role === MessageRole.USER ? index : -1, rows: [], answer: null, live: false };
}

/**
 * A page opens at every user question; everything until the next question belongs to it. While a run
 * streams, the last page without an answer is the live one.
 * @param {ChatMessage[]} messages @param {boolean} streaming
 */
export function buildPages(messages, streaming) {
  /** @type {PageModel[]} */
  const pages = [];
  /** @type {PageModel | null} */
  let current = null;
  messages.forEach((m, index) => {
    if (m.role === MessageRole.USER) { current = openPage(m, index, pages.length); pages.push(current); return; }
    if (!current) { current = openPage(m, index, pages.length); pages.push(current); }
    if (m.role === MessageRole.ASSISTANT && m.data) current.answer = { msg: m, index };
    else current.rows.push({ msg: m, index });
  });
  const last = pages[pages.length - 1];
  if (streaming && last && !last.answer) last.live = true;
  return pages;
}
