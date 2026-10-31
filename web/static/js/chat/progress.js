// @ts-check
/**
 * Streaming progress model: folds SSE frames into a step bar
 * (plan → hop N retrieve → evaluate → answer). Pure functions; the view owns
 * the ref. Hit counts come from the final chain's steps because hop_done frames
 * do not carry them; elapsed time is measured client-side.
 */
import { StepPhase, StepState, ThinkingStage } from '../contracts.js';
import { record, text, number, strings } from '../responses.js';

/** @typedef {import('./types.js').ProgressModel} ProgressModel */
/** @typedef {import('./types.js').HopProgress} HopProgress */
/** @typedef {import('./types.js').StepBarItem} StepBarItem */
/** @typedef {import('../responses.js').Answer} Answer */

/** @param {number} now @returns {ProgressModel} */
export function emptyProgress(now) {
  return {
    startedAt: now, phase: StepPhase.PLAN, state: StepState.ACTIVE, planText: '', planDetail: '',
    note: '', route: '', cacheHit: false, hops: [],
  };
}

/** @param {ProgressModel} model @param {unknown} payload */
export function applyTriage(model, payload) {
  const data = record(payload);
  return { ...model, route: text(data.route) || text(data.path) };
}

/** @param {ProgressModel} model @param {unknown} payload */
export function applyThinking(model, payload) {
  const data = record(payload);
  const stage = text(data.stage);
  const body = text(data.text) || text(data.thought);
  if (stage === ThinkingStage.PLAN) {
    return { ...model, phase: StepPhase.PLAN, planText: body, planDetail: text(data.detail) };
  }
  if (stage === ThinkingStage.RETRIEVE) {
    const tools = body.split(':').pop()?.replace(/检索/, '').split(',').map(t => t.trim()).filter(Boolean) ?? [];
    return patchLastHop(model, hop => ({ ...hop, tools }));
  }
  return { ...model, note: body };
}

/** @param {ProgressModel} model @param {unknown} payload @param {number} now */
export function applySubQuestion(model, payload, now) {
  const data = record(payload);
  const hop = number(data.hop) || model.hops.length + 1;
  const subQuestion = text(data.sub_question) || text(data.question) || text(data.text);
  /** @type {HopProgress} */
  const fresh = {
    hop, subQuestion, startedAt: now, elapsedMs: 0, tools: [], hits: 0, conclusion: '',
    criticAction: '', state: StepState.ACTIVE,
  };
  const existing = model.hops.findIndex(h => h.hop === hop);
  const hops = existing === -1
    ? [...model.hops, fresh]
    : model.hops.map((h, i) => (i === existing ? { ...h, subQuestion: subQuestion || h.subQuestion, state: StepState.ACTIVE } : h));
  return { ...model, phase: StepPhase.RETRIEVE, hops };
}

/** @param {ProgressModel} model @param {unknown} payload @param {number} now */
export function applyHopDone(model, payload, now) {
  const data = record(payload);
  const hop = number(data.hop);
  const done = { conclusion: text(data.conclusion), criticAction: text(data.critic_action), state: StepState.DONE };
  const known = model.hops.some(h => h.hop === hop);
  const hops = known
    ? model.hops.map(h => (h.hop === hop ? { ...h, ...done, elapsedMs: Math.max(0, now - h.startedAt) } : h))
    : [...model.hops, { hop, subQuestion: '', startedAt: now, elapsedMs: 0, tools: [], hits: 0, ...done }];
  return { ...model, phase: StepPhase.EVALUATE, hops };
}

/** @param {ProgressModel} model */
export function applyCacheHit(model) {
  return { ...model, cacheHit: true };
}

/** Attach the final chain: hit counts per hop and the answer phase. @param {ProgressModel} model @param {Answer} answer */
export function finishProgress(model, answer) {
  const hops = model.hops.map(hop => {
    const step = answer.steps.find(s => s.hop === hop.hop);
    const hits = step ? new Set([...step.evidence_ids, ...step.tool_calls.flatMap(c => c.hits)]).size : hop.hits;
    return { ...hop, hits, state: StepState.DONE, subQuestion: hop.subQuestion || (step?.sub_question ?? '') };
  });
  const missing = answer.steps.filter(s => !hops.some(h => h.hop === s.hop)).map(s => ({
    hop: s.hop, subQuestion: s.sub_question, startedAt: model.startedAt, elapsedMs: 0,
    tools: s.tool_calls.map(c => c.tool), hits: new Set(s.evidence_ids).size, conclusion: s.conclusion,
    criticAction: s.critic_action, state: StepState.DONE,
  }));
  return { ...model, phase: StepPhase.ANSWER, state: StepState.DONE, hops: [...hops, ...missing].sort((a, b) => a.hop - b.hop) };
}

/** @param {ProgressModel} model */
export function stopProgress(model) {
  return {
    ...model, state: StepState.STOPPED,
    hops: model.hops.map(h => (h.state === StepState.ACTIVE ? { ...h, state: StepState.STOPPED } : h)),
  };
}

/** @param {ProgressModel} model */
export function failProgress(model) {
  return { ...model, state: StepState.FAILED };
}

/** @param {ProgressModel} model @param {(hop: HopProgress) => HopProgress} update */
function patchLastHop(model, update) {
  if (!model.hops.length) return model;
  const last = model.hops.length - 1;
  return { ...model, hops: model.hops.map((h, i) => (i === last ? update(h) : h)) };
}

/** Items for the horizontal step bar. @param {ProgressModel} model @returns {StepBarItem[]} */
export function stepBar(model) {
  const finished = model.state === StepState.DONE;
  const planState = model.phase === StepPhase.PLAN && !finished ? model.state : StepState.DONE;
  /** @type {StepBarItem[]} */
  const items = [{ key: 'plan', phase: StepPhase.PLAN, hop: 0, state: planState }];
  for (const hop of model.hops) {
    items.push({ key: `hop-${hop.hop}`, phase: StepPhase.RETRIEVE, hop: hop.hop, state: hop.state });
  }
  const evaluating = model.phase === StepPhase.EVALUATE && !finished;
  items.push({
    key: 'evaluate', phase: StepPhase.EVALUATE, hop: 0,
    state: finished ? StepState.DONE : evaluating ? StepState.ACTIVE : terminalOrPending(model),
  });
  items.push({ key: 'answer', phase: StepPhase.ANSWER, hop: 0, state: finished ? StepState.DONE : terminalOrPending(model) });
  return items;
}

/** @param {ProgressModel} model */
function terminalOrPending(model) {
  return model.state === StepState.STOPPED || model.state === StepState.FAILED ? model.state : StepState.PENDING;
}

/** Parse a plan detail block ("1. …\n2. …") into lines. @param {ProgressModel} model */
export function planLines(model) {
  return strings(model.planDetail.split('\n').filter(Boolean));
}
