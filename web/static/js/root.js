/* Console shell (P5-UI-02 U-03/U-05): identity state via /v1/me (probed at
 * boot and on key change — never by forbidden-error probing), role-filtered
 * navigation from the view registry, health dot, and hash-routed views.
 */
import { fetchHealth, fetchMe, getApiKey, setApiKey } from "./api.js";
import { currentViewHash, navigateToView } from "./router.js";
import { DEFAULT_VIEW_ID, VIEWS, roleAllows, viewById } from "./views/registry.js";

const ROLE_LABELS = Object.freeze({
  admin: "管理员",
  operator: "操作员",
  reader: "读者",
  anonymous: "未登录",
});

export const rootComponent = {
  name: "AgrConsoleShell",
  data() {
    return {
      currentId: DEFAULT_VIEW_ID,
      apiKey: getApiKey(),
      health: { state: "checking", label: "检测服务中…" },
      identity: { tenantId: "", userId: "", role: "", ready: false },
    };
  },
  computed: {
    /* Before /v1/me answers, assume the server-default reader role so nav is
     * never wider than the caller's real permissions. */
    activeRole() {
      return this.identity.ready ? this.identity.role || "reader" : "reader";
    },
    navViews() {
      return VIEWS.filter((v) => roleAllows(v.minRole, this.activeRole));
    },
    currentView() {
      return viewById(this.currentId) || viewById(DEFAULT_VIEW_ID);
    },
    currentAllowed() {
      return roleAllows(this.currentView.minRole, this.activeRole);
    },
    roleLabel() {
      return ROLE_LABELS[this.identity.role] || this.identity.role || "未知";
    },
    identityMeta() {
      const user = this.identity.userId || "anonymous";
      const shown = user.length > 18 ? `${user.slice(0, 18)}…` : user;
      return `${this.identity.tenantId || "default"} / ${shown}`;
    },
  },
  mounted() {
    window.addEventListener("hashchange", this.onHashChange);
    this.onHashChange();
    this.checkHealth();
    this.refreshIdentity();
  },
  beforeUnmount() {
    window.removeEventListener("hashchange", this.onHashChange);
  },
  methods: {
    async checkHealth() {
      try {
        const h = await fetchHealth();
        const status = (h && h.status) || "ok";
        if (status === "ok") this.health = { state: "ok", label: "服务正常" };
        else this.health = { state: "warn", label: `服务${status}` };
      } catch {
        this.health = { state: "down", label: "服务不可用" };
      }
    },
    async refreshIdentity() {
      try {
        const me = await fetchMe();
        this.identity = {
          tenantId: me.tenant_id || "default",
          userId: me.user_id || "anonymous",
          role: me.role || "reader",
          ready: true,
        };
      } catch {
        this.identity = { tenantId: "default", userId: "anonymous", role: "reader", ready: true };
      }
    },
    saveApiKey() {
      setApiKey((this.apiKey || "").trim());
      this.refreshIdentity();
    },
    clearApiKey() {
      this.apiKey = "";
      setApiKey("");
      this.refreshIdentity();
    },
    onHashChange() {
      const id = currentViewHash();
      const view = id ? viewById(id) : null;
      if (view) {
        this.currentId = view.id;
        navigateToView(view.id);
      } else {
        this.currentId = DEFAULT_VIEW_ID;
      }
    },
  },
  template: `
    <div class="shell">
      <aside class="rail" aria-label="Sidebar">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true"></div>
          <div>
            <div class="brand-name">AgenticGraphRAG</div>
            <div class="brand-tag">多跳图谱问答 · 工作台</div>
          </div>
        </div>
        <p class="rail-health" v-cloak>
          <span class="health-dot" :class="health.state" aria-hidden="true"></span>
          <span>{{ health.label }}</span>
        </p>
        <nav class="rail-nav" aria-label="视图导航">
          <a v-for="v in navViews" :key="v.id" class="nav-link"
             :class="{ active: v.id === currentId }" :href="'#/' + v.id">{{ v.title }}</a>
        </nav>
        <div class="rail-identity">
          <div class="card-label">身份</div>
          <p class="identity-line" v-cloak>
            <span class="identity-role" :data-role="identity.role">{{ roleLabel }}</span>
            <span class="identity-meta">{{ identityMeta }}</span>
          </p>
          <label class="field">
            <span>API Key</span>
            <input type="password" id="apiKey" autocomplete="off" placeholder="Bearer token（可选）"
              v-model="apiKey" @change="saveApiKey" />
          </label>
          <button v-if="apiKey" type="button" class="mini-btn" @click="clearApiKey">清除 Key（登出）</button>
        </div>
      </aside>
      <div class="stage">
        <header class="topbar">
          <h1>{{ currentView.title }}</h1>
          <p class="topbar-sub">{{ currentView.subtitle }}</p>
        </header>
        <component v-if="currentAllowed" :is="currentView.component"></component>
        <state-card v-else class="console-view" kind="forbidden" title="无权限访问该视图"
          detail="当前身份的角色不足以查看此视图；如需访问，请联系管理员调整 API Key 的角色。"></state-card>
      </div>
    </div>
  `,
};
