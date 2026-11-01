// @ts-check
/** Identity owns view lifetime. No authentication material is stored in browser storage. */
import { ref } from '../../vendor/vue.esm-browser.prod.js';
import { ApiError } from '../http.js';
import { request, installTransport } from '../transport.js';
import { cancelRequests } from '../request_scope.js';
import { activeConversationId, clearAccountHistory, findConversation, selectHistoryOwner } from '../store/conversations.js';
import { closeOverlays } from '../store/shell.js';
import { setGraphFocus } from '../store/graph_focus.js';
import { inspSyncTo } from '../shell/use_inspector.js';
import { cookieTransport, acceptCsrf, forgetCsrf, onCookieIdentity } from './cookie_transport.js';
import { parseUser, safeNext } from './contracts.js';
import { listenIdentity, publishIdentity } from './identity_events.js';

/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('./contracts.js').User | null>} */
export const user = ref(null);
export const sessionReady = ref(false);
export const sessionRevision = ref(0);
export const changingIdentity = ref(false);
/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<unknown>} */
export const sessionError = ref(null);
/** @type {Promise<void> | null} */
let flight = null;
let signedOutDestination = '';
/** @type {Promise<void> | null} */
let syncFlight = null;
let syncAgain = false;
/** @type {(() => void) | null} */
let stopEvents = null;
let identityWork = 0;
let viewsCleared = true;

function beginChange() { identityWork += 1; changingIdentity.value = true; clearViews(); }
function endChange() { identityWork -= 1; changingIdentity.value = identityWork > 0; }

/** An explicit account action wins over the automatic session-expired redirect. @param {string} fullPath */
export function loginDestination(fullPath) { return signedOutDestination || `/login?next=${encodeURIComponent(safeNext(fullPath))}`; }

function clearViews() {
  viewsCleared = true;
  cancelRequests();
  selectHistoryOwner(null);
  closeOverlays();
  inspSyncTo(null);
  setGraphFocus('');
  sessionRevision.value += 1;
}
/** @param {import('./contracts.js').User | null} next */
export function applyUser(next) {
  const previous = user.value;
  const changed = previous?.user_id !== next?.user_id || previous?.tenant_id !== next?.tenant_id || previous?.role !== next?.role;
  if (changed && previous) clearViews();
  user.value = next;
  if (next) signedOutDestination = '';
  if (changed || viewsCleared) { selectHistoryOwner(next?.user_id ?? null, next?.tenant_id); viewsCleared = false; }
  sessionReady.value = true;
  sessionError.value = null;
}
/** @param {boolean} [clearHistory] @param {string} [destination] */
export function clearSession(clearHistory = true, destination = '') {
  signedOutDestination = destination;
  if (clearHistory && user.value) clearAccountHistory(user.value.user_id);
  clearViews();
  user.value = null;
  sessionReady.value = true;
  forgetCsrf();
}

export function initializeSession() {
  installTransport(cookieTransport);
  onCookieIdentity({ identity: value => applyUser(parseUser(value)), expired: () => clearSession(), expected: () => user.value,
    changed: () => { void synchronizeSession(); } });
  stopEvents?.();
  stopEvents = listenIdentity(() => { void synchronizeSession(); }, () => { if (!changingIdentity.value) void reloadSession(); });
}
export function disposeSession() { stopEvents?.(); stopEvents = null; }
export function reloadSession() {
  if (!flight) flight = (async () => {
    try { applyUser(parseUser((await request('/v1/me')).data)); }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) clearSession();
      else if (!(error instanceof Error && error.name === 'AbortError')) sessionError.value = error;
      sessionReady.value = true;
    }
  })().finally(() => { flight = null; });
  return flight;
}
export async function ensureSession() { if (!sessionReady.value) await reloadSession(); }

/** Freeze immediately, then reconcile shared Cookies. A second notification schedules another read. */
export function synchronizeSession() {
  syncAgain = true;
  if (syncFlight) return syncFlight;
  const previous = user.value, active = activeConversationId.value;
  beginChange();
  forgetCsrf();
  syncFlight = (async () => {
    do {
      syncAgain = false;
      if (flight) await flight;
      await reloadSession();
    } while (syncAgain);
    if (!sessionError.value && previous?.user_id === user.value?.user_id && previous?.tenant_id === user.value?.tenant_id && findConversation(active)) activeConversationId.value = active;
  })().finally(() => { endChange(); syncFlight = null; });
  return syncFlight;
}

/** @param {'login' | 'register'} action @param {Record<string, string>} body */
export async function authenticate(action, body) {
  const result = await request(`/v1/auth/${action}`, { method: 'POST', body: JSON.stringify(body) });
  acceptCsrf(result.data);
  applyUser(parseUser(result.data));
  publishIdentity();
}
export async function logout() {
  await request('/v1/auth/logout', { method: 'POST' });
  clearSession(true, '/login');
  publishIdentity('logout');
}
/** @param {string} id */
export async function switchTenant(id) {
  if (changingIdentity.value || id === user.value?.tenant_id) return;
  beginChange();
  try {
    const result = await request(`/v1/tenants/${encodeURIComponent(id)}/switch`, { method: 'POST' });
    acceptCsrf(result.data);
    applyUser(parseUser(result.data));
    await reloadSession();
    publishIdentity();
  } catch (error) {
    await reloadSession();
    throw error;
  } finally { endChange(); }
}
