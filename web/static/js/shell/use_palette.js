// @ts-check
/** Command palette (⌘K, §4.2): search past questions, jump to a view, switch mode or theme, start a new question. */
import { ref, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { KeyboardKey, PaletteKind, QueryMode, Theme, UiPolicy } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { theme } from '../theme.js';
import { conversations } from '../store/conversations.js';
import { closePalette, paletteOpen } from '../store/shell.js';
import { NAV_ITEMS } from './use_shell.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../contracts.js').ViewName} ViewName */
/** @typedef {{id: string, kind: import('../contracts.js').PaletteKindName, kindLabel: string, icon: string, label: string, run: () => void}} PaletteEntry */
/**
 * @typedef {object} PaletteDeps
 * @property {(view: ViewName) => void} navigate
 * @property {() => void} newQuestion
 * @property {(id: string) => void} selectChat
 * @property {(mode: string) => void} setMode
 * @property {() => void} toggleTheme
 * @property {Ref<Partial<Record<ViewName, boolean>>>} capabilities
 */

/** @param {string} value */
const norm = value => value.toLowerCase().replace(/\s+/g, ' ').trim();

/** @param {PaletteDeps} deps */
export function usePalette(deps) {
  const paletteQuery = ref('');
  /** @type {Ref<PaletteEntry[]>} */
  const paletteResults = ref([]);
  const paletteActive = ref(0);
  /** @type {Ref<HTMLInputElement | null>} */
  const paletteFirstEl = ref(null);
  /** @type {HTMLElement | null} */
  let lastFocus = null;

  /** Everything the palette can do, in the order it should be offered. @returns {PaletteEntry[]} */
  function entries() {
    const kind = /** @param {import('../contracts.js').PaletteKindName} k */ k => T.palette.kinds[k];
    /** @type {PaletteEntry[]} */
    const out = [{ id: 'new', kind: PaletteKind.ACTION, kindLabel: kind(PaletteKind.ACTION), icon: 'plus', label: T.palette.newQuestion, run: deps.newQuestion }];
    for (const c of conversations.value) {
      out.push({ id: `q:${c.id}`, kind: PaletteKind.QUESTION, kindLabel: kind(PaletteKind.QUESTION), icon: 'chat', label: c.title, run: () => deps.selectChat(c.id) });
    }
    for (const n of NAV_ITEMS) {
      if (deps.capabilities.value[n.id]) out.push({ id: `v:${n.id}`, kind: PaletteKind.VIEW, kindLabel: kind(PaletteKind.VIEW), icon: n.icon, label: n.label, run: () => deps.navigate(n.id) });
    }
    for (const m of Object.values(QueryMode)) {
      out.push({ id: `m:${m}`, kind: PaletteKind.MODE, kindLabel: kind(PaletteKind.MODE), icon: 'plan', label: `${T.topbar.mode} · ${T.modes[m]}`, run: () => deps.setMode(m) });
    }
    const other = theme.value === Theme.DARK ? T.topbar.themeLight : T.topbar.themeDark;
    out.push({ id: 't', kind: PaletteKind.THEME, kindLabel: kind(PaletteKind.THEME), icon: 'contrast', label: fmt(T.palette.switchTheme, { theme: other }), run: deps.toggleTheme });
    return out;
  }
  function search() {
    const q = norm(paletteQuery.value);
    const all = entries();
    const hits = q ? all.filter(e => norm(e.label).includes(q) || norm(e.kindLabel).includes(q)) : all;
    paletteResults.value = hits.slice(0, UiPolicy.PALETTE_LIMIT);
    paletteActive.value = 0;
  }
  function onPaletteInput() { search(); }
  function openPaletteDialog() {
    lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    paletteQuery.value = '';
    search();
    paletteOpen.value = true;
    nextTick(() => paletteFirstEl.value?.focus());
  }
  function closePaletteDialog() { closePalette(); lastFocus?.focus(); }
  /** @param {PaletteEntry} entry */
  function runPalette(entry) { closePaletteDialog(); entry.run(); }
  /** @param {KeyboardEvent} e */
  function paletteKeydown(e) {
    const n = paletteResults.value.length;
    if (e.key === KeyboardKey.ESCAPE) { e.preventDefault(); closePaletteDialog(); return; }
    if (!n) return;
    if (e.key === KeyboardKey.ARROW_DOWN) { e.preventDefault(); paletteActive.value = (paletteActive.value + 1) % n; }
    else if (e.key === KeyboardKey.ARROW_UP) { e.preventDefault(); paletteActive.value = (paletteActive.value - 1 + n) % n; }
    else if (e.key === KeyboardKey.ENTER) { e.preventDefault(); runPalette(paletteResults.value[paletteActive.value]); }
  }

  return { paletteOpen, paletteQuery, paletteResults, paletteActive, paletteFirstEl, onPaletteInput, openPaletteDialog, closePaletteDialog, runPalette, paletteKeydown };
}
