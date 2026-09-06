/* Console-domain API client (P5-UI-02): knowledge / review / observability /
 * graph endpoints. Envelope + auth handling lives in api.js; this module only
 * knows the endpoint URLs and query-parameter shapes.
 */
import { fetchEnvelope, getApiKey, postJson, unwrapEnvelope } from "./api.js";

const DOCS_URL = "/v1/docs";
const INGEST_TASKS_URL = "/v1/ingest-tasks";
const REVIEW_QUEUE_URL = "/v1/review-queue";
const METRICS_URL = "/v1/metrics";
const BUDGET_URL = "/v1/budget/snapshot";
const AUDIT_EVENTS_URL = "/v1/audit-events";
const GRAPH_ENTITIES_URL = "/v1/graph/entities";

function query(params) {
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") usp.set(key, String(value));
  }
  const qs = usp.toString();
  return qs ? `?${qs}` : "";
}

export function fetchIngestTasks(params = {}) {
  return fetchEnvelope(INGEST_TASKS_URL + query(params));
}

export function fetchReviewQueue(params = {}) {
  return fetchEnvelope(REVIEW_QUEUE_URL + query(params));
}

export function postReviewDecision(itemId, body) {
  return postJson(`${REVIEW_QUEUE_URL}/${encodeURIComponent(itemId)}/decision`, body);
}

export function fetchMetrics() {
  return fetchEnvelope(METRICS_URL);
}

export function fetchBudgetSnapshot() {
  return fetchEnvelope(BUDGET_URL);
}

export function fetchAuditEvents(params = {}) {
  return fetchEnvelope(AUDIT_EVENTS_URL + query(params));
}

export function fetchAuditQuery(queryId) {
  return fetchEnvelope(`/v1/audit/queries/${encodeURIComponent(queryId)}`);
}

export function fetchGraphEntities(params = {}) {
  return fetchEnvelope(GRAPH_ENTITIES_URL + query(params));
}

/* Multipart upload — FormData carries its own Content-Type boundary, so no
 * JSON content-type header is set here (unlike requestEnvelope). */
export async function uploadDocs(files) {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  const headers = {};
  const key = getApiKey();
  if (key) headers.Authorization = `Bearer ${key}`;
  const res = await fetch(DOCS_URL, { method: "POST", headers, body: form });
  return unwrapEnvelope(res);
}
