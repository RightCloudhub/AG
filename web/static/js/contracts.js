// @ts-check
/** Shared wire values and UI policy. Business modules reference these members. */
export const HttpMethod = Object.freeze({ POST: 'POST' });
export const HttpHeader = Object.freeze({
  CONTENT_TYPE: 'Content-Type', AUTHORIZATION: 'Authorization',
});
export const MediaType = Object.freeze({ JSON: 'application/json', SSE: 'text/event-stream' });
export const StorageKey = Object.freeze({
  API_KEY: 'agr_api_key', CONVERSATIONS: 'agr_conversations', PREFERENCES: 'agr_preferences', THEME: 'agr_theme',
});
export const Theme = Object.freeze({ DARK: 'dark', LIGHT: 'light' });
export const FormField = Object.freeze({ FILES: 'files' });
export const ErrorCode = Object.freeze({
  STREAM: 'STREAM_ERROR', HTTP: 'HTTP_ERROR', INVALID_RESPONSE: 'INVALID_RESPONSE',
});
export const ErrorName = Object.freeze({ ABORT: 'AbortError' });
export const KeyboardKey = Object.freeze({
  ENTER: 'Enter', ESCAPE: 'Escape', ARROW_UP: 'ArrowUp', ARROW_DOWN: 'ArrowDown', ARROW_LEFT: 'ArrowLeft',
  ARROW_RIGHT: 'ArrowRight', TAB: 'Tab', BRACKET_LEFT: '[', BRACKET_RIGHT: ']',
});
/** Global shortcuts (§8). Letters are matched case-insensitively; `mod` is ⌘ on macOS and Ctrl elsewhere. */
export const Shortcut = Object.freeze({
  PALETTE: 'k', NEW_QUESTION: 'n', INSPECTOR: 'e', THEME: 'l', PREV_HOP: '[', NEXT_HOP: ']',
});
export const MentionTrigger = '@';
export const SlashTrigger = '/';
export const SlashCommand = Object.freeze({ WIZARD: 'wizard', PLAN: 'plan', IMPORT: 'import' });
export const UploadPolicy = Object.freeze({
  ACCEPT: '.md,.txt', EXTENSIONS: Object.freeze(['md', 'txt']), MAX_FILES: 20,
  MAX_BYTES: 5 * 1024 * 1024, PREVIEW_CHARS: 240,
});
export const ErrorMessage = Object.freeze({
  UNKNOWN: 'Unknown error', INVALID_RESPONSE: 'Invalid API response',
  INVALID_STREAM: 'Invalid stream response',
});
export const StreamEvent = Object.freeze({
  CACHE_HIT: 'cache_hit', TRIAGE: 'triage', THINKING: 'thinking',
  SUB_QUESTION: 'sub_question', HOP_DONE: 'hop_done', ANSWER: 'answer', ERROR: 'error',
});
/** `thinking` payload stages emitted by agent/loop_stream_events.py. */
export const ThinkingStage = Object.freeze({ PLAN: 'plan', RETRIEVE: 'retrieve', PROGRESS: 'progress' });
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
export const MessageRole = Object.freeze({
  USER: 'user', ASSISTANT: 'assistant', ERROR: 'error', CLARIFY: 'clarify', NOTICE: 'notice',
});
export const QueryRoute = Object.freeze({
  AGENTIC: 'agentic', FAST_PATH: 'fast_path', BASELINE: 'baseline', CHITCHAT: 'chitchat',
});
export const QueryStatus = Object.freeze({ ANSWERED: 'answered', PARTIAL: 'partial', NO_ANSWER: 'no_answer' });
export const CriticAction = Object.freeze({
  SUFFICIENT: 'sufficient', NEXT_HOP: 'next_hop', REWRITE: 'rewrite', GIVE_UP: 'give_up',
});
/** Composer mode. PLAN_CONFIRM degrades to AGENTIC while FeatureFlag.PLAN_PREVIEW is off. */
export const QueryMode = Object.freeze({ AUTO: 'auto', AGENTIC: 'agentic', PLAN_CONFIRM: 'plan_confirm' });
export const InspectorTab = Object.freeze({ EVIDENCE: 'evidence', SUBGRAPH: 'subgraph', LOG: 'log' });
export const EvidenceSort = Object.freeze({ SCORE: 'score', HOP: 'hop' });
/** Command-palette entry kinds (§4.2): a past question, a view, a mode, the theme. */
export const PaletteKind = Object.freeze({ QUESTION: 'question', VIEW: 'view', MODE: 'mode', THEME: 'theme', ACTION: 'action' });
/** Notebook history groups (§4.3). */
export const HistoryGroup = Object.freeze({ TODAY: 'today', WEEK: 'week', EARLIER: 'earlier' });
export const WizardStep = Object.freeze({ ENTITY: 0, RELATIONS: 1, CONSTRAINTS: 2, PREVIEW: 3 });
export const IngestStep = Object.freeze({ SELECT: 0, PREVIEW: 1, CONFIRM: 2, PROGRESS: 3 });
export const ConstraintKind = Object.freeze({ TIME: 'time', COUNT: 'count', INTERSECT: 'intersect' });
export const FeedbackReason = Object.freeze({
  ENTITY: 'wrong_entity', CHAIN: 'wrong_chain', MISSING: 'missing_answer', EXTRA: 'extra_answers',
});
export const ClarifyKind = Object.freeze({ ENTITY: 'entity', BROAD: 'broad', MISSING: 'missing' });
export const StepPhase = Object.freeze({ PLAN: 'plan', RETRIEVE: 'retrieve', EVALUATE: 'evaluate', ANSWER: 'answer' });
export const StepState = Object.freeze({
  PENDING: 'pending', ACTIVE: 'active', DONE: 'done', STOPPED: 'stopped', FAILED: 'failed',
});
/** Evidence ids minted by retrieval/graph.py and retrieval/graph_paths.py. */
export const EvidenceIdPrefix = Object.freeze({ NEIGHBOR: 'nbr:', PATH: 'path:' });
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
  DEFAULT_MAX_HOPS: 6, MIN_MAX_HOPS: 1, MAX_MAX_HOPS: 20,
  SCORE_PRECISION: 2, PERCENT: 100, LOW_SCORE: 0.5,
  AUTOCOMPLETE_LIMIT: 8, AUTOCOMPLETE_MIN_CHARS: 1,
  MAX_CONVERSATIONS: 200, CONVERSATION_TITLE_CHARS: 40,
  FOLLOWUP_COUNT: 3, CLARIFY_MIN_QUESTION_CHARS: 12, CLARIFY_MAX_ENTITIES: 3,
  WIZARD_MAX_RELATIONS: 4, EVIDENCE_PREVIEW_CHARS: 120, SUBGRAPH_MAX_EDGES: 40, CHAIN_CITE_PREVIEW: 5,
  POLL_INTERVAL_MS: 2000, COMPOSER_MAX_LINES: 8, COPY_FLASH_MS: 1500,
  /* the HopRail (§5.1) and the inspector (§4.5) */
  HOP_COLOURS: 6, HOP_LABEL_CHARS: 12, HOP_RAIL_SCROLL_AFTER: 5, HIGHLIGHT_MS: 1200, EVIDENCE_CLAMP_LINES: 3,
  INSPECTOR_MIN_PX: 320, INSPECTOR_MAX_PX: 520, INSPECTOR_DEFAULT_PX: 380, PALETTE_LIMIT: 8,
  /* responsive bands (§7) */
  WIDE_PX: 1440, DESKTOP_PX: 1200, TABLET_PX: 768, PHONE_PX: 480,
  DAY_MS: 86400000, WEEK_DAYS: 7,
});
/**
 * Backend capabilities the UI degrades around. Each `false` flag has a documented
 * interface contract in plan/workstreams/api-and-ui.md §2.8; flip to `true` only
 * once the endpoint ships. The UI must never call a ProposedEndpoint while its
 * flag is off.
 */
export const FeatureFlag = Object.freeze({
  PLAN_PREVIEW: false, RERUN_FROM_HOP: false, EXTRACTION_PREVIEW: false,
  GRAPH_NEIGHBORS: false, RELATION_CATALOG: false,
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
/** Contract-only paths (not implemented by the backend); see FeatureFlag. */
export const ProposedEndpoint = Object.freeze({
  PLAN_PREVIEW: '/v1/query/plan', RERUN_FROM_HOP: '/v1/query/{query_id}/rerun',
  EXTRACTION_PREVIEW: '/v1/docs/preview', GRAPH_NEIGHBORS: '/v1/graph/entities/{id}/neighbors',
  RELATION_CATALOG: '/v1/graph/relations',
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
/** @typedef {(typeof QueryMode)[keyof typeof QueryMode]} QueryModeName */
/** @typedef {(typeof QueryStatus)[keyof typeof QueryStatus]} QueryStatusName */
/** @typedef {(typeof InspectorTab)[keyof typeof InspectorTab]} InspectorTabName */
/** @typedef {(typeof EvidenceSort)[keyof typeof EvidenceSort]} EvidenceSortName */
/** @typedef {(typeof Theme)[keyof typeof Theme]} ThemeName */
/** @typedef {(typeof PaletteKind)[keyof typeof PaletteKind]} PaletteKindName */
/** @typedef {(typeof HistoryGroup)[keyof typeof HistoryGroup]} HistoryGroupName */
/** @typedef {(typeof SlashCommand)[keyof typeof SlashCommand]} SlashCommandName */
/** @typedef {(typeof ConstraintKind)[keyof typeof ConstraintKind]} ConstraintKindName */
/** @typedef {(typeof FeedbackReason)[keyof typeof FeedbackReason]} FeedbackReasonName */
/** @typedef {(typeof ClarifyKind)[keyof typeof ClarifyKind]} ClarifyKindName */
/** @typedef {(typeof StepPhase)[keyof typeof StepPhase]} StepPhaseName */
/** @typedef {(typeof StepState)[keyof typeof StepState]} StepStateName */
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
