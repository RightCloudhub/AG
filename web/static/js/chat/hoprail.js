// @ts-check
/**
 * HopRail model (§5.1): pure helpers that turn a streaming progress model or a finished chain into rail
 * items, map evidence rows to the hop that retrieved them, and derive what the brief marks as missing
 * backend fields from fields that do exist (steps[].evidence_ids, evidence ids). Nothing here invents data.
 */
import { CriticAction, EvidenceIdPrefix, QueryStatus, StepPhase, StepState, UiPolicy } from '../contracts.js';

/** @typedef {import('./types.js').ProgressModel} ProgressModel */
/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {import('../contracts.js').StepStateName} StepStateName */
/**
 * @typedef {object} RailItem
 * @property {string} key
 * @property {'plan' | 'hop' | 'answer' | 'skeleton'} kind
 * @property {number} hop 0 for the plan, skeleton and answer nodes
 * @property {string} subQuestion
 * @property {string} conclusion
 * @property {number} evidenceCount -1 while a run streams and the count is not known yet
 * @property {number} elapsedMs client-measured while streaming; 0 when unknown
 * @property {StepStateName} state
 * @property {string} criticAction
 */

export const UNKNOWN_COUNT = -1;

const CHUNK_ID = /^([0-9a-f]{8,})[:#](\d+)$/i;
const HOP_SHORT_ID = 8;
const QUESTION_WORDS = /^(?:what|who|which|where|when|how many|how)\s+(?:is|are|was|were|did|does|do)?\s*(?:the\s+)?|^(?:谁是|什么是|哪个|哪些|请问)/i;
const CJK = /[　-鿿豈-﫿＀-￯]/;
/** A CJK character is as wide as two Latin ones; the brief's 12 字 cap is 24 of these units. */
const LABEL_UNITS = UiPolicy.HOP_LABEL_CHARS * 2;

/** The hop-colour class for hop n; colours repeat after six (§2.4). @param {number} hop */
export function hopClass(hop) {
  if (!Number.isFinite(hop) || hop <= 0) return 'hop-c0';
  return `hop-c${((hop - 1) % UiPolicy.HOP_COLOURS) + 1}`;
}

/** @param {string} ch */
const unit = ch => (CJK.test(ch) ? 2 : 1);

/** A node label of at most 12 字 / 24 Latin characters (§5.1): the text's first clause, cut on width. @param {string} text @param {number} [max] */
export function shortLabel(text, max = LABEL_UNITS) {
  const clause = text.replace(/\s+/g, ' ').trim().split(/[?？。.,，;；:：]/)[0] || text.trim();
  let width = 0;
  for (const ch of clause) width += unit(ch);
  if (width <= max) return clause;
  let out = '', used = 0;
  for (const ch of clause) { if (used + unit(ch) > max - 1) break; out += ch; used += unit(ch); }
  return `${out.trimEnd()}…`;
}

/** The sub-question without its interrogative ("What is the parent company of X?" → "parent company of X"). @param {string} text */
export function stripQuestionWords(text) {
  return text.replace(/\s+/g, ' ').trim().replace(QUESTION_WORDS, '');
}

/** What a finished hop found, else what it asked: the node's label and the evidence tag. @param {{sub_question: string, conclusion: string}} step */
export function hopTitle(step) {
  return shortLabel(step.conclusion || stripQuestionWords(step.sub_question));
}

/** @param {number} hop @param {string} criticAction @returns {StepStateName} */
function stateOfStep(hop, criticAction) {
  return criticAction === CriticAction.GIVE_UP ? StepState.FAILED : StepState.DONE;
}

/** Rail items for a finished chain. @param {Answer} answer @param {ProgressModel | null} [progress] measured timings, if any */
export function railFromAnswer(answer, progress = null) {
  const items = answer.steps.map(step => {
    const measured = progress?.hops.find(h => h.hop === step.hop);
    /** @type {RailItem} */
    const item = {
      key: `hop-${step.hop}`, kind: 'hop', hop: step.hop, subQuestion: step.sub_question, conclusion: step.conclusion,
      evidenceCount: new Set(step.evidence_ids).size, elapsedMs: measured?.elapsedMs ?? 0,
      state: stateOfStep(step.hop, step.critic_action), criticAction: step.critic_action,
    };
    return item;
  });
  const failed = items.some(i => i.state === StepState.FAILED) || answer.status === QueryStatus.NO_ANSWER;
  return [...items, answerNode(failed ? StepState.FAILED : StepState.DONE, answer.answer)];
}

/** Rail items while a run streams: plan → hops as they arrive → the answer node. Counts are unknown (-1) until the chain lands. @param {ProgressModel} model */
export function railFromProgress(model) {
  if (!model.hops.length) {
    const planning = model.phase === StepPhase.PLAN && (model.planText || model.planDetail);
    /** @type {RailItem} */
    const first = {
      key: planning ? 'plan' : 'skeleton', kind: planning ? 'plan' : 'skeleton', hop: 0, subQuestion: '', conclusion: '',
      evidenceCount: UNKNOWN_COUNT, elapsedMs: 0, state: model.state === StepState.STOPPED ? StepState.STOPPED : StepState.ACTIVE, criticAction: '',
    };
    return [first, answerNode(StepState.PENDING, '')];
  }
  const items = model.hops.map(h => /** @type {RailItem} */ ({
    key: `hop-${h.hop}`, kind: 'hop', hop: h.hop, subQuestion: h.subQuestion, conclusion: h.conclusion,
    evidenceCount: h.hits > 0 ? h.hits : UNKNOWN_COUNT, elapsedMs: h.elapsedMs, state: h.state, criticAction: h.criticAction,
  }));
  const terminal = model.state === StepState.STOPPED || model.state === StepState.FAILED;
  const answerState = model.state === StepState.DONE ? StepState.DONE : terminal ? model.state : StepState.PENDING;
  return [...items, answerNode(answerState, '')];
}

/** @param {StepStateName} state @param {string} conclusion @returns {RailItem} */
function answerNode(state, conclusion) {
  return { key: 'answer', kind: 'answer', hop: 0, subQuestion: '', conclusion, evidenceCount: 0, elapsedMs: 0, state, criticAction: '' };
}

/** The link after item i is solid once the next node has started (§5.1 连线). @param {RailItem[]} items @param {number} i */
export function linkPending(items, i) {
  const next = items[i + 1];
  return !next || next.state === StepState.PENDING;
}

/** Evidence id → the first hop whose step retrieved it. @param {Answer} answer @returns {Map<string, number>} */
export function hopOfEvidence(answer) {
  /** @type {Map<string, number>} */
  const map = new Map();
  for (const step of answer.steps) for (const id of step.evidence_ids) if (!map.has(id)) map.set(id, step.hop);
  return map;
}

/** Evidence rows per hop, counting only rows present in the catalog. @param {Answer} answer @returns {Map<number, number>} */
export function hopCounts(answer) {
  const byId = hopOfEvidence(answer);
  /** @type {Map<number, number>} */
  const counts = new Map();
  for (const row of answer.evidence) {
    const hop = byId.get(row.id);
    if (hop !== undefined) counts.set(hop, (counts.get(hop) ?? 0) + 1);
  }
  return counts;
}

/**
 * Where an evidence row comes from, read from its id: `{doc}:{chunk}` is a document chunk, `nbr:` / `path:`
 * are graph rows. Returns null when the id says nothing (then the source line is hidden, §10).
 * @param {string} id @returns {{kind: 'doc', doc: string, chunk: number} | {kind: 'graph', relation: string} | null}
 */
export function evidenceOrigin(id) {
  const chunk = CHUNK_ID.exec(id);
  if (chunk) return { kind: 'doc', doc: chunk[1].slice(0, HOP_SHORT_ID), chunk: Number(chunk[2]) + 1 };
  if (id.startsWith(EvidenceIdPrefix.NEIGHBOR)) {
    const parts = id.slice(EvidenceIdPrefix.NEIGHBOR.length).split(':');
    return { kind: 'graph', relation: parts[1] ?? '' };
  }
  if (id.startsWith(EvidenceIdPrefix.PATH)) return { kind: 'graph', relation: '' };
  return null;
}

/** The hop the rail should select after `[` / `]` (§8). @param {number[]} hops @param {number} current @param {number} delta */
export function stepHop(hops, current, delta) {
  if (!hops.length) return 0;
  const i = hops.indexOf(current);
  if (i === -1) return delta > 0 ? hops[0] : hops[hops.length - 1];
  return hops[Math.min(hops.length - 1, Math.max(0, i + delta))];
}
