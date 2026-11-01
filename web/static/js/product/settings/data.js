// @ts-check
import { record, text, number } from '../../responses.js';
import { user } from '../session.js';
import { P } from '../copy.js';

export const roles = Object.entries(P.roles).map(([value, label]) => ({ value, label }));
export const SECONDS_PER_DAY = 86400;
/** @param {string} [suffix] */
export function tenantPath(suffix = '') { return `/v1/tenants/${encodeURIComponent(user.value?.tenant_id || '')}${suffix}`; }
/** @param {unknown} value */
export function nullableTime(value) { return typeof value === 'number' && Number.isFinite(value) ? value : null; }
/** @param {number | null} value */
export function dateTime(value) {
  return value === null ? P.unknown : new Date(value * 1000).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' });
}
/** @param {unknown} value */
export function parseSession(value) {
  const data = record(value);
  return { id: text(data.id), tenant_id: text(data.tenant_id), created_at: nullableTime(data.created_at),
    expires_at: nullableTime(data.expires_at), current: data.current === true };
}
/** @param {unknown} value */
export function parseMember(value) {
  const data = record(value);
  return { user_id: text(data.user_id), display_name: text(data.display_name), email: text(data.email),
    role: text(data.role), joined_at: nullableTime(data.joined_at) };
}
/** @param {unknown} value */
export function parseInvite(value) {
  const data = record(value);
  return { id: text(data.id), email: text(data.email), role: text(data.role), status: text(data.status),
    created_at: nullableTime(data.created_at), expires_at: nullableTime(data.expires_at) };
}
/** @param {unknown} value */
export function parseKey(value) {
  const data = record(value);
  return { id: text(data.id), name: text(data.name), prefix: text(data.prefix), role: text(data.role),
    created_at: nullableTime(data.created_at), expires_at: nullableTime(data.expires_at),
    last_used_at: nullableTime(data.last_used_at), revoked_at: nullableTime(data.revoked_at) };
}
/** @param {ReturnType<typeof parseKey>} key @param {number} [now] */
export function keyStatus(key, now = Date.now() / 1000) {
  if (key.revoked_at !== null) return 'revoked';
  return key.expires_at !== null && key.expires_at <= now ? 'expired' : 'active';
}
/** @param {unknown} value */
export function parseTenant(value) {
  const data = record(value);
  return { id: text(data.id), name: text(data.name), kind: text(data.kind),
    member_count: number(data.member_count), created_at: nullableTime(data.created_at) };
}
/** @param {string} value */
export function initial(value) { return Array.from(value.trim())[0]?.toUpperCase() || '?'; }
