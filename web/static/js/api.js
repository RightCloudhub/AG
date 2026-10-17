/* API client for the AgenticGraphRAG backend.
 * Every JSON route returns the envelope { success, data, error, meta }. */
const KEY_STORAGE = "agr_api_key";

export function getApiKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key) {
  const value = (key || "").trim();
  if (value) localStorage.setItem(KEY_STORAGE, value);
  else localStorage.removeItem(KEY_STORAGE);
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const key = getApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  return headers;
}

export class ApiError extends Error {
  constructor(message, { status = 0, code = "", details = null } = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isAuth() {
    return this.status === 401 || this.status === 403;
  }
}

async function unwrap(res) {
  let envelope = null;
  try {
    envelope = await res.json();
  } catch {
    throw new ApiError(`HTTP ${res.status}`, { status: res.status });
  }
  if (!res.ok || !envelope || envelope.success === false) {
    const err = (envelope && envelope.error) || {};
    throw new ApiError(err.message || `HTTP ${res.status}`, {
      status: res.status,
      code: err.code || "",
      details: err.details || null,
    });
  }
  return { data: envelope.data, meta: envelope.meta || {} };
}

async function request(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: authHeaders(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(`网络错误：${err.message}`, { status: 0, code: "NETWORK" });
  }
  return unwrap(res);
}

function qs(params) {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") search.set(k, String(v));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/* ── Identity & health ─────────────────────────────────────────────── */
export const fetchMe = () => request("/v1/me");

export async function fetchHealth() {
  const res = await fetch("/healthz");
  return res.json();
}

/* ── Query ─────────────────────────────────────────────────────────── */
export const postQuery = (body) => request("/v1/query", { method: "POST", body });

/** Parse one SSE frame ("event: x\ndata: {...}") into [event, payload]. */
export function parseSseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return null;
  const raw = data.join("\n");
  try {
    return [event, JSON.parse(raw)];
  } catch {
    return [event, raw];
  }
}

/** POST /v1/query/stream; calls handlers[event](payload). Returns an AbortController. */
export function streamQuery(body, handlers = {}) {
  const ctrl = new AbortController();
  const emit = (event, payload) => (handlers[event] || handlers.unknown)?.(payload, event);
  (async () => {
    const res = await fetch("/v1/query/stream", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json", Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) {
      const { data } = await unwrap(res); // throws ApiError with the server message
      return emit("answer", data);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const parsed = parseSseFrame(buffer.slice(0, cut));
        buffer = buffer.slice(cut + 2);
        if (parsed) emit(parsed[0], parsed[1]);
      }
    }
    handlers.done?.();
  })().catch((err) => {
    if (err.name === "AbortError") return handlers.done?.();
    handlers.error?.({ code: err.code || "STREAM_ERROR", message: err.message });
  });
  return ctrl;
}

export const postFeedback = (body) => request("/v1/feedback", { method: "POST", body });

/* ── Knowledge ─────────────────────────────────────────────────────── */
export async function uploadDocs(files) {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  let res;
  try {
    res = await fetch("/v1/docs", { method: "POST", headers: authHeaders(), body: form });
  } catch (err) {
    throw new ApiError(`网络错误：${err.message}`, { code: "NETWORK" });
  }
  return unwrap(res);
}

export const fetchIngestTasks = ({ limit = 20, offset = 0 } = {}) =>
  request(`/v1/ingest-tasks${qs({ limit, offset })}`);

export const fetchIngestTask = (taskId) =>
  request(`/v1/ingest-tasks/${encodeURIComponent(taskId)}`);

/* ── Review ────────────────────────────────────────────────────────── */
export const fetchReviewQueue = ({ limit = 20, offset = 0, status = "", type = "" } = {}) =>
  request(`/v1/review-queue${qs({ limit, offset, status, type })}`);

export const decideReview = (itemId, body) =>
  request(`/v1/review-queue/${encodeURIComponent(itemId)}/decision`, { method: "POST", body });

/* ── Graph ─────────────────────────────────────────────────────────── */
export const fetchEntities = ({ limit = 24, offset = 0, query = "", entity_type = "" } = {}) =>
  request(`/v1/graph/entities${qs({ limit, offset, query, entity_type })}`);

/* ── Ops & audit ───────────────────────────────────────────────────── */
export const fetchMetrics = () => request("/v1/metrics");
export const fetchBudgetSnapshot = () => request("/v1/budget/snapshot");
export const fetchAuditEvents = ({ limit = 50, action = "" } = {}) =>
  request(`/v1/audit-events${qs({ limit, action })}`);
export const fetchAuditQuery = (queryId) =>
  request(`/v1/audit/queries/${encodeURIComponent(queryId)}`);
export const fetchTrace = (queryId) => request(`/v1/traces/${encodeURIComponent(queryId)}`);
