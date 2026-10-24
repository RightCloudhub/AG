// @ts-check
/** Shared wire values and UI policy. Business modules reference these members. */
export const HttpMethod = Object.freeze({ POST: 'POST' });
export const HttpHeader = Object.freeze({
  CONTENT_TYPE: 'Content-Type', AUTHORIZATION: 'Authorization',
});
export const MediaType = Object.freeze({ JSON: 'application/json', SSE: 'text/event-stream' });
export const StorageKey = Object.freeze({ API_KEY: 'agr_api_key' });
export const FormField = Object.freeze({ FILES: 'files' });
export const ErrorCode = Object.freeze({
  STREAM: 'STREAM_ERROR', HTTP: 'HTTP_ERROR', INVALID_RESPONSE: 'INVALID_RESPONSE',
});
export const ErrorName = Object.freeze({ ABORT: 'AbortError' });
export const KeyboardKey = Object.freeze({ ENTER: 'Enter' });
export const UploadPolicy = Object.freeze({ ACCEPT: '.md,.txt' });
export const ErrorMessage = Object.freeze({
  UNKNOWN: 'Unknown error', INVALID_RESPONSE: 'Invalid API response',
  INVALID_STREAM: 'Invalid stream response',
});
export const StreamEvent = Object.freeze({
  CACHE_HIT: 'cache_hit', TRIAGE: 'triage', THINKING: 'thinking',
  SUB_QUESTION: 'sub_question', HOP_DONE: 'hop_done', ANSWER: 'answer', ERROR: 'error',
});
export const SseField = Object.freeze({ EVENT: 'event:', DATA: 'data:' });
export const PaginationKey = Object.freeze({ LIMIT: 'limit', OFFSET: 'offset' });
export const QueryKey = Object.freeze({
  QUERY: 'query', ENTITY_TYPE: 'entity_type', STATUS: 'status', ACTION: 'action',
});
export const View = Object.freeze({
  CHAT: 'chat', GRAPH: 'graph', KNOWLEDGE: 'knowledge', REVIEW: 'review', OPS: 'ops',
});
export const Role = Object.freeze({ READER: 'reader', OPERATOR: 'operator', ADMIN: 'admin' });
export const HealthStatus = Object.freeze({ OK: 'ok', PASSED: '1' });
export const AuditOutcome = Object.freeze({ SUCCESS: 'success' });
export const MessageRole = Object.freeze({ USER: 'user', ASSISTANT: 'assistant', ERROR: 'error' });
export const QueryRoute = Object.freeze({
  AGENTIC: 'agentic', FAST_PATH: 'fast_path', BASELINE: 'baseline', CHITCHAT: 'chitchat',
});
export const ReviewStatus = Object.freeze({
  PENDING: 'pending', APPROVED: 'approved', REJECTED: 'rejected', SKIPPED: 'skipped',
});
export const ReviewDecision = Object.freeze({ APPROVE: 'approve', REJECT: 'reject', SKIP: 'skip' });
export const ReviewType = Object.freeze({
  EXTRACTION: 'extraction', RESOLUTION: 'resolution', CONFLICT: 'conflict',
  SPOTCHECK: 'spotcheck', FEEDBACK: 'feedback',
});
export const IngestStatus = Object.freeze({
  QUEUED: 'queued', EXTRACTING: 'extracting', REVIEW: 'review', DONE: 'done',
  FAILED: 'failed', EMPTY: 'empty',
});
export const OpsTab = Object.freeze({ METRICS: 'metrics', BUDGET: 'budget', AUDIT: 'audit' });
export const UiPolicy = Object.freeze({
  DEFAULT_PAGE_SIZE: 50, TABLE_PAGE_SIZE: 20, GRAPH_PAGE_SIZE: 24,
  AUDIT_PAGE_SIZE: 100, SEARCH_DEBOUNCE_MS: 300, ID_PREVIEW: 12,
  SHORT_ID_PREVIEW: 8, PAYLOAD_PREVIEW: 80, MILLISECONDS_PER_SECOND: 1000,
  DEFAULT_MAX_HOPS: 6,
  SCORE_PRECISION: 2,
});
export const ApiEndpoint = Object.freeze({
  ME: '/v1/me', QUERY: '/v1/query', STREAM: '/v1/query/stream', HEALTH: '/healthz',
  ENTITIES: '/v1/graph/entities', INGEST_TASKS: '/v1/ingest-tasks', DOCS: '/v1/docs',
  REVIEW_QUEUE: '/v1/review-queue', FEEDBACK: '/v1/feedback', METRICS: '/v1/metrics',
  BUDGET: '/v1/budget/snapshot', AUDIT_EVENTS: '/v1/audit-events',
  /** @param {string} id */
  ingestTask: id => `/v1/ingest-tasks/${encodeURIComponent(id)}`,
  /** @param {string} id */
  reviewDecision: id => `/v1/review-queue/${encodeURIComponent(id)}/decision`,
  /** @param {string} id */
  auditQuery: id => `/v1/audit/queries/${encodeURIComponent(id)}`,
  /** @param {string} id */
  trace: id => `/v1/traces/${encodeURIComponent(id)}`,
});

/** @typedef {(typeof StreamEvent)[keyof typeof StreamEvent]} StreamEventName */
/** @typedef {(payload: unknown, event: StreamEventName) => void} StreamHandler */
/** @typedef {Partial<Record<StreamEventName, StreamHandler>> & {done?: () => void}} StreamHandlers */
/** @typedef {{question: string, force_agentic?: boolean, max_hops?: number, timeout_ms?: number}} QueryRequest */
/** @typedef {(typeof ReviewDecision)[keyof typeof ReviewDecision]} ReviewDecisionName */
/** @typedef {(typeof ReviewStatus)[keyof typeof ReviewStatus]} ReviewStatusName */
/** @typedef {(typeof ReviewType)[keyof typeof ReviewType]} ReviewTypeName */
/** @typedef {(typeof IngestStatus)[keyof typeof IngestStatus]} IngestStatusName */
/** @typedef {(typeof View)[keyof typeof View]} ViewName */
/** @typedef {(typeof OpsTab)[keyof typeof OpsTab]} OpsTabName */
/** @typedef {(typeof MessageRole)[keyof typeof MessageRole]} MessageRoleName */
/** @typedef {{limit?: number, offset?: number, query?: string, entity_type?: string, status?: ReviewStatusName, action?: string}} ListParams */
/** @template [T=unknown] @typedef {{data: T, meta: {total: number, extra: {pending_count: number}}}} ApiResult */

/** @param {unknown} value @returns {value is Record<string, unknown>} */
export function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const streamEvents = new Set(Object.values(StreamEvent));
/** @param {string} value @returns {value is StreamEventName} */
export function isStreamEvent(value) {
  return Array.from(streamEvents).some(event => event === value);
}
