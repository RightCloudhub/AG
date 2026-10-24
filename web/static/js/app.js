// @ts-check
/**
 * Main application — Vue 3 SPA with hash router and role-aware navigation.
 */
import { createApp, ref, computed, onMounted, shallowRef }
  from '../vendor/vue.esm-browser.prod.js';
import { View, Role, HealthStatus } from './contracts.js';
import { fetchMe, fetchHealth, getApiKey, setApiKey } from './api.js';

/* ── Lazy view imports ─────────────────────────────────────────────── */
const viewModules = {
  [View.CHAT]:      () => import('./views/chat.js'),
  [View.GRAPH]:     () => import('./views/graph.js'),
  [View.KNOWLEDGE]: () => import('./views/knowledge.js'),
  [View.REVIEW]:    () => import('./views/review.js'),
  [View.OPS]:       () => import('./views/ops.js'),
};

/* ── Navigation config ─────────────────────────────────────────────── */
const NAV_ITEMS = [
  { id: View.CHAT,      icon: '💬', label: '对话',      capability: View.CHAT },
  { id: View.GRAPH,     icon: '🕸', label: '知识图谱',     capability: View.GRAPH },
  { id: View.KNOWLEDGE, icon: '📚', label: '知识库', capability: View.KNOWLEDGE },
  { id: View.REVIEW,    icon: '🔍', label: '人工审核',    capability: View.REVIEW },
  { id: View.OPS,       icon: '📡', label: '系统运维', capability: View.OPS },
];

/* ── App component ─────────────────────────────────────────────────── */
function useAppState() {
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<import('./contracts.js').ViewName>} */
  const currentView = ref(View.CHAT);
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<import('./responses.js').Identity | null>} */
  const user = ref(null);
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<Partial<Record<import('./contracts.js').ViewName, boolean>>>} */
  const capabilities = ref({});
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<import('./responses.js').Health | null>} */
  const health = ref(null);
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<import('../vendor/vue.esm-browser.prod.js').Component | null>} */
  const currentComponent = shallowRef(null);
  const sidebarOpen = ref(false);
  const apiKey = ref(getApiKey());
  /** @type {import('../vendor/vue.esm-browser.prod.js').Ref<string | null>} */
  const loadError = ref(null);

  const visibleNav = computed(() =>
    NAV_ITEMS.filter(n => capabilities.value[n.capability])
  );

  async function loadUser() {
    try {
      const { data } = await fetchMe();
      user.value = data;
      capabilities.value = data.capabilities || {};
    } catch {
      // Unauthenticated — show all reader capabilities
      capabilities.value = { [View.CHAT]: true, [View.GRAPH]: true };
    }
  }

  async function loadHealth() {
    try { health.value = await fetchHealth(); }
    catch { health.value = null; }
  }

  function userInitial() {
    const uid = user.value?.user_id || 'A';
    return uid.charAt(0).toUpperCase();
  }

  return { currentView, user, capabilities, health, currentComponent, sidebarOpen, apiKey, loadError, visibleNav, loadUser, loadHealth, userInitial };
}

const App = {
  setup() {
    const { currentView, user, capabilities, health, currentComponent, sidebarOpen, apiKey, loadError, visibleNav, loadUser, loadHealth, userInitial } = useAppState();

    /** @param {string} key */
    async function updateApiKey(key) {
      apiKey.value = key;
      setApiKey(key);
      await loadUser();
      handleHashChange();
    }

    /** @param {import('./contracts.js').ViewName} viewId */
    async function navigate(viewId) {
      if (!viewModules[viewId]) return;
      currentView.value = viewId;
      window.location.hash = viewId;
      sidebarOpen.value = false;
      loadError.value = null;
      try {
        const mod = await viewModules[viewId]();
        currentComponent.value = mod.default;
      } catch (e) {
        currentComponent.value = null;
        loadError.value = String(e);
      }
    }

    function handleHashChange() {
      const target = NAV_ITEMS.find(n => n.id === (window.location.hash.slice(1) || View.CHAT));
      if (target && capabilities.value[target.capability]) navigate(target.id);
    }

    onMounted(async () => {
      await loadUser();
      await loadHealth();
      window.addEventListener('hashchange', handleHashChange);
      const hash = window.location.hash.slice(1);
      const target = NAV_ITEMS.find(n => n.id === hash && capabilities.value[n.capability])?.id || View.CHAT;
      navigate(target);
    });

    return {
      currentView, user, capabilities, health, currentComponent,
      visibleNav, navigate, sidebarOpen, userInitial, NAV_ITEMS,
      apiKey, updateApiKey, loadError, Role, HealthStatus,
    };
  },

  template: `
<div class="app-shell">
  <!-- Mobile menu button -->
  <button class="btn btn--icon btn--ghost"
          style="position:fixed;top:12px;left:12px;z-index:30;display:none;"
          :style="{ display: sidebarOpen ? 'none' : '' }"
          @click="sidebarOpen = true"
          id="mobile-menu-btn">☰</button>

  <!-- Sidebar -->
  <aside class="sidebar" :class="{ 'sidebar--open': sidebarOpen }">
    <div class="sidebar__brand">
      <div class="sidebar__logo">G</div>
      <div>
        <div class="sidebar__title">AgenticGraphRAG</div>
        <div class="sidebar__subtitle">智能问答控制台</div>
      </div>
    </div>

    <nav class="sidebar__nav">
      <div v-for="nav in visibleNav" :key="nav.id"
           class="nav-item" :class="{ 'nav-item--active': currentView === nav.id }"
           @click="navigate(nav.id)">
        <span class="nav-item__icon">{{ nav.icon }}</span>
        <span>{{ nav.label }}</span>
      </div>
    </nav>

    <div class="sidebar__footer">
      <div v-if="(!user || !user.authenticated)" style="margin-bottom:var(--space-3);">
        <input class="input" style="padding:var(--space-2); font-size:var(--text-xs);" 
               type="password" placeholder="输入 API Key..." 
               :value="apiKey" @input="updateApiKey($event.target.value)" />
      </div>
      <div class="sidebar__user" v-if="user && user.authenticated">
        <div class="sidebar__avatar">{{ userInitial() }}</div>
        <div>
          <div style="color:var(--color-text-primary);font-weight:600;">{{ user.user_id || '匿名用户' }}</div>
          <div style="color:var(--color-text-muted);font-size:11px;display:flex;align-items:center;gap:4px;">
            <span class="badge badge--accent" style="font-size:9px;padding:1px 4px;">{{ user.role || Role.READER }}</span>
            {{ user.tenant_id || 'default' }}
          </div>
        </div>
      </div>
      <div v-if="health" style="margin-top:var(--space-3);display:flex;align-items:center;gap:var(--space-2);padding:0 var(--space-3);">
        <span style="width:6px;height:6px;border-radius:50%;display:inline-block;"
              :style="{ background: health.status === HealthStatus.OK ? 'var(--color-success)' : 'var(--color-warning)' }"></span>
        <span style="font-size:var(--text-xs);color:var(--color-text-muted);">{{ health.status }}</span>
      </div>
    </div>
  </aside>

  <!-- Backdrop for mobile -->
  <div v-if="sidebarOpen" style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:15;"
       @click="sidebarOpen = false"></div>

  <!-- Main content -->
  <main class="main-content">
    <component v-if="currentComponent" :is="currentComponent" :key="currentView"></component>
    <div v-else-if="loadError" class="empty-state" style="flex:1;">
      <div class="empty-state__icon">⚠</div>
      <div class="empty-state__text" style="color:var(--color-danger);">视图加载失败: {{ currentView }}</div>
      <pre style="margin-top:12px;font-size:11px;color:var(--color-text-muted);text-align:left;max-width:400px;white-space:pre-wrap;">{{ loadError }}</pre>
    </div>
    <div v-else class="empty-state" style="flex:1;">
      <div class="spinner spinner--lg"></div>
    </div>
  </main>
</div>
`
};

/* ── Mount ─────────────────────────────────────────────────────────── */
const app = createApp(App);
app.mount('#app');
