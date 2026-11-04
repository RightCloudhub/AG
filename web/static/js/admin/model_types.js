// @ts-check
import { isRecord } from '../contracts.js';

/** @typedef {'triage'|'researcher'|'verifier'|'writer'|'extractor'|'embedding'|'rerank'} ModelRole */
/** @typedef {{id: string, provider: string, model: string, capabilities: Record<string, unknown>}} CatalogModel */
/** @typedef {{role: ModelRole, model_id: string, capability: string, status: 'passed'|'failed'|'disabled', error_code: string, latency_ms: number, tokens: number}} Check */
/** @typedef {{checks: Check[], passed: boolean}} TestReport */
/** @typedef {{id: string, name: string, revision: number, roles: Record<ModelRole, string|null>, active_revision: number, test: TestReport|null}} Profile */
/** @type {readonly ModelRole[]} */
export const modelRoles = Object.freeze(['triage', 'researcher', 'verifier', 'writer', 'extractor', 'embedding', 'rerank']);

/** @param {unknown} value @returns {CatalogModel} */
export function parseModel(value) {
  if (!isRecord(value) || typeof value.id !== 'string' || typeof value.model !== 'string') throw new Error('Invalid model catalogue');
  return { id: value.id, provider: String(value.provider || ''), model: value.model,
    capabilities: isRecord(value.capabilities) ? value.capabilities : {} };
}

/** @param {unknown} value @returns {TestReport|null} */
export function parseTest(value) {
  if (!isRecord(value) || !Array.isArray(value.checks)) return null;
  const checks = value.checks.map(parseCheck);
  const covered = new Set(checks.filter(row => row.status === 'passed').map(row => row.role));
  return { checks, passed: checks.every(row => row.status !== 'failed') && modelRoles.every(role => role === 'rerank' || covered.has(role)) };
}

/** @param {unknown} value @returns {Check} */
function parseCheck(value) {
  if (!isRecord(value) || !modelRoles.some(role => role === value.role)) throw new Error('Invalid capability check');
  if (value.status !== 'passed' && value.status !== 'failed' && value.status !== 'disabled') throw new Error('Invalid capability status');
  return { role: /** @type {ModelRole} */ (value.role), status: value.status,
    model_id: String(value.model_id || ''), capability: String(value.capability || ''),
    error_code: String(value.error_code || ''), latency_ms: Number(value.latency_ms || 0), tokens: Number(value.tokens || 0) };
}

/** @param {unknown} value @returns {Profile} */
export function parseProfile(value) {
  if (!isRecord(value) || typeof value.id !== 'string') throw new Error('Invalid model profile');
  const raw = isRecord(value.roles) ? value.roles : {};
  const roles = emptyRoles();
  for (const role of modelRoles) roles[role] = typeof raw[role] === 'string' ? raw[role] : null;
  return { id: value.id, name: String(value.name || ''), revision: Number(value.revision || 0),
    active_revision: Number(value.active_revision || 0), roles, test: parseTest(value.test) };
}

/** @returns {Record<ModelRole, string|null>} */
export function emptyRoles() {
  return { triage: null, researcher: null, verifier: null, writer: null, extractor: null, embedding: null, rerank: null };
}

/** @param {CatalogModel[]} catalog @param {ModelRole} role */
export function forRole(catalog, role) {
  const capability = role === 'embedding' || role === 'rerank' ? role : 'completion';
  return catalog.filter(model => model.capabilities[capability] === true);
}
