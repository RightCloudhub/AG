// @ts-check
import { computed, ref, watch, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { useRoute, useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { QueryMode, UiPolicy, View } from '../contracts.js';
import { activeConversationId, conversations, findConversation, groupConversations, hopCountOf, removeConversation, startNewConversation } from '../store/conversations.js';
import { preferences, setPreferences } from '../store/preferences.js';
import { closeNotebook, closeOverlays, notebookOpen, openNotebook } from '../store/shell.js';
import { inspSyncTo } from '../shell/use_inspector.js';
import { NAV_ITEMS } from '../shell/use_shell.js';
import { user, changingIdentity, sessionError } from './session.js';
import { T } from './copy.js';

export function closePopovers() {
  document.querySelectorAll('[popover]:popover-open').forEach(element => { if (element instanceof HTMLElement) element.hidePopover(); });
}
function usePhoneNotebook() {
  const media = window.matchMedia(`(max-width: ${UiPolicy.TABLET_PX - 1}px)`);
  const phone = ref(media.matches);
  /** @param {MediaQueryListEvent} event */
  const resize = event => { phone.value = event.matches; if (!event.matches) closeNotebook(); };
  onMounted(() => media.addEventListener('change', resize));
  onUnmounted(() => media.removeEventListener('change', resize));
  return computed(() => phone.value && !notebookOpen.value);
}
export function useNotebook() {
  const route = useRoute(), router = useRouter();
  const capabilities = computed(() => user.value?.capabilities || {});
  const adminNav = computed(() => NAV_ITEMS.filter(item => item.id !== View.CHAT && capabilities.value[item.id]));
  const currentView = computed(() => route.path.split('/')[2]);
  const tenant = computed(() => user.value?.tenants.find(item => item.id === user.value?.tenant_id));
  const groups = computed(() => groupConversations(conversations.value, Date.now()));
  const mode = computed(() => preferences.value.mode);
  const modeOptions = Object.values(QueryMode).map(id => ({ id, label: T.modes[id] }));
  /** @param {string} value */
  function setMode(value) { const next = Object.values(QueryMode).find(id => id === value); if (next) setPreferences({ mode: next }); }
  function closeNavigation() { closeOverlays(); closePopovers(); }
  /** @param {import('../contracts.js').ViewName} view */
  function navigate(view) { closeNavigation(); void router.push(`/app/${view}`); }
  function newQuestion() { startNewConversation(); inspSyncTo(null); navigate(View.CHAT); }
  /** @param {string} id */
  function selectChat(id) { closeNavigation(); void router.push(`/app/chat/${encodeURIComponent(id)}`); }
  /** @param {string} id */
  function removeChat(id) { const active = activeConversationId.value === id; removeConversation(id); if (active) newQuestion(); }
  watch(() => [route.fullPath, changingIdentity.value, sessionError.value], () => {
    if (route.name !== 'chat' || changingIdentity.value || sessionError.value) return;
    const id = typeof route.params.id === 'string' ? route.params.id : null;
    activeConversationId.value = findConversation(id)?.id || null;
    inspSyncTo(findConversation(activeConversationId.value));
  }, { immediate: true });
  watch(activeConversationId, id => {
    if (route.name === 'chat' && !changingIdentity.value && (route.params.id || null) !== id) void router.replace(id ? `/app/chat/${encodeURIComponent(id)}` : '/app/chat');
  });
  return { capabilities, adminNav, currentView, tenant, groups, mode, modeOptions, setMode, navigate, newQuestion, selectChat, removeChat,
    notebookHidden: usePhoneNotebook(), notebookOpen, openNotebook, closeNotebook, conversations, activeConversationId, hopCount: hopCountOf };
}
