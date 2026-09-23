import { fetchHealth, fetchMe, getApiKey, setApiKey } from "./api.js";
import { canOpenView, findView, visibleViews } from "./views/registry.js";

const ROLE_LABELS = Object.freeze({
  admin: "管理员",
  operator: "知识运营",
  reader: "只读成员",
});

export const rootComponent = {
  name: "AgrWorkspace",
  template: `
    <div class="workspace-shell">
      <aside id="workspaceNavigation" class="sidebar" :class="{ 'sidebar-open': mobileNavOpen }" aria-label="主导航">
        <a class="brand" href="#/chat" aria-label="AgenticGraphRAG 首页">
          <span class="brand-mark" aria-hidden="true">AG</span>
          <span class="brand-copy"><strong>AgenticGraphRAG</strong><small>知识推理工作台</small></span>
        </a>
        <div class="sidebar-section-label">工作区</div>
        <nav class="primary-nav" aria-label="工作区视图">
          <a v-for="view in navigation" :key="view.id" :href="'#/' + view.id"
             class="nav-link" :class="{ active: activeView === view.id }"
             :aria-current="activeView === view.id ? 'page' : null" @click="mobileNavOpen = false">
            <span class="nav-icon" aria-hidden="true">{{ view.icon }}</span>
            <span>{{ view.title }}</span>
            <span v-if="view.id === 'review' && pendingReviews" class="nav-count">{{ pendingReviews }}</span>
          </a>
        </nav>
        <div class="sidebar-spacer"></div>
        <div class="service-card">
          <span class="health-indicator" :class="health.state" aria-hidden="true"></span>
          <span><strong>服务连接</strong><small>{{ health.label }}</small></span>
          <button class="icon-button" type="button" aria-label="重新检查服务" @click="checkHealth">↻</button>
        </div>
        <div v-if="identity" class="identity-card">
          <span class="identity-avatar" aria-hidden="true">{{ identity.role.slice(0, 1).toUpperCase() }}</span>
          <span class="identity-copy"><strong>{{ ROLE_LABELS[identity.role] || identity.role }}</strong>
            <small>{{ identity.tenant_id }} · {{ identity.user_id }}</small></span>
          <button class="icon-button" type="button" aria-label="连接设置" @click="openSettings">⚙</button>
        </div>
        <button v-else class="button button-secondary sidebar-login" type="button" @click="openSettings">配置连接</button>
        <p class="sidebar-footnote">安全连接 · 租户数据隔离</p>
      </aside>

      <button v-if="mobileNavOpen" class="mobile-scrim" aria-label="关闭导航" @click="mobileNavOpen = false"></button>
      <section class="workspace-main">
        <header class="workspace-topbar">
          <button class="mobile-menu icon-button" type="button" aria-label="切换导航" aria-controls="workspaceNavigation" :aria-expanded="mobileNavOpen" @click="mobileNavOpen = !mobileNavOpen">☰</button>
          <div class="page-heading">
            <span class="eyebrow">AGENTIC GRAPHRAG</span>
            <h1>{{ page.title }}</h1>
          </div>
          <div class="topbar-actions">
            <span class="topbar-health" :class="health.state"><i aria-hidden="true"></i>{{ health.label }}</span>
            <button class="button button-secondary settings-button" type="button" @click="openSettings">连接设置</button>
          </div>
        </header>

        <div v-if="identityState === 'loading'" class="workspace-state" role="status">正在验证工作区身份…</div>
        <section v-else-if="!identity" class="auth-gate" aria-labelledby="authTitle">
          <div class="state-mark" aria-hidden="true">{{ identityFailure === 'auth' ? '钥' : '!' }}</div>
          <p class="eyebrow">SECURE WORKSPACE</p>
          <h2 id="authTitle">{{ identityFailure === 'auth' ? '需要有效的 API Key' : '无法连接工作区' }}</h2>
          <p>{{ identityError || (identityFailure === 'auth' ? '此工作区已启用身份验证。输入服务管理员提供的 API Key 以继续。' : '请检查 API 服务和网络连接后重试。') }}</p>
          <template v-if="identityFailure === 'auth'">
            <label class="field-label" for="gateApiKey">API Key</label>
            <input id="gateApiKey" v-model="keyDraft" type="password" autocomplete="current-password"
                   placeholder="粘贴 API Key" @keydown.enter.prevent="saveAndConnect" />
          </template>
          <div class="auth-actions">
            <button v-if="identityFailure === 'auth'" class="button button-primary" type="button" :disabled="!keyDraft.trim() || identityState === 'loading'" @click="saveAndConnect">验证并连接</button>
            <button v-else class="button button-primary" type="button" @click="loadIdentity">重新连接</button>
            <button v-if="identityFailure === 'auth'" class="button button-secondary" type="button" @click="loadIdentity">重试</button>
            <button v-else class="button button-secondary" type="button" @click="openSettings">连接设置</button>
          </div>
          <p v-if="identityFailure === 'auth'" class="field-help">Key 仅保存在此浏览器，可随时在连接设置中清除。</p>
        </section>
        <template v-else>
          <div v-if="accessMessage" class="inline-alert alert-warning" role="status">
            <span>{{ accessMessage }}</span><button class="icon-button" aria-label="关闭提示" @click="accessMessage = ''">×</button>
          </div>
          <keep-alive>
            <component :is="page.component" :identity="identity" @notify="notify" @pending-count="pendingReviews = $event"></component>
          </keep-alive>
        </template>
      </section>

      <div v-if="settingsOpen" class="modal-backdrop" @click.self="settingsOpen = false">
        <section class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
          <div class="dialog-heading"><div><p class="eyebrow">CONNECTION</p><h2 id="settingsTitle">连接设置</h2></div>
            <button class="icon-button" type="button" aria-label="关闭" @click="settingsOpen = false">×</button></div>
          <p class="dialog-copy">API Key 用于识别租户与访问角色。请勿在共享设备上保存密钥。</p>
          <label class="field-label" for="settingsApiKey">API Key</label>
          <input id="settingsApiKey" v-model="keyDraft" type="password" autocomplete="off" placeholder="未配置" />
          <div class="connection-summary" v-if="identity">
            <span>当前身份</span><strong>{{ identity.tenant_id }} · {{ ROLE_LABELS[identity.role] || identity.role }}</strong>
          </div>
          <div class="dialog-actions">
            <button class="button button-primary" type="button" :disabled="identityState === 'loading'" @click="saveAndConnect">保存并验证</button>
            <button class="button button-danger-quiet" type="button" @click="clearKey">清除密钥</button>
          </div>
          <p class="field-help">使用 localStorage 保存；清除后将以匿名身份重连（若服务允许）。</p>
        </section>
      </div>
      <div v-if="toast" class="toast" :class="'toast-' + toast.kind" role="status" aria-live="polite">{{ toast.message }}</div>
    </div>
  `,
  data() {
    return {
      activeView: "chat",
      identity: null,
      identityState: "loading",
      identityError: "",
      identityFailure: "",
      health: { state: "checking", label: "检查中" },
      settingsOpen: false,
      keyDraft: getApiKey(),
      mobileNavOpen: false,
      accessMessage: "",
      pendingReviews: 0,
      toast: null,
      _toastTimer: null,
    };
  },
  computed: {
    navigation() {
      return visibleViews(this.identity);
    },
    page() {
      return findView(this.activeView) || findView("chat");
    },
  },
  mounted() {
    window.addEventListener("hashchange", this.syncRoute);
    this.loadIdentity();
    this.checkHealth();
  },
  beforeUnmount() {
    window.removeEventListener("hashchange", this.syncRoute);
    window.clearTimeout(this._toastTimer);
  },
  methods: {
    async loadIdentity() {
      this.identityState = "loading";
      this.identityError = "";
      this.identityFailure = "";
      try {
        this.identity = await fetchMe();
        this.identityState = "ready";
        this.keyDraft = getApiKey();
        this.syncRoute();
      } catch (err) {
        this.identity = null;
        this.identityState = "error";
        this.identityFailure = err && err.status === 401 ? "auth" : "connection";
        this.identityError = this.identityFailure === "auth"
          ? "请使用有效密钥登录。"
          : (err && err.message) || "暂时无法连接身份服务。";
      }
    },
    async saveAndConnect() {
      setApiKey(this.keyDraft.trim());
      await this.loadIdentity();
      if (this.identity) {
        this.settingsOpen = false;
        this.notify("连接已验证", "success");
      }
    },
    async clearKey() {
      setApiKey("");
      this.keyDraft = "";
      await this.loadIdentity();
      if (this.identity) this.settingsOpen = false;
    },
    openSettings() {
      this.keyDraft = getApiKey();
      this.settingsOpen = true;
    },
    syncRoute() {
      if (!this.identity) return;
      const requestedId = (window.location.hash || "#/chat").replace(/^#\/?/, "") || "chat";
      const requested = findView(requestedId);
      if (!requested) {
        this.activeView = "chat";
        window.history.replaceState(null, "", "#/chat");
        return;
      }
      if (!canOpenView(this.identity, requested)) {
        this.activeView = "chat";
        this.accessMessage = `“${requested.title}”仅对授权角色开放。当前账号：${ROLE_LABELS[this.identity.role] || this.identity.role}。`;
        window.history.replaceState(null, "", "#/chat");
        return;
      }
      this.activeView = requested.id;
      this.mobileNavOpen = false;
    },
    async checkHealth() {
      try {
        const result = await fetchHealth();
        const state = result.status === "ok" ? "ok" : "warning";
        this.health = { state, label: state === "ok" ? "服务正常" : "部分依赖异常" };
      } catch {
        this.health = { state: "down", label: "无法连接" };
      }
    },
    notify(message, kind = "success") {
      this.toast = { message, kind };
      window.clearTimeout(this._toastTimer);
      this._toastTimer = window.setTimeout(() => { this.toast = null; }, 3800);
    },
  },
};
