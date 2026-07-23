/* Backend client for the trial UI: envelope-aware JSON calls + manual SSE
 * parsing. SSE uses fetch + ReadableStream (not EventSource) because the
 * stream endpoint requires POST with a JSON body. Framework-free module.
 *
 * Auth: optional API key from localStorage key `agr_api_key` (or window.AGR_API_KEY).
 * When set, sent as Authorization: Bearer <key> so AGR_REQUIRE_AUTH=1 works.
 */
const QUERY_URL = "/v1/query";
const STREAM_URL = "/v1/query/stream";
const FEEDBACK_URL = "/v1/feedback";
const HEALTH_URL = "/healthz";
const SSE_BLOCK_SEPARATOR = "\n\n";
const SSE_EVENT_PREFIX = "event:";
const SSE_DATA_PREFIX = "data:";
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
    /* private mode */
  }
}

function authHeaders() {
  const headers = { "Content-Type": "application/json" };
  const key = getApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  return headers;
}

export function friendlyError(err) {
  if (err && err.name === "AbortError") return "已中止";
  const message = err && err.message ? err.message : String(err);
  return message || "未知错误";
}

/* POST JSON and unwrap the unified envelope; throws on success=false. */
async function postEnvelope(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  let env = null;
  try {
    env = await res.json();
  } catch {
    env = null;
  }
  if (!env) throw new Error(`请求失败（HTTP ${res.status}）`);
  if (!env.success) {
    const error = env.error || {};
    throw new Error(error.message || error.code || "请求失败");
  }
  return env.data;
}

export function postQuery(body) {
  return postEnvelope(QUERY_URL, body);
}

export function postFeedback(body) {
  return postEnvelope(FEEDBACK_URL, body);
}

export async function fetchHealth() {
  const res = await fetch(HEALTH_URL, { headers: authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* Consume the SSE stream; calls opts.onEvent({type, payload}) per frame.
 * onEvent may be async — we await it so the UI can paint between frames
 * even when multiple events arrive in one TCP chunk. */
export async function streamQuery(opts) {
  const res = await fetch(STREAM_URL, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(opts.body),
    signal: opts.signal,
  });
  if (!res.ok || !res.body) throw new Error(`stream failed: HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = await emitCompleteBlocks(buffer, opts.onEvent);
  }
}

async function emitCompleteBlocks(buffer, onEvent) {
  const parts = buffer.split(SSE_BLOCK_SEPARATOR);
  const rest = parts.pop() || "";
  for (const block of parts) {
    const evt = parseSseBlock(block);
    if (evt) await onEvent(evt);
  }
  return rest;
}

function parseSseBlock(block) {
  let type = "message";
  let dataLine = "";
  for (const line of block.split("\n")) {
    if (line.startsWith(SSE_EVENT_PREFIX)) type = line.slice(SSE_EVENT_PREFIX.length).trim();
    else if (line.startsWith(SSE_DATA_PREFIX)) dataLine += line.slice(SSE_DATA_PREFIX.length).trim();
  }
  if (!dataLine) return null;
  try {
    return { type, payload: JSON.parse(dataLine) };
  } catch {
    return null;
  }
}
