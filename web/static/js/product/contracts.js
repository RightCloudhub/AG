// @ts-check
import { parseIdentity, record, rows, text } from '../responses.js';

export const SETTINGS = ['profile', 'security', 'members', 'api-keys', 'tenant'];
/** @param {unknown} value */
export function parseUser(value) {
  const data = record(value);
  const caps = record(data.capabilities);
  /** @type {Record<string, boolean>} */
  const capabilities = { ...parseIdentity(data).capabilities };
  for (const id of SETTINGS) capabilities[`settings_${id.replace('-', '_')}`] = caps[`settings_${id.replace('-', '_')}`] === true;
  return { ...parseIdentity(data), capabilities, email: text(data.email), display_name: text(data.display_name),
    tenants: rows(data.tenants, value => {
      const item = record(value);
      return { id: text(item.id), name: text(item.name), role: text(item.role), kind: text(item.kind) };
    }),
  };
}
/** @typedef {ReturnType<typeof parseUser>} User */

/** Restrict return URLs to application routes and invitation links, including encoded input. @param {unknown} value */
export function safeNext(value) {
  if (typeof value !== 'string') return '/app/chat';
  try {
    const decoded = decodeURIComponent(value);
    if (/[\\\u0000-\u0020]/.test(decoded) || !/^\/(?:app(?:\/|$)|invite\/)/.test(decoded)) return '/app/chat';
    const url = new URL(value, 'https://local.invalid');
    if (url.origin !== 'https://local.invalid' || !/^\/(?:app(?:\/|$)|invite\/)/.test(url.pathname)) return '/app/chat';
    return `${url.pathname}${url.search}${url.hash}`;
  } catch { return '/app/chat'; }
}
