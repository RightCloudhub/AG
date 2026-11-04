// @ts-check
import { request } from '../transport.js';
import { record, text, number, rows } from '../responses.js';

/** @param {unknown} value */
export function parseSummary(value) {
  const row = record(value), jobs = record(row.jobs ?? {});
  return { kb_id: text(row.kb_id), generation: text(row.generation), enabled: row.enabled === true,
    status: text(row.status), total: number(row.document_count), ready: number(row.ready_documents),
    partial: number(row.partial_documents), communities: number(row.community_reports),
    pending: number(jobs.pending), running: number(jobs.running), failed: number(jobs.failed) };
}
/** @param {unknown} value */
function parseJob(value) {
  const row = record(value);
  return { id: text(row.job_id), kind: text(row.kind), artifact: text(row.artifact_id),
    attempts: number(row.attempts), error: text(row.error_code) };
}
/** @param {string} id */
const base = id => `/v1/knowledge-bases/${encodeURIComponent(id)}`;
/** @param {string} id */
export async function loadSummary(id) { return parseSummary((await request(`${base(id)}/summary-index`)).data); }
/** @param {string} id */
export async function loadFailedJobs(id) {
  return rows((await request(`${base(id)}/summary-jobs?status=failed&limit=100`)).data, parseJob);
}
/** @param {string} id */
export async function buildSummary(id) {
  return request(`${base(id)}/summary-index/build`, { method: 'POST', body: '{}' });
}
/** @param {string} id @param {string} job */
export async function retrySummary(id, job) {
  return request(`${base(id)}/summary-jobs/${encodeURIComponent(job)}/retry`, { method: 'POST' });
}

/** @typedef {ReturnType<typeof parseSummary>} SummaryStatus */
/** @typedef {ReturnType<typeof parseJob>} SummaryJob */
