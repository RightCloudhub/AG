// @ts-check
/** A scope spans fetch, decoding and streaming, including servers that ignore abort. */
let generation = 0;
/** @type {Set<AbortController>} */
const pending = new Set();
export function requestGeneration() { return generation; }
/** @param {number} value */
export function isCurrentRequest(value) { return value === generation; }
export function cancelRequests() {
  generation += 1;
  for (const controller of pending) controller.abort();
  pending.clear();
}
/** @param {number} value @param {AbortSignal | null} [signal] */
export function assertCurrentRequest(value, signal) {
  if (!isCurrentRequest(value) || signal?.aborted) throw new DOMException('Request cancelled', 'AbortError');
}
/** @template T @param {RequestInit} options @param {(options: RequestInit) => Promise<T>} run */
export async function scopedRequest(options, run) {
  const current = generation;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, { once: true });
  if (options.signal?.aborted) cancel();
  pending.add(controller);
  try {
    assertCurrentRequest(current, controller.signal);
    const value = await run({ ...options, signal: controller.signal });
    assertCurrentRequest(current, controller.signal);
    return value;
  } catch (error) {
    assertCurrentRequest(current, controller.signal);
    throw error;
  } finally {
    pending.delete(controller);
    options.signal?.removeEventListener('abort', cancel);
  }
}
