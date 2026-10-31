// @ts-check
/**
 * Theme: 靛夜 (dark) by default, 晨纸 (light) when the system prefers it, and a manual choice that is
 * persisted (localStorage.agr_theme). The CSS already follows the system preference before this runs.
 */
import { ref } from '../vendor/vue.esm-browser.prod.js';
import { StorageKey, Theme } from './contracts.js';

/** @typedef {import('./contracts.js').ThemeName} ThemeName */

const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: light)') : null;

/** @returns {ThemeName | null} */
function stored() {
  try {
    const value = localStorage.getItem(StorageKey.THEME);
    return value === Theme.DARK || value === Theme.LIGHT ? value : null;
  } catch { return null; }
}

/** @returns {ThemeName} */
function system() { return media?.matches ? Theme.LIGHT : Theme.DARK; }

/** @type {import('../vendor/vue.esm-browser.prod.js').Ref<ThemeName>} */
export const theme = ref(stored() ?? system());

/** @param {ThemeName} value */
function apply(value) { document.documentElement.dataset.theme = value; }

/** @param {ThemeName} value */
export function setTheme(value) {
  theme.value = value;
  apply(value);
  try { localStorage.setItem(StorageKey.THEME, value); } catch { /* storage unavailable: the choice lasts this session */ }
}

export function toggleTheme() { setTheme(theme.value === Theme.DARK ? Theme.LIGHT : Theme.DARK); }

/** Apply the current theme and keep following the system until the user chooses one. */
export function initTheme() {
  apply(theme.value);
  media?.addEventListener?.('change', () => { if (!stored()) { theme.value = system(); apply(theme.value); } });
}
