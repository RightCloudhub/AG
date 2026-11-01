// @ts-check
/** Core keeps its key transport; only the independent Web entry installs Cookies. */
import { HttpHeader, MediaType, StorageKey } from './contracts.js';
import { parseResponse } from './http.js';
import { scopedRequest } from './request_scope.js';

/** @typedef {(path: string, options: RequestInit) => Promise<Response>} Transport */
/** @type {Transport} */
let transport = async (path, options) => {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData)) headers.set(HttpHeader.CONTENT_TYPE, MediaType.JSON);
  const key = localStorage.getItem(StorageKey.API_KEY);
  if (key) headers.set(HttpHeader.AUTHORIZATION, `Bearer ${key}`);
  return fetch(path, { ...options, headers });
};

/** @param {Transport} send */
export function installTransport(send) { transport = send; }
/** @param {string} path @param {RequestInit} [options] */
export function sendRequest(path, options = {}) { return transport(path, options); }
/** @param {string} path @param {RequestInit} [options] */
export function request(path, options = {}) {
  return scopedRequest(options, async scoped => parseResponse(await sendRequest(path, scoped)));
}
