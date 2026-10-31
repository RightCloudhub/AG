// @ts-check
/** Pure presentation helpers for the question page, the rail, the citations and the log. */
import { StepState, StreamEvent, UiPolicy } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { record, text, number } from '../responses.js';
import { hopTitle } from './hoprail.js';

/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {import('../responses.js').Evidence} Evidence */
/** @typedef {import('./hoprail.js').RailItem} RailItem */
/** @typedef {import('./types.js').LogFrame} LogFrame */

const KILO = 1000;

/** Cited evidence rows in first-citation order with their 1-based catalog index. @param {Answer} answer */
export function citationsFor(answer) {
  const seen = new Set();
  /** @type {{index: number, ev: Evidence}[]} */
  const out = [];
  for (const id of answer.claims.flatMap(c => c.evidence_ids)) {
    const index = answer.evidence.findIndex(e => e.id === id);
    if (index === -1 || seen.has(id)) continue;
    seen.add(id);
    out.push({ index: index + 1, ev: answer.evidence[index] });
  }
  return out;
}

/** @param {string} route */
export function routeLabel(route) {
  const labels = /** @type {Record<string, string>} */ (T.answer.routes);
  return labels[route] ?? route;
}
/** @param {string} action */
export function criticLabel(action) {
  const labels = /** @type {Record<string, string>} */ (T.answer.critics);
  return labels[action] ?? action;
}
/** @param {string} status */
export function statusLabel(status) {
  const labels = /** @type {Record<string, string>} */ (T.answer.status);
  return labels[status] ?? status;
}
/** "0.4 s" under a second as "412 ms" (§9: a space between figure and unit). @param {number} ms */
export function elapsedText(ms) {
  return ms < KILO ? `${Math.round(ms)} ms` : `${(ms / KILO).toFixed(1)} s`;
}
/** @param {number} n */
function compactCount(n) { return n >= KILO ? `${(n / KILO).toFixed(1)}k` : String(n); }

/** The meta row (§4.4 ④): "2 跳 · 1.5 s · 4 次调用 · 1.2k tok". @param {Answer} answer */
export function metaLine(answer) {
  return fmt(T.answer.meta, {
    hops: answer.steps.length, time: elapsedText(answer.cost.latency_ms),
    calls: answer.cost.llm_calls, tokens: compactCount(answer.cost.tokens),
  });
}
/** "置信度 高 95%" from metadata.confidence; empty when the backend sent none or an unknown level (§10). @param {Answer} answer */
export function confidenceTag(answer) {
  const c = answer.confidence;
  const levels = /** @type {Record<string, string>} */ (T.answer.confidenceLevels);
  if (!c || !(c.level in levels)) return '';
  return fmt(T.answer.confidenceTag, { level: levels[c.level], pct: Math.round(c.score * UiPolicy.PERCENT) });
}
/** @param {Answer} answer */
export function confidenceClass(answer) {
  const level = answer.confidence?.level;
  return level === 'high' ? 'tag--ok' : level === 'low' ? 'tag--warn' : 'tag';
}

/** The label under a rail node (§5.1): what the hop found, else what it asked, at most 12 字. @param {RailItem} item */
export function railLabel(item) {
  if (item.kind === 'hop') return hopTitle({ sub_question: item.subQuestion, conclusion: item.state === StepState.DONE ? item.conclusion : '' }) || fmt(T.hoprail.hop, { n: item.hop });
  if (item.kind === 'plan') return T.hoprail.planning;
  if (item.kind === 'skeleton') return T.states.connecting;
  return T.hoprail.answer;
}
/** The second line: the evidence count once known, and the measured time when there is one. @param {RailItem} item */
export function railMeta(item) {
  if (item.kind !== 'hop') return '';
  const parts = [];
  if (item.evidenceCount >= 0) parts.push(fmt(T.hoprail.evidence, { n: item.evidenceCount }));
  if (item.elapsedMs > 0) parts.push(elapsedText(item.elapsedMs));
  return parts.join(' · ');
}
/** @param {RailItem} item */
export function railAria(item) {
  const states = /** @type {Record<string, string>} */ (T.hoprail.states);
  const state = states[item.state] ?? item.state;
  if (item.kind === 'hop') return fmt(T.hoprail.aria, { hop: item.hop, state, n: Math.max(0, item.evidenceCount) });
  return fmt(T.hoprail.ariaAnswer, { label: railLabel(item), state });
}

/** The first 120 characters of a cited row, for the citation popover (§5.2). @param {Evidence} ev */
export function citeSnippet(ev) {
  const flat = ev.content.replace(/\s+/g, ' ').trim();
  return flat.length > UiPolicy.EVIDENCE_PREVIEW_CHARS ? `${flat.slice(0, UiPolicy.EVIDENCE_PREVIEW_CHARS)}…` : flat;
}

/** One log line per SSE frame (§4.5 日志). @param {string} name @param {unknown} payload @param {number} startedAt */
export function logFrame(name, payload, startedAt) {
  const at = `+${elapsedText(Date.now() - startedAt)}`;
  let data = /** @type {Record<string, unknown>} */ ({});
  try { data = record(payload); } catch { data = { message: String(payload) }; }
  /** @type {LogFrame} */
  const frame = { at, name, summary: '' };
  if (name === StreamEvent.TRIAGE) frame.summary = `${routeLabel(text(data.route))}${text(data.rule_hit) ? ` · ${text(data.rule_hit)}` : ''}`;
  else if (name === StreamEvent.THINKING) frame.summary = `${text(data.stage)}: ${text(data.text)}`;
  else if (name === StreamEvent.SUB_QUESTION) frame.summary = `${fmt(T.hoprail.hop, { n: number(data.hop) })} ${text(data.sub_question)}`;
  else if (name === StreamEvent.HOP_DONE) frame.summary = `${fmt(T.hoprail.hop, { n: number(data.hop) })} → ${text(data.conclusion)} · ${criticLabel(text(data.critic_action))}`;
  else if (name === StreamEvent.ANSWER) frame.summary = `${statusLabel(text(data.status))} · ${fmt(T.hoprail.summary, { n: Array.isArray(data.steps) ? data.steps.length : 0 })}`;
  else if (name === StreamEvent.ERROR) frame.summary = `${text(data.code)} ${text(data.message)}`.trim();
  else frame.summary = Object.entries(data).slice(0, 3).map(([k, v]) => `${k}: ${String(v).slice(0, 40)}`).join(' · ');
  return frame;
}

/** @param {string} value */
export async function copyText(value) {
  try { await navigator.clipboard.writeText(value); return true; } catch { return false; }
}
