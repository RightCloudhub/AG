// @ts-check
/**
 * The application: the 推理笔记本 shell (top bar · notebook column · the view · the inspector lives inside the
 * chat view), a hash router gated by role capabilities, the global shortcuts (§8), theme and settings.
 */
import { createApp, ref, onMounted, onUnmounted, shallowRef } from '../vendor/vue.esm-browser.prod.js';
import { KeyboardKey, Role, Shortcut, UiPolicy, View } from './contracts.js';
import { text as T, fmt } from './i18n.js';
import { AgIcon } from './icons.js';
import { initTheme, toggleTheme } from './theme.js';
import { preferences } from './store/preferences.js';
import { closeOverlays, paletteOpen } from './store/shell.js';
import { NAV_ITEMS, useShell } from './shell/use_shell.js';
import { usePalette } from './shell/use_palette.js';
import { applyInspectorWidth, stepInspectorHop, toggleInspector } from './shell/use_inspector.js';
import { BANNER_TEMPLATE, PALETTE_TEMPLATE, SETTINGS_TEMPLATE, TOPBAR_TEMPLATE } from './shell/tpl_topbar.js';
import { NOTEBOOK_TEMPLATE } from './shell/tpl_notebook.js';

/** @typedef {import('./contracts.js').ViewName} ViewName */

/* ── Lazy view imports ─────────────────────────────────────────────── */
const viewModules = {
  [View.CHAT]: () => import('./views/chat.js'),
  [View.GRAPH]: () => import('./admin/graph.js'),
  [View.KNOWLEDGE]: () => import('./admin/knowledge.js'),
  [View.REVIEW]: () => import('./admin/review.js'),
  [View.OPS]: () => import('./admin/ops.js'),
};

/** @param {EventTarget | null} target */
function isTyping(target) {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement
    || (target instanceof HTMLElement && target.isContentEditable);
}

const App = {
  setup() {
    /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<ViewName>} */
    const currentView = ref(View.CHAT);
    /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<import('../vendor/vue.esm-browser.prod.js').Component | null>} */
    const currentComponent = shallowRef(null);
    /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<string | null>} */
    const loadError = ref(null);

    /** @param {ViewName} viewId */
    async function navigate(viewId) {
      if (!viewModules[viewId]) return;
      currentView.value = viewId;
      window.location.hash = viewId;
      closeOverlays();
      loadError.value = null;
      try { currentComponent.value = (await viewModules[viewId]()).default; }
      catch (e) { currentComponent.value = null; loadError.value = String(e); }
    }
    const shell = useShell({ navigate, currentView });
    const palette = usePalette({
      navigate, newQuestion: shell.newQuestion, selectChat: shell.selectChat, setMode: shell.setMode, toggleTheme, capabilities: shell.capabilities,
    });
    function handleHashChange() {
      const target = NAV_ITEMS.find(n => n.id === (window.location.hash.slice(1) || View.CHAT));
      if (target && shell.capabilities.value[target.id]) navigate(target.id);
    }
    /** Global shortcuts (§8): ⌘K · ⌘N · ⌘⇧L with a modifier; E · [ · ] outside inputs; Esc closes what floats. @param {KeyboardEvent} e */
    function onKeydown(e) {
      const mod = e.metaKey || e.ctrlKey, key = e.key.toLowerCase();
      if (mod && key === Shortcut.PALETTE) { e.preventDefault(); palette.openPaletteDialog(); return; }
      if (mod && key === Shortcut.NEW_QUESTION) { e.preventDefault(); shell.newQuestion(); return; }
      if (mod && e.shiftKey && key === Shortcut.THEME) { e.preventDefault(); toggleTheme(); return; }
      if (e.key === KeyboardKey.ESCAPE) { closeOverlays(); if (shell.settingsOpen.value) shell.closeSettings(); return; }
      if (mod || e.altKey || isTyping(e.target)) return;
      if (key === Shortcut.INSPECTOR) { e.preventDefault(); toggleInspector(); }
      else if (e.key === Shortcut.PREV_HOP) stepInspectorHop(-1);
      else if (e.key === Shortcut.NEXT_HOP) stepInspectorHop(1);
    }

    onMounted(async () => {
      initTheme();
      applyInspectorWidth(preferences.value.inspectorWidth);
      shell.watchViewport();
      window.addEventListener('keydown', onKeydown);
      window.addEventListener('hashchange', handleHashChange);
      await shell.loadUser();
      await shell.probe();
      const hash = window.location.hash.slice(1);
      navigate(NAV_ITEMS.find(n => n.id === hash && shell.capabilities.value[n.id])?.id || View.CHAT);
    });
    onUnmounted(() => { window.removeEventListener('keydown', onKeydown); window.removeEventListener('hashchange', handleHashChange); });

    return {
      ...shell, ...palette, openPalette: palette.openPaletteDialog, closePalette: palette.closePaletteDialog, currentView, currentComponent,
      loadError, navigate, handleHashChange, paletteOpen, T, fmt, Role, UiPolicy,
    };
  },

  template: `
<div class="app" :class="{ 'app--insp-collapsed': preferences.inspectorCollapsed }">
  ${TOPBAR_TEMPLATE}
  ${BANNER_TEMPLATE}
  ${NOTEBOOK_TEMPLATE}
  <component v-if="currentComponent" :is="currentComponent" :key="currentView"></component>
  <div v-else class="page"><p v-if="loadError" class="load-error" role="alert">{{ T.app.loadFailed }}: {{ currentView }}
{{ loadError }}</p><div v-else class="empty"><span class="spinner"></span></div></div>
  ${SETTINGS_TEMPLATE}
  ${PALETTE_TEMPLATE}
</div>`,
};

/* ── Mount ─────────────────────────────────────────────────────────── */
const app = createApp(App);
app.component('ag-icon', AgIcon);
app.mount('#app');
