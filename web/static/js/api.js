/**
 * API client — typed fetch wrapper for AgenticGraphRAG backend.
 * All responses follow the envelope: { success, data, error, meta }.
 */

const BASE = '';

export function getApiKey() {
  return localStorage.getItem('agr_api_key') || '';
}

export function setApiKey(key) {
  if (key) localStorage.setItem('agr_api_key', key.trim());
  else localStorage.removeItem('agr_api_key');
}

/** Unwrap the envelope; throw on error. */
async function request(path, options = {}) {
  const url = `${BASE}${path}`;
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  const key = getApiKey();
  if (key) headers['Authorization'] = `Bearer ${key}`;

  const res = await fetch(url, {
    ...options,
    headers,
  });
  if (!res.ok && !res.headers.get('content-type')?.includes('json')) {
    throw new Error(`HTTP ${res.status}`);
  }
  const envelope = await res.json();
  if (!envelope.success) {
    const err = new Error(envelope.error?.message || 'Unknown error');
    err.code = envelope.error?.code;
    err.details = envelope.error?.details;
    throw err;
  }
  return { data: envelope.data, meta: envelope.meta };
}

/* ── Identity ──────────────────────────────────────────────────────── */

export function fetchMe() {
  return request('/v1/me');
}

/* ── Query ─────────────────────────────────────────────────────────── */

export function postQuery(body) {
  return request('/v1/query', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * SSE streaming query — calls handlers for each event type.
 * Returns an AbortController so caller can cancel.
 */
export function streamQuery(body, handlers = {}) {
  const ctrl = new AbortController();
  const run = async () => {
    const headers = { 'Content-Type': 'application/json' };
    const key = getApiKey();
    if (key) headers['Authorization'] = `Bearer ${key}`;

    const res = await fetch(`${BASE}/v1/query/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const raw = line.slice(6);
          let payload;
          try { payload = JSON.parse(raw); } catch { payload = raw; }
          const handler = handlers[currentEvent] || handlers.unknown;
          if (handler) handler(payload, currentEvent);
        }
      }
    }
    if (handlers.done) handlers.done();
  };
  run().catch(err => {
    if (err.name !== 'AbortError' && handlers.error) {
      handlers.error({ code: 'STREAM_ERROR', message: err.message });
    }
  });
  return ctrl;
}

/* ── Health ─────────────────────────────────────────────────────────── */

export async function fetchHealth() {
  const res = await fetch(`${BASE}/healthz`);
  return res.json();
}

/* ── Graph ──────────────────────────────────────────────────────────── */

export function fetchEntities(params = {}) {
  const qs = new URLSearchParams();
  if (params.limit) qs.set('limit', params.limit);
  if (params.offset != null) qs.set('offset', params.offset);
  if (params.query) qs.set('query', params.query);
  if (params.entity_type) qs.set('entity_type', params.entity_type);
  return request(`/v1/graph/entities?${qs}`);
}

/* ── Knowledge ─────────────────────────────────────────────────────── */

export function fetchIngestTasks(params = {}) {
  const qs = new URLSearchParams();
  qs.set('limit', params.limit || 50);
  qs.set('offset', params.offset || 0);
  return request(`/v1/ingest-tasks?${qs}`);
}

export function fetchIngestTask(taskId) {
  return request(`/v1/ingest-tasks/${taskId}`);
}

export async function uploadDocs(files) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  const res = await fetch(`${BASE}/v1/docs`, { method: 'POST', body: form });
  const envelope = await res.json();
  if (!envelope.success) throw new Error(envelope.error?.message);
  return { data: envelope.data, meta: envelope.meta };
}

/* ── Review Queue ──────────────────────────────────────────────────── */

export function fetchReviewQueue(params = {}) {
  const qs = new URLSearchParams();
  qs.set('limit', params.limit || 50);
  qs.set('offset', params.offset || 0);
  if (params.status) qs.set('status', params.status);
  return request(`/v1/review-queue?${qs}`);
}

export function decideReview(itemId, body) {
  return request(`/v1/review-queue/${itemId}/decision`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/* ── Feedback ──────────────────────────────────────────────────────── */

export function postFeedback(body) {
  return request('/v1/feedback', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/* ── Audit ─────────────────────────────────────────────────────────── */

export function fetchAuditQuery(queryId) {
  return request(`/v1/audit/queries/${queryId}`);
}

/* ── Admin / Ops ───────────────────────────────────────────────────── */

export function fetchMetrics() {
  return request('/v1/metrics');
}

export function fetchBudgetSnapshot() {
  return request('/v1/budget/snapshot');
}

export function fetchAuditEvents(params = {}) {
  const qs = new URLSearchParams();
  if (params.limit) qs.set('limit', params.limit);
  if (params.action) qs.set('action', params.action);
  return request(`/v1/audit-events?${qs}`);
}

export function fetchTrace(queryId) {
  return request(`/v1/traces/${queryId}`);
}
