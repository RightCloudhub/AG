// @ts-check
/** Cookies are HttpOnly. CSRF stays in memory; refresh retries end at the response boundary. */
import { ApiError, parseResponse } from '../http.js';
import { record, text } from '../responses.js';

let csrf = '';
/** @type {Promise<string> | null} */
let csrfFlight = null;
/** @type {Promise<void> | null} */
let refreshFlight = null;
/** @type {(value: unknown) => void} */
let observeIdentity = () => {};
/** @type {() => void} */
let expireIdentity = () => {};
const PUBLIC_AUTH = /^\/v1\/auth\/(?:login|register|forgot|reset|logout|csrf)$/;
const IDENTITY_WRITE = /^\/v1\/(?:auth\/(?:login|register|reset|logout|password\/change)|tenants\/[^/]+\/switch)$/;

/** @param {{identity: (value: unknown) => void, expired: () => void}} callbacks */
export function onCookieIdentity(callbacks) {
  observeIdentity = callbacks.identity;
  expireIdentity = callbacks.expired;
}
/** @template T @param {() => Promise<T>} work */
export async function identityLock(work) {
  return globalThis.navigator?.locks
    ? await navigator.locks.request('agr-identity-cookie', work) : await work();
}
/** @param {unknown} value */
export function acceptCsrf(value) { csrf = text(record(value).csrf_token, csrf); }
export function forgetCsrf() { csrf = ''; }

/** @param {string} path @param {RequestInit} [options] */
async function raw(path, options = {}) {
  const headers = new Headers(options.headers);
  headers.delete('Authorization');
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD'].includes(options.method || 'GET')) headers.set('X-CSRF-Token', csrf);
  options.signal?.throwIfAborted();
  return fetch(path, { ...options, headers, credentials: 'include' });
}

async function bootstrapCsrf() {
  if (!csrfFlight) csrfFlight = (async () => {
    const result = await parseResponse(await raw('/v1/auth/csrf'));
    csrf = text(record(result.data).csrf_token);
    if (!csrf) throw new ApiError('Invalid CSRF response', 'INVALID_RESPONSE');
    return csrf;
  })().finally(() => { csrfFlight = null; });
  return csrfFlight;
}

async function refreshUnlocked() {
  const existing = await raw('/v1/me');
  if (existing.ok) {
    observeIdentity((await parseResponse(existing)).data);
    await bootstrapCsrf();
    return;
  }
  if (existing.status !== 401) await parseResponse(existing);
  await bootstrapCsrf();
  const result = await parseResponse(await raw('/v1/auth/refresh', { method: 'POST' }));
  acceptCsrf(result.data);
  observeIdentity(result.data);
}

/** @param {boolean} locked */
function refreshAccess(locked) {
  if (locked) return refreshUnlocked();
  if (!refreshFlight) refreshFlight = identityLock(refreshUnlocked).finally(() => { refreshFlight = null; });
  return refreshFlight;
}

/** @param {Response} response */
async function isCsrfError(response) {
  if (response.status !== 403) return false;
  try { await parseResponse(response.clone()); }
  catch (error) { return error instanceof ApiError && error.code === 'CSRF_FAILED'; }
  return false;
}

/** @param {string} path @param {RequestInit} options @param {boolean} locked */
async function exchange(path, options, locked) {
  if (!csrf && !['GET', 'HEAD'].includes(options.method || 'GET')) await bootstrapCsrf();
  let response = await raw(path, options);
  if (await isCsrfError(response)) {
    await bootstrapCsrf();
    response = await raw(path, options);
  }
  if (response.status !== 401 || PUBLIC_AUTH.test(path)) return response;
  try { await refreshAccess(locked); }
  catch (error) {
    if (error instanceof ApiError && error.status === 401) expireIdentity();
    throw error;
  }
  response = await raw(path, options);
  if (response.status === 401) expireIdentity();
  return response;
}

/** @param {string} path @param {RequestInit} options */
export function cookieTransport(path, options) {
  return IDENTITY_WRITE.test(path) && options.method !== 'GET'
    ? identityLock(() => exchange(path, options, true)) : exchange(path, options, false);
}
