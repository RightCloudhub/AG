/* Workspace shell: identity, role-aware rail, hash routing, theme, connection settings. */
import { fetchHealth, fetchMe, getApiKey, setApiKey } from "./api.js";
import { vue } from "./runtime.js";
import { applyTheme, currentTheme, workspace } from "./store.js";
import { DEFAULT_VIEW, findView, groupedViews, visibleViews } from "./views/registry.js";

const HEALTH_POLL_MS = 30000;
const ROLE_LABELS = { admin: "管理员", operator: "运维", reader: "只读" };

export const rootComponent = {
  name: "WorkspaceRoot",
  setup() {
    const { ref, shallowRef, computed, markRaw, onMounted, onBeforeUnmount } = vue();
    const ws = workspace();
    const current = ref("");
    const component = shallowRef(null);
    const loading = ref(true);
    const identityFailure = ref(""); // "" | "auth" | "network"
    const viewError = ref("");
    const collapsed = ref(localStorage.getItem("agr_rail") === "collapsed");
    const drawer = ref(false);
    const settingsOpen = ref(false);
    const keyDraft = ref("");
    const hasKey = ref(Boolean(getApiKey()));
    const theme = ref(currentTheme());
    const cache = new Map();
    let timer = 0;

    const capabilities = computed(() => ws.me?.capabilities || {});
    const groups = computed(() => groupedViews(capabilities.value));
    const view = computed(() => findView(current.value));
    const roleLabel = computed(() => ROLE_LABELS[ws.me?.role] || ws.me?.role || "访客");
    const ready = computed(() => !loading.value && !identityFailure.value && !viewError.value);
    const healthState = computed(() => {
      const status = ws.health?.status;
      if (!ws.health) return { cls: "health-dot--down", text: "服务不可达" };
      return status === "ok"
        ? { cls: "health-dot--ok", text: `服务正常 · ${ws.health.checks?.graph_backend || ""}` }
        : { cls: "health-dot--warn", text: `服务降级 · ${status}` };
    });

    function canOpenView(id) {
      return visibleViews(capabilities.value).some((v) => v.id === id);
    }

    async function open(id) {
      const target = canOpenView(id) ? id : DEFAULT_VIEW;
      drawer.value = false;
      viewError.value = "";
      if (window.location.hash.slice(1) !== target) window.location.hash = target;
      if (current.value === target && component.value) return;
      current.value = target;
      try {
        if (!cache.has(target)) cache.set(target, markRaw((await findView(target).load()).default));
        component.value = cache.get(target);
      } catch (err) {
        component.value = null;
        viewError.value = String(err && err.message ? err.message : err);
      }
    }

    async function loadIdentity() {
      loading.value = true;
      identityFailure.value = "";
      try {
        ws.me = (await fetchMe()).data;
      } catch (err) {
        ws.me = null;
        identityFailure.value = err.isAuth ? "auth" : "network";
      } finally {
        loading.value = false;
      }
      if (!identityFailure.value) await open(window.location.hash.slice(1) || DEFAULT_VIEW);
    }

    async function pollHealth() {
      try {
        ws.health = await fetchHealth();
      } catch {
        ws.health = null;
      }
    }

    function saveKey() {
      setApiKey(keyDraft.value);
      hasKey.value = Boolean(getApiKey());
      keyDraft.value = "";
      settingsOpen.value = false;
      cache.clear();
      component.value = null;
      current.value = "";
      loadIdentity();
    }

    function clearKey() {
      keyDraft.value = "";
      saveKey();
    }

    function toggleTheme() {
      theme.value = theme.value === "dark" ? "light" : "dark";
      applyTheme(theme.value);
    }

    function toggleRail() {
      collapsed.value = !collapsed.value;
      localStorage.setItem("agr_rail", collapsed.value ? "collapsed" : "expanded");
    }

    const onHash = () => ws.me && open(window.location.hash.slice(1) || DEFAULT_VIEW);

    onMounted(() => {
      loadIdentity();
      pollHealth();
      timer = window.setInterval(pollHealth, HEALTH_POLL_MS);
      window.addEventListener("hashchange", onHash);
    });
    onBeforeUnmount(() => {
      window.clearInterval(timer);
      window.removeEventListener("hashchange", onHash);
    });

    return {
      ws, current, component, loading, identityFailure, viewError, collapsed, drawer,
      settingsOpen, keyDraft, hasKey, theme, groups, view, roleLabel, healthState, ready,
      canOpenView, open, loadIdentity, saveKey, clearKey, toggleTheme, toggleRail,
    };
  },
  template: `
<div class="shell" :class="{ 'shell--collapsed': collapsed, 'shell--drawer': drawer }">
  <aside class="rail" aria-label="工作台导航">
    <div class="rail-brand">
      <div class="rail-brand__mark"><app-icon name="graph" /></div>
      <div class="rail-hide">
        <div class="rail-brand__name">知识推理工作台</div>
        <div class="rail-brand__sub">AgenticGraphRAG</div>
      </div>
    </div>
    <template v-for="g in groups" :key="g.group">
      <div class="rail-section rail-hide">{{ g.group }}</div>
      <nav class="rail-nav">
        <button v-for="v in g.views" :key="v.id" class="rail-link" :class="{ 'rail-link--active': current === v.id }"
                :aria-current="current === v.id ? 'page' : null" :title="v.label" @click="open(v.id)">
          <app-icon :name="v.icon" /><span class="rail-hide">{{ v.label }}</span>
          <span v-if="v.id === 'review' && ws.reviewPending" class="badge badge--warn rail-link__count rail-hide">{{ ws.reviewPending }}</span>
        </button>
      </nav>
    </template>
    <div class="rail-footer">
      <div class="rail-health" :title="healthState.text"><span class="health-dot" :class="healthState.cls"></span><span class="rail-hide">{{ healthState.text }}</span></div>
      <div v-if="ws.me" class="rail-identity">
        <div class="avatar">{{ (ws.me.user_id || 'A').charAt(0).toUpperCase() }}</div>
        <div class="rail-identity__text rail-hide">
          <div class="rail-identity__name">{{ ws.me.authenticated ? ws.me.user_id : '匿名访客' }}</div>
          <div class="muted" style="font-size:11px">{{ roleLabel }} · {{ ws.me.tenant_id }}</div>
        </div>
      </div>
      <button class="rail-link" @click="settingsOpen = !settingsOpen" :aria-expanded="settingsOpen"><app-icon name="key" /><span class="rail-hide">连接设置</span></button>
      <form v-if="settingsOpen" class="field rail-hide" @submit.prevent="saveKey">
        <label for="apiKey">API Key（仅保存在本机浏览器）</label>
        <input id="apiKey" class="input" type="password" autocomplete="off" v-model="keyDraft" :placeholder="hasKey ? '已保存，输入新值以替换' : 'Bearer 密钥'" />
        <div style="display:flex;gap:6px"><button class="btn btn--primary btn--sm" type="submit">保存</button><button v-if="hasKey" class="btn btn--sm" type="button" @click="clearKey">清除</button></div>
      </form>
    </div>
  </aside>
  <div class="scrim" @click="drawer = false"></div>

  <main class="main">
    <header class="topbar">
      <button class="btn btn--ghost btn--icon menu-btn" @click="drawer = true" aria-label="打开导航"><app-icon name="menu" /></button>
      <button class="btn btn--ghost btn--icon collapse-btn" @click="toggleRail" aria-label="折叠侧栏"><app-icon name="sidebar" /></button>
      <div v-if="view"><h1 class="topbar__title">{{ view.label }}</h1></div>
      <div v-if="view" class="topbar__desc">{{ view.desc }}</div>
      <div class="topbar__actions">
        <button class="btn btn--ghost btn--icon" @click="toggleTheme" :aria-label="theme === 'dark' ? '切换浅色' : '切换深色'"><app-icon :name="theme === 'dark' ? 'sun' : 'moon'" /></button>
      </div>
    </header>
    <section class="view">
      <div v-if="loading" class="view-pad"><state-card loading /></div>
      <div v-else-if="identityFailure === 'auth'" class="view-pad">
        <state-card error icon="key" title="需要有效的 API Key" text="服务启用了鉴权（AGR_REQUIRE_AUTH）。在左下角「连接设置」中填写密钥后重试。">
          <button class="btn btn--primary" @click="settingsOpen = true; drawer = true">填写密钥</button>
        </state-card>
      </div>
      <div v-else-if="identityFailure === 'network'" class="view-pad">
        <state-card error icon="alert" title="无法连接工作区" text="/v1/me 请求失败：请确认 agr-api 已启动且当前页面与 API 同源。">
          <button class="btn btn--primary" @click="loadIdentity"><app-icon name="refresh" size="sm" />重试</button>
        </state-card>
      </div>
      <div v-else-if="viewError" class="view-pad"><state-card error icon="alert" title="视图加载失败" :text="viewError" /></div>
      <keep-alive><component v-if="ready && component" :is="component" :key="component.name" /></keep-alive>
    </section>
  </main>
  <toast-host />
</div>`,
};
