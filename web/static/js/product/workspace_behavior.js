// @ts-check
import { ref, computed, onMounted, onUnmounted, watch } from '../../vendor/vue.esm-browser.prod.js';
import { useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { fetchHealth } from '../api.js';
import { Shortcut, UiPolicy } from '../contracts.js';
import { preferences } from '../store/preferences.js';
import { closeOverlays } from '../store/shell.js';
import { applyInspectorWidth, toggleInspector, stepInspectorHop } from '../shell/use_inspector.js';
import { toggleTheme } from '../theme.js';
import { changingIdentity, logout, switchTenant, user, sessionRevision } from './session.js';
import { refreshKnowledgeBases } from '../store/knowledge.js';
import { useForm } from './forms.js';
import { closePopovers } from './use_notebook.js';
import { T } from './copy.js';

/** @param {ReturnType<typeof import('./use_notebook.js').useNotebook>} notebook @param {ReturnType<typeof import('../shell/use_palette.js').usePalette>} palette */
export function useWorkspaceBehavior(notebook, palette) {
  const router = useRouter(), form = useForm(), creatingTenant = ref(false);
  const offline = ref(false), healthKind = ref('unknown');
  const healthTitle = computed(() => offline.value ? T.states.offline : healthKind.value === 'ok' ? T.topbar.healthOk : T.topbar.healthUnknown);
  async function probe() { try { const status = await fetchHealth(); healthKind.value = status.status === 'ok' ? 'ok' : 'degraded'; offline.value = false; } catch { healthKind.value = 'down'; offline.value = true; } }
  function openCreateTenant() { closePopovers(); creatingTenant.value = true; }
  function closeCreateTenant() { creatingTenant.value = false; }
  /** @param {string} id */
  async function chooseTenant(id) { closePopovers(); await form.run(async () => { await switchTenant(id); await router.replace('/app/chat'); }); }
  async function signOut() { closePopovers(); await form.run(async () => { await logout(); await router.replace('/login'); }); }
  /** @param {KeyboardEvent} event */
  function keydown(event) {
    if (document.querySelector('dialog[open]')) return;
    if (palette.paletteOpen.value && event.key === 'Tab') { event.preventDefault(); palette.paletteFirstEl.value?.focus(); return; }
    const mod = event.metaKey || event.ctrlKey, key = event.key.toLowerCase();
    if (mod && key === Shortcut.PALETTE) { event.preventDefault(); palette.openPaletteDialog(); return; }
    if (mod && key === Shortcut.NEW_QUESTION) { event.preventDefault(); notebook.newQuestion(); return; }
    if (mod && event.shiftKey && key === Shortcut.THEME) { event.preventDefault(); toggleTheme(); return; }
    if (event.key === 'Escape') { closeOverlays(); closePopovers(); return; }
    if (mod || event.altKey || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return;
    if (key === Shortcut.INSPECTOR) toggleInspector();
    if (event.key === Shortcut.PREV_HOP) stepInspectorHop(-1);
    if (event.key === Shortcut.NEXT_HOP) stepInspectorHop(1);
  }
  watch(changingIdentity, changing => { if (changing) { closePopovers(); creatingTenant.value = false; form.message.value = ''; } });
  watch(() => [user.value?.user_id, user.value?.tenant_id, sessionRevision.value, changingIdentity.value], () => {
    if (user.value && !changingIdentity.value) void refreshKnowledgeBases();
  }, { immediate: true });
  onMounted(() => { applyInspectorWidth(preferences.value.inspectorWidth); void probe(); window.addEventListener('keydown', keydown); });
  onUnmounted(() => window.removeEventListener('keydown', keydown));
  return { ...form, creatingTenant, openCreateTenant, closeCreateTenant, chooseTenant, signOut, probe, offline, healthKind, healthTitle, closePopovers, UiPolicy };
}
