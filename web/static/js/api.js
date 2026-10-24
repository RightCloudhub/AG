// @ts-check
/** API client with validated envelopes and incremental SSE frames. */
import {
  ApiEndpoint, ErrorCode, ErrorMessage, ErrorName, FormField, HttpHeader,
  HttpMethod, MediaType, PaginationKey, QueryKey, StorageKey, StreamEvent,
  UiPolicy, isRecord,
} from './contracts.js';
import { consumeStream } from './stream.js';
import {
  parseMeta, parseIdentity, parseHealth, parseEntity, parseTask, parseReview,
  parseAuditEvent, parseMetrics, rows, record,
} from './responses.js';

/** @typedef {import('./contracts.js').ApiResult} ApiResult */
/** @typedef {import('./contracts.js').ListParams} ListParams */
/** @typedef {import('./contracts.js').QueryRequest} QueryRequest */
/** @typedef {import('./contracts.js').StreamHandlers} StreamHandlers */

export class ApiError extends Error {
  /** @param {string} message @param {string} code @param {unknown} [details] */
  constructor(message, code, details) {
    super(message);
    this.code = code;
    this.details = details;
  }
}

export function getApiKey() {
  return localStorage.getItem(StorageKey.API_KEY) || '';
}

/** @param {string} key */
export function setApiKey(key) {
  const value = key.trim();
  if (value) localStorage.setItem(StorageKey.API_KEY, value);
  else localStorage.removeItem(StorageKey.API_KEY);
}

/** @param {HeadersInit} [initial] @param {boolean} [multipart] */
function requestHeaders(initial, multipart = false) {
  const headers = new Headers(initial);
  if (!multipart) headers.set(HttpHeader.CONTENT_TYPE, MediaType.JSON);
  const key = getApiKey();
  if (key) headers.set(HttpHeader.AUTHORIZATION, `Bearer ${key}`);
  return headers;
}

/** @param {Response} response @returns {Promise<ApiResult>} */
async function parseResponse(response) {
  if (!response.ok && !response.headers.get(HttpHeader.CONTENT_TYPE)?.includes(MediaType.JSON)) {
    throw new ApiError(`HTTP ${response.status}`, ErrorCode.HTTP);
  }
  /** @type {unknown} */
  const envelope = await response.json();
  if (!isRecord(envelope) || typeof envelope.success !== 'boolean') {
    throw new ApiError(ErrorMessage.INVALID_RESPONSE, ErrorCode.INVALID_RESPONSE);
  }
  if (!response.ok || !envelope.success) {
    const error = isRecord(envelope.error) ? envelope.error : {};
    throw new ApiError(
      typeof error.message === 'string' ? error.message : ErrorMessage.UNKNOWN,
      typeof error.code === 'string' ? error.code : ErrorCode.HTTP,
      error.details,
    );
  }
  return { data: envelope.data, meta: parseMeta(envelope.meta) };
}

/** @param {string} path @param {RequestInit} [options] */
async function request(path, options = {}) {
  const headers = requestHeaders(options.headers, options.body instanceof FormData);
  return parseResponse(await fetch(path, { ...options, headers }));
}

/** @template T @param {Promise<ApiResult>} response @param {(value: unknown) => T} parser */
async function parsed(response, parser) {
  const result = await response;
  return { ...result, data: parser(result.data) };
}

export function fetchMe() { return parsed(request(ApiEndpoint.ME), parseIdentity); }

/** @param {QueryRequest} body */
export function postQuery(body) {
  return request(ApiEndpoint.QUERY, { method: HttpMethod.POST, body: JSON.stringify(body) });
}

/** @param {Response} response */
async function validateStream(response) {
  if (!response.ok) await parseResponse(response);
  if (!response.body || !response.headers.get(HttpHeader.CONTENT_TYPE)?.includes(MediaType.SSE)) {
    throw new ApiError(ErrorMessage.INVALID_STREAM, ErrorCode.INVALID_RESPONSE);
  }
  return response.body;
}

/** @param {QueryRequest} body @param {StreamHandlers} [handlers] */
export function streamQuery(body, handlers = {}) {
  const controller = new AbortController();
  const run = async () => {
    const response = await fetch(ApiEndpoint.STREAM, {
      method: HttpMethod.POST,
      headers: requestHeaders(),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    await consumeStream(await validateStream(response), handlers);
    handlers.done?.();
  };
  run().catch(error => {
    if (error instanceof Error && error.name === ErrorName.ABORT) return;
    handlers[StreamEvent.ERROR]?.({
      code: error instanceof ApiError ? error.code : ErrorCode.STREAM,
      message: error instanceof Error ? error.message : String(error),
      details: error instanceof ApiError ? error.details : undefined,
    }, StreamEvent.ERROR);
  });
  return controller;
}

export async function fetchHealth() {
  const response = await fetch(ApiEndpoint.HEALTH);
  if (!response.ok) throw new ApiError(`HTTP ${response.status}`, ErrorCode.HTTP);
  return parseHealth(await response.json());
}

/** @param {ListParams} params */
function pagination(params) {
  return new URLSearchParams({
    [PaginationKey.LIMIT]: String(params.limit ?? UiPolicy.DEFAULT_PAGE_SIZE),
    [PaginationKey.OFFSET]: String(params.offset ?? 0),
  });
}

/** @param {ListParams} [params] */
export function fetchEntities(params = {}) {
  const query = pagination(params);
  if (params.query) query.set(QueryKey.QUERY, params.query);
  if (params.entity_type) query.set(QueryKey.ENTITY_TYPE, params.entity_type);
  return parsed(request(`${ApiEndpoint.ENTITIES}?${query}`), value => rows(value, parseEntity));
}

/** @param {ListParams} [params] */
export function fetchIngestTasks(params = {}) {
  return parsed(request(`${ApiEndpoint.INGEST_TASKS}?${pagination(params)}`), value => rows(value, parseTask));
}

/** @param {string} taskId */
export function fetchIngestTask(taskId) { return request(ApiEndpoint.ingestTask(taskId)); }

/** @param {File[]} files */
export function uploadDocs(files) {
  const form = new FormData();
  for (const file of files) form.append(FormField.FILES, file);
  return request(ApiEndpoint.DOCS, { method: HttpMethod.POST, body: form });
}

/** @param {ListParams} [params] */
export function fetchReviewQueue(params = {}) {
  const query = pagination(params);
  if (params.status) query.set(QueryKey.STATUS, params.status);
  return parsed(request(`${ApiEndpoint.REVIEW_QUEUE}?${query}`), value => rows(value, parseReview));
}

/** @param {string} itemId @param {{decision: import('./contracts.js').ReviewDecisionName}} body */
export function decideReview(itemId, body) {
  return request(ApiEndpoint.reviewDecision(itemId), {
    method: HttpMethod.POST, body: JSON.stringify(body),
  });
}

/** @param {{query_id: string, accurate: boolean, reason?: string}} body */
export function postFeedback(body) {
  return request(ApiEndpoint.FEEDBACK, { method: HttpMethod.POST, body: JSON.stringify(body) });
}

/** @param {string} queryId */
export function fetchAuditQuery(queryId) { return request(ApiEndpoint.auditQuery(queryId)); }

export function fetchMetrics() { return parsed(request(ApiEndpoint.METRICS), parseMetrics); }
export function fetchBudgetSnapshot() { return parsed(request(ApiEndpoint.BUDGET), record); }

/** @param {ListParams} [params] */
export function fetchAuditEvents(params = {}) {
  const query = pagination(params);
  if (params.action) query.set(QueryKey.ACTION, params.action);
  return parsed(request(`${ApiEndpoint.AUDIT_EVENTS}?${query}`), value => rows(value, parseAuditEvent));
}

/** @param {string} queryId */
export function fetchTrace(queryId) { return request(ApiEndpoint.trace(queryId)); }
