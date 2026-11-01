// @ts-check
/** Cross-tab notifications contain only a kind and nonce, never credentials or account data. */
const STORAGE_EVENT = 'agr_identity_event_v1';
/** @type {BroadcastChannel | null} */
let channel = null;
/** @type {Set<string>} */
const seen = new Set();
const MAX_SEEN_EVENTS = 64;

/** @param {'changed' | 'logout' | 'profile'} [kind] */
export function publishIdentity(kind = 'changed') {
  const event = { kind, nonce: crypto.randomUUID() };
  channel?.postMessage(event);
  try { localStorage.setItem(STORAGE_EVENT, JSON.stringify(event)); } catch { /* BroadcastChannel still works without storage. */ }
}

/** @param {() => void} changed @param {() => void} focused */
export function listenIdentity(changed, focused) {
  /** @param {unknown} value */
  function receive(value) {
    if (!value || typeof value !== 'object') return;
    const event = /** @type {{kind?: unknown, nonce?: unknown}} */ (value);
    if (!['changed', 'logout', 'profile'].includes(String(event.kind)) || typeof event.nonce !== 'string' || seen.has(event.nonce)) return;
    seen.add(event.nonce);
    if (seen.size > MAX_SEEN_EVENTS) seen.delete(/** @type {string} */ (seen.values().next().value));
    if (event.kind === 'profile') focused(); else changed();
  }
  /** @param {StorageEvent} event */
  function storage(event) {
    if (event.key !== STORAGE_EVENT || !event.newValue) return;
    try { receive(JSON.parse(event.newValue)); } catch { /* Ignore malformed local notifications. */ }
  }
  function visible() { if (document.visibilityState === 'visible') focused(); }
  if (typeof BroadcastChannel !== 'undefined') { channel = new BroadcastChannel(STORAGE_EVENT); channel.onmessage = event => receive(event.data); }
  window.addEventListener('storage', storage);
  window.addEventListener('focus', focused);
  document.addEventListener('visibilitychange', visible);
  return () => {
    channel?.close(); channel = null;
    window.removeEventListener('storage', storage);
    window.removeEventListener('focus', focused);
    document.removeEventListener('visibilitychange', visible);
  };
}
