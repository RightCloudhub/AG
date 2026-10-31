// @ts-check
/**
 * App-shell behaviour: identity and health, the query mode and hop budget, the theme, the settings dialog,
 * the notebook column (history by day, the admin group by role, the phone sheet).
 */
import { ref, computed, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { HealthStatus, QueryMode, Role, UiPolicy, View } from '../contracts.js';
import { fetchHealth, fetchMe, getApiKey, setApiKey } from '../api.js';
import { text as T } from '../i18n.js';
import { theme, toggleTheme } from '../theme.js';
import {
  activeConversationId, conversations, findConversation, groupConversations, hopCountOf, removeConversation, startNewConversation,
} from '../store/conversations.js';
import { preferences, setPreferences } from '../store/preferences.js';
import { closeNotebook, closePalette, notebookOpen, openNotebook, openPalette, paletteOpen } from '../store/shell.js';
import { inspSyncTo } from './use_inspector.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../contracts.js').ViewName} ViewName */

/** Navigation: the chat is the home; the rest form the notebook's 管理 group, shown by capability. */
export const NAV_ITEMS = Object.freeze([
  { id: View.CHAT, icon: 'chat', label: T.nav.chat },
  { id: View.GRAPH, icon: 'graph', label: T.nav.graph },
  { id: View.KNOWLEDGE, icon: 'knowledge', label: T.nav.knowledge },
  { id: View.REVIEW, icon: 'review', label: T.nav.review },
  { id: View.OPS, icon: 'ops', label: T.nav.ops },
]);

/** @param {{navigate: (view: ViewName) => void, currentView: Ref<ViewName>}} deps */
export function useShell(deps) {
  /** @type {Ref<import('../responses.js').Identity | null>} */
  const user = ref(null);
  /** @type {Ref<Partial<Record<ViewName, boolean>>>} */
  const capabilities = ref({});
  /** @type {Ref<import('../responses.js').Health | null>} */
  const health = ref(null);
  const offline = ref(false);
  const apiKey = ref(getApiKey());
  const settingsOpen = ref(false);
  /** @type {Ref<HTMLInputElement | null>} */
  const settingsFirstEl = ref(null);
  const phone = ref(false);
  /** @type {HTMLElement | null} */
  let lastFocus = null;

  const mode = computed(() => preferences.value.mode);
  const modeOptions = Object.values(QueryMode).map(id => ({ id, label: T.modes[id] }));
  const adminNav = computed(() => NAV_ITEMS.filter(n => n.id !== View.CHAT && capabilities.value[n.id]));
  const visibleNav = computed(() => NAV_ITEMS.filter(n => capabilities.value[n.id]));
  const groups = computed(() => groupConversations(conversations.value, Date.now()));
  const notebookHidden = computed(() => phone.value && !notebookOpen.value);
  const healthKind = computed(() => {
    if (offline.value) return 'down';
    if (!health.value) return 'unknown';
    return health.value.status === HealthStatus.OK ? 'ok' : 'degraded';
  });
  const healthTitle = computed(() => {
    if (offline.value) return T.topbar.healthDown;
    const h = health.value;
    if (!h) return T.topbar.healthUnknown;
    const checks = Object.entries(h.checks).map(([k, v]) => `${k}: ${String(v)}`).join(' · ');
    const label = h.status === HealthStatus.OK ? T.topbar.healthOk : T.topbar.healthDegraded;
    return checks ? `${label} · ${checks}` : label;
  });

  async function loadUser() {
    try {
      const { data } = await fetchMe();
      user.value = data;
      capabilities.value = data.capabilities || {};
    } catch {
      // Unauthenticated: the reader capabilities
      capabilities.value = { [View.CHAT]: true, [View.GRAPH]: true };
    }
  }
  /** Health is the connection probe: a failed /healthz shows the banner (§6 断网或 5xx). */
  async function probe() {
    try { health.value = await fetchHealth(); offline.value = false; }
    catch { health.value = null; offline.value = true; }
  }
  /** @param {string} key */
  async function updateApiKey(key) {
    apiKey.value = key;
    setApiKey(key);
    await loadUser();
    await probe();
  }
  /** @param {string} value */
  function setMode(value) {
    const next = Object.values(QueryMode).find(m => m === value);
    if (next) setPreferences({ mode: next });
  }
  /** @param {string} raw */
  function setMaxHops(raw) {
    const n = Number.parseInt(raw, 10);
    if (Number.isFinite(n)) setPreferences({ maxHops: Math.min(UiPolicy.MAX_MAX_HOPS, Math.max(UiPolicy.MIN_MAX_HOPS, n)) });
  }
  function newQuestion() { startNewConversation(); inspSyncTo(null); closeNotebook(); closePalette(); deps.navigate(View.CHAT); }
  /** @param {string} id */
  function selectChat(id) { activeConversationId.value = id; inspSyncTo(findConversation(id)); closeNotebook(); deps.navigate(View.CHAT); }
  /** @param {string} id */
  function removeChat(id) { removeConversation(id); }
  function openSettings() {
    lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeNotebook();
    settingsOpen.value = true;
    nextTick(() => settingsFirstEl.value?.focus());
  }
  function closeSettings() { settingsOpen.value = false; lastFocus?.focus(); }
  /** Below 768px the notebook is a sheet; track the band so aria-hidden follows it. */
  function watchViewport() {
    const mq = window.matchMedia(`(max-width: ${UiPolicy.TABLET_PX - 1}px)`);
    phone.value = mq.matches;
    mq.addEventListener?.('change', e => { phone.value = e.matches; if (!e.matches) closeNotebook(); });
  }

  return {
    user, capabilities, health, offline, apiKey, settingsOpen, settingsFirstEl, phone, theme, mode, modeOptions, adminNav, visibleNav,
    groups, conversations, activeConversationId, notebookOpen, notebookHidden, paletteOpen, preferences, healthKind, healthTitle,
    loadUser, probe, updateApiKey, setMode, setMaxHops, newQuestion, selectChat, removeChat, openSettings, closeSettings,
    openNotebook, closeNotebook, openPalette, closePalette, toggleTheme, watchViewport, hopCount: hopCountOf, Role, UiPolicy,
  };
}
