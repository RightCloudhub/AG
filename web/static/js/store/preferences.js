// @ts-check
/** Persisted UI preferences: the inspector fold and width, the query mode, the hop budget. */
import { ref } from '../../vendor/vue.esm-browser.prod.js';
import { QueryMode, StorageKey, UiPolicy, isRecord } from '../contracts.js';

/**
 * @typedef {object} Preferences
 * @property {boolean} inspectorCollapsed
 * @property {number} inspectorWidth
 * @property {import('../contracts.js').QueryModeName} mode
 * @property {number} maxHops
 */

/** @returns {Preferences} */
function defaults() {
  return {
    inspectorCollapsed: false, inspectorWidth: UiPolicy.INSPECTOR_DEFAULT_PX,
    mode: QueryMode.AUTO, maxHops: UiPolicy.DEFAULT_MAX_HOPS,
  };
}

/** @param {unknown} value @param {number} lo @param {number} hi @param {number} fallback */
function bounded(value, lo, hi, fallback) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(hi, Math.max(lo, Math.round(value))) : fallback;
}

/** @returns {Preferences} */
function load() {
  const base = defaults();
  try {
    /** @type {unknown} */
    const parsed = JSON.parse(localStorage.getItem(StorageKey.PREFERENCES) || '{}');
    if (!isRecord(parsed)) return base;
    const modes = Object.values(QueryMode);
    return {
      inspectorCollapsed: typeof parsed.inspectorCollapsed === 'boolean' ? parsed.inspectorCollapsed : base.inspectorCollapsed,
      inspectorWidth: bounded(parsed.inspectorWidth, UiPolicy.INSPECTOR_MIN_PX, UiPolicy.INSPECTOR_MAX_PX, base.inspectorWidth),
      mode: modes.find(m => m === parsed.mode) ?? base.mode,
      maxHops: bounded(parsed.maxHops, UiPolicy.MIN_MAX_HOPS, UiPolicy.MAX_MAX_HOPS, base.maxHops),
    };
  } catch { return base; }
}

/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<Preferences>} */
export const preferences = ref(load());

/** @param {Partial<Preferences>} patch */
export function setPreferences(patch) {
  preferences.value = { ...preferences.value, ...patch };
  try { localStorage.setItem(StorageKey.PREFERENCES, JSON.stringify(preferences.value)); } catch { /* storage unavailable */ }
}
