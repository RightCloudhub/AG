// @ts-check
/** Validate external JSON before exposing typed data to views. */
import {
  ErrorMessage, IngestStatus, QueryRoute, ReviewStatus, ReviewType, Role, View, isRecord,
} from './contracts.js';

/** @param {unknown} value @returns {Record<string, unknown>} */
export function record(value) {
  if (!isRecord(value)) throw new TypeError(ErrorMessage.INVALID_RESPONSE);
  return value;
}

/** @param {unknown} value @param {string} [fallback] */
export function text(value, fallback = '') {
  return typeof value === 'string' ? value : fallback;
}

/** @param {unknown} value */
export function number(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** @template {string} T @param {unknown} value @param {Record<string, T>} choices */
function choice(value, choices) {
  const match = Object.values(choices).find(item => item === value);
  if (match === undefined) throw new TypeError(ErrorMessage.INVALID_RESPONSE);
  return match;
}

/** @template T @param {unknown} value @param {(value: unknown) => T} parser @returns {T[]} */
export function rows(value, parser) {
  if (!Array.isArray(value)) throw new TypeError(ErrorMessage.INVALID_RESPONSE);
  return value.map(parser);
}

/** @param {unknown} value @returns {string[]} */
export function strings(value) {
  return value === undefined || value === null ? [] : rows(value, item => {
    if (typeof item !== 'string') throw new TypeError(ErrorMessage.INVALID_RESPONSE);
    return item;
  });
}

/** @param {unknown} value */
export function parseMeta(value) {
  const meta = isRecord(value) ? value : {};
  const extra = isRecord(meta.extra) ? meta.extra : {};
  return { total: number(meta.total), extra: { pending_count: number(extra.pending_count) } };
}

/** @param {unknown} value */
export function parseIdentity(value) {
  const data = record(value);
  const caps = record(data.capabilities);
  const capabilities = {
    [View.CHAT]: caps[View.CHAT] === true, [View.GRAPH]: caps[View.GRAPH] === true,
    [View.KNOWLEDGE]: caps[View.KNOWLEDGE] === true, [View.REVIEW]: caps[View.REVIEW] === true,
    [View.OPS]: caps[View.OPS] === true,
  };
  const role = choice(data.role, Role);
  return {
    user_id: text(data.user_id), tenant_id: text(data.tenant_id), role,
    authenticated: data.authenticated === true, capabilities,
  };
}

/** @param {unknown} value */
export function parseHealth(value) {
  const data = record(value);
  return { status: text(data.status), checks: record(data.checks) };
}

/** @param {unknown} value */
export function parseEntity(value) {
  const data = record(value);
  return { id: text(data.id), name: text(data.name), type: text(data.type), aliases: strings(data.aliases) };
}

/** @param {unknown} value */
export function parseTaskDoc(value) {
  const data = record(value);
  return {
    doc_id: text(data.doc_id), name: text(data.name), bytes: text(data.bytes), error: text(data.error),
  };
}

/** @param {unknown} value */
export function parseTask(value) {
  const data = record(value);
  return {
    id: text(data.id), task_id: text(data.task_id), status: choice(data.status, IngestStatus),
    created_at: number(data.created_at), docs: rows(data.docs, parseTaskDoc), message: text(data.message),
  };
}

/** @param {unknown} value */
export function parseReview(value) {
  const data = record(value);
  return {
    id: text(data.id), type: choice(data.type, ReviewType), status: choice(data.status, ReviewStatus),
    confidence: number(data.confidence), payload: record(data.payload),
  };
}

/** @param {unknown} value */
export function parseAuditEvent(value) {
  const data = record(value);
  return {
    ts: number(data.ts), timestamp: number(data.timestamp), action: text(data.action),
    target: text(data.target), outcome: text(data.outcome), tenant_id: text(data.tenant_id),
  };
}

/** @param {unknown} value */
export function parseMetrics(value) {
  const data = record(value);
  return {
    count: number(data.count), latency_p50_ms: number(data.latency_p50_ms),
    latency_p95_ms: number(data.latency_p95_ms), latency_p99_ms: number(data.latency_p99_ms),
    budget_trips: number(data.budget_trips),
    route_counts: record(data.route_counts), error_counts: record(data.error_counts),
  };
}

/**
 * Evidence catalog row (generation/evidence_catalog.py). Optional fields are
 * only copied when the wire carries them so older payloads round-trip unchanged.
 * @param {unknown} value
 * @returns {{id: string, content: string, score: number, source?: string, truncated?: boolean}}
 */
export function parseEvidence(value) {
  const entry = record(value);
  return {
    id: text(entry.id), content: text(entry.content), score: number(entry.score),
    ...(typeof entry.source === 'string' ? { source: entry.source } : {}),
    ...(typeof entry.truncated === 'boolean' ? { truncated: entry.truncated } : {}),
  };
}

/** @param {unknown} value */
function parseToolCall(value) {
  const call = record(value);
  return { tool: text(call.tool), reason: text(call.reason), hits: strings(call.hits) };
}

/** @param {unknown} value */
export function parseStep(value) {
  const step = record(value);
  return {
    hop: number(step.hop), sub_question: text(step.sub_question), conclusion: text(step.conclusion),
    depends_on: strings(step.depends_on), evidence_ids: strings(step.evidence_ids),
    critic_action: text(step.critic_action),
    tool_calls: step.tool_calls === undefined ? [] : rows(step.tool_calls, parseToolCall),
  };
}

/** @param {unknown} value */
function parseClaim(value) {
  const claim = record(value);
  return { text: text(claim.text), evidence_ids: strings(claim.evidence_ids) };
}

/** Answer confidence persisted by the composer in metadata.confidence. @param {unknown} value */
export function parseConfidence(value) {
  if (!isRecord(value)) return null;
  return { level: text(value.level), score: number(value.score), reasons: strings(value.reasons) };
}

/** Triage decision (agent/triage.py) as streamed and persisted. @param {unknown} value */
export function parseTriage(value) {
  if (!isRecord(value)) return null;
  return {
    route: text(value.route), rationale: text(value.rationale),
    estimated_hops: number(value.estimated_hops), confidence: number(value.confidence),
    rule_hit: text(value.rule_hit),
  };
}

/** @param {unknown} value */
export function parseAnswer(value) {
  const data = record(value);
  if (typeof data.answer !== 'string' || typeof data.query_id !== 'string') {
    throw new TypeError(ErrorMessage.INVALID_RESPONSE);
  }
  const route = choice(data.route, QueryRoute);
  const cost = record(data.cost);
  const metadata = data.metadata === undefined ? {} : record(data.metadata);
  return {
    query_id: data.query_id, question: text(data.question), answer: data.answer, route,
    status: text(data.status),
    evidence: metadata.evidence === undefined ? [] : rows(metadata.evidence, parseEvidence),
    claims: data.claims === undefined || data.claims === null ? [] : rows(data.claims, parseClaim),
    steps: data.steps === undefined ? [] : rows(data.steps, parseStep),
    missing_info: strings(data.missing_info), explored_paths: strings(data.explored_paths),
    cost: { latency_ms: number(cost.latency_ms), tokens: number(cost.tokens), llm_calls: number(cost.llm_calls) },
    confidence: parseConfidence(metadata.confidence), triage: parseTriage(metadata.triage),
  };
}

/** @typedef {ReturnType<typeof parseIdentity>} Identity */
/** @typedef {ReturnType<typeof parseHealth>} Health */
/** @typedef {ReturnType<typeof parseEntity>} Entity */
/** @typedef {ReturnType<typeof parseTask>} Task */
/** @typedef {ReturnType<typeof parseTaskDoc>} TaskDoc */
/** @typedef {ReturnType<typeof parseReview>} ReviewItem */
/** @typedef {ReturnType<typeof parseAuditEvent>} AuditEvent */
/** @typedef {ReturnType<typeof parseMetrics>} Metrics */
/** @typedef {ReturnType<typeof parseEvidence>} Evidence */
/** @typedef {ReturnType<typeof parseStep>} Step */
/** @typedef {ReturnType<typeof parseAnswer>} Answer */
