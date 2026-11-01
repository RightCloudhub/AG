// @ts-check
/** Shared envelope parsing for both independent entry points. */
import { ErrorCode, ErrorMessage, HttpHeader, MediaType, isRecord } from './contracts.js';
import { parseMeta } from './responses.js';

export class ApiError extends Error {
  /** @param {string} message @param {string} code @param {unknown} [details] @param {number} [status] */
  constructor(message, code, details, status = 0) {
    super(message);
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

/** @param {Response} response @returns {Promise<import('./contracts.js').ApiResult>} */
export async function parseResponse(response) {
  if (!response.ok && !response.headers.get(HttpHeader.CONTENT_TYPE)?.includes(MediaType.JSON)) {
    throw new ApiError(`HTTP ${response.status}`, ErrorCode.HTTP, undefined, response.status);
  }
  /** @type {unknown} */
  const envelope = await response.json();
  if (!isRecord(envelope) || typeof envelope.success !== 'boolean') {
    throw new ApiError(ErrorMessage.INVALID_RESPONSE, ErrorCode.INVALID_RESPONSE);
  }
  if (!response.ok || !envelope.success) {
    const error = isRecord(envelope.error) ? envelope.error : {};
    throw new ApiError(
      typeof error.message === 'string' ? error.message : ErrorMessage.UNKNOWN,
      typeof error.code === 'string' ? error.code : ErrorCode.HTTP,
      error.details, response.status,
    );
  }
  return { data: envelope.data, meta: parseMeta(envelope.meta) };
}
