const API_KEY_STORAGE = "agr_api_key";

export function getApiKey() {
  try {
    if (typeof window !== "undefined" && window.AGR_API_KEY) return String(window.AGR_API_KEY);
    return localStorage.getItem(API_KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key) {
  try {
    if (key) localStorage.setItem(API_KEY_STORAGE, key);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {
    return;
  }
}

export function friendlyError(err) {
  if (err && err.name === "AbortError") return "请求已取消";
  if (err && err.status === 401) return "API Key 无效或已过期，请检查连接设置。";
  if (err && err.status === 403) return "当前账号没有执行此操作的权限。";
  if (err && err.status === 404) return "记录不存在，或已被移除。";
  if (err && err.status === 409) return "记录已处理，请刷新后重试。";
  return (err && err.message) || String(err || "未知错误");
}

export async function requestEnvelope(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const key = getApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  if (options.body && !options.form) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.form || (options.body ? JSON.stringify(options.body) : undefined),
    signal: options.signal,
  });
  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new Error(`服务器返回了无法读取的响应（HTTP ${response.status}）`);
  }
  if (!response.ok || !envelope.success) {
    const detail = envelope.error || {};
    const error = new Error(detail.message || `请求失败（HTTP ${response.status}）`);
    error.status = response.status;
    error.code = detail.code || "REQUEST_FAILED";
    error.details = detail.details || null;
    throw error;
  }
  return { data: envelope.data, meta: envelope.meta || {} };
}

export async function fetchHealth() {
  const response = await fetch("/healthz");
  if (!response.ok) throw new Error(`健康检查失败（HTTP ${response.status}）`);
  return response.json();
}

export async function fetchMe() {
  return (await requestEnvelope("/v1/me")).data;
}

export async function postQuery(body, signal) {
  return (await requestEnvelope("/v1/query", { method: "POST", body, signal })).data;
}

export async function postFeedback(body) {
  return (await requestEnvelope("/v1/feedback", { method: "POST", body })).data;
}

export async function streamQuery({ body, signal, onEvent }) {
  const headers = { "Content-Type": "application/json" };
  const key = getApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  const response = await fetch("/v1/query/stream", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    const err = new Error(`流式查询失败（HTTP ${response.status}）`);
    err.status = response.status;
    throw err;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = await emitCompleteBlocks(buffer, onEvent);
  }
  if (buffer.trim()) await emitEventBlock(buffer, onEvent);
}

export async function uploadDocuments(files) {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return requestEnvelope("/v1/docs", { method: "POST", form });
}

export async function listIngestTasks({ limit = 50, offset = 0 } = {}) {
  return requestEnvelope(`/v1/ingest-tasks?limit=${limit}&offset=${offset}`);
}

export async function listReviewItems({ status = "pending", type = "", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ status, limit: String(limit), offset: String(offset) });
  if (type) params.set("type", type);
  return requestEnvelope(`/v1/review-queue?${params}`);
}

export async function decideReview(itemId, body) {
  return (await requestEnvelope(`/v1/review-queue/${encodeURIComponent(itemId)}/decision`, {
    method: "POST",
    body,
  })).data;
}

export async function listGraphEntities({ query = "", type = "", limit = 50, offset = 0 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query) params.set("query", query);
  if (type) params.set("entity_type", type);
  return requestEnvelope(`/v1/graph/entities?${params}`);
}

export async function fetchMetrics() {
  return (await requestEnvelope("/v1/metrics")).data;
}

export async function fetchBudget() {
  return (await requestEnvelope("/v1/budget/snapshot")).data;
}

export async function fetchAuditEvents(filters = {}) {
  const params = new URLSearchParams({ limit: String(filters.limit || 100) });
  ["since", "until", "tenant_id", "action"].forEach((key) => {
    if (filters[key] !== undefined && filters[key] !== "") params.set(key, String(filters[key]));
  });
  return (await requestEnvelope(`/v1/audit-events?${params}`)).data;
}

export async function fetchAuditQuery(queryId) {
  return (await requestEnvelope(`/v1/audit/queries/${encodeURIComponent(queryId)}`)).data;
}

export async function fetchTrace(queryId) {
  return (await requestEnvelope(`/v1/traces/${encodeURIComponent(queryId)}`)).data;
}

async function emitCompleteBlocks(buffer, onEvent) {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const remainder = blocks.pop() || "";
  for (const block of blocks) await emitEventBlock(block, onEvent);
  return remainder;
}

async function emitEventBlock(block, onEvent) {
  let type = "message";
  const data = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (!data.length) return;
  let payload;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return;
  }
  await onEvent({ type, payload });
}
