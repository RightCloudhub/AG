/* Console shell (P5-UI-02 U-03/U-05; P5-UI-03 interaction pass): identity
 * state via /v1/me (never forbidden-error probing), role-filtered navigation,
 * health dot, hash-routed views, light/dark theming, ⌘K command palette and
 * the `/` composer shortcut. Theme/palette helpers live in theme.js /
 * palette-items.js; icons in icons.js.
 */
import { fetchHealth, fetchMe, getApiKey, setApiKey } from "./api.js";
import { currentViewHash, navigateToView } from "./router.js";
import { DEFAULT_VIEW_ID, VIEWS, roleAllows, viewById } from "./views/registry.js";
import { pushToast } from "./components/toast.js";
import { applyTheme, currentTheme } from "./theme.js";
import { buildPaletteItems } from "./palette-items.js";
import { BRAND_ICON, MOON_ICON, SEARCH_ICON, SUN_ICON } from "./icons.js";

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
      theme: currentTheme(),
      paletteOpen: false,
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
    paletteItems() {
      return buildPaletteItems(this.navViews, this.theme, Boolean(this.apiKey));
    },
  },
  mounted() {
    window.addEventListener("hashchange", this.onHashChange);
    window.addEventListener("keydown", this.onGlobalKey);
    this.onHashChange();
    this.checkHealth();
    this.refreshIdentity();
  },
  beforeUnmount() {
    window.removeEventListener("hashchange", this.onHashChange);
    window.removeEventListener("keydown", this.onGlobalKey);
  },
  methods: {
    async checkHealth() {
      try {
        const h = await fetchHealth();
        const status = (h && h.status) || "ok";
        this.health = status === "ok" ? { state: "ok", label: "服务正常" } : { state: "warn", label: `服务${status}` };
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
      if (this.apiKey) pushToast("API Key 已保存", "good");
    },
    clearApiKey() {
      this.apiKey = "";
      setApiKey("");
      this.refreshIdentity();
      pushToast("已清除 API Key（回到匿名身份）", "info");
    },
    toggleTheme() {
      this.theme = applyTheme(this.theme === "dark" ? "light" : "dark");
    },
    onGlobalKey(event) {
      const key = (event.key || "").toLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === "k") {
        event.preventDefault();
        this.paletteOpen = !this.paletteOpen;
        return;
      }
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const t = event.target;
      const tag = t && t.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (t && t.isContentEditable)) return;
      if (this.currentId !== "chat") return;
      const box = document.getElementById("q");
      if (box) {
        event.preventDefault();
        box.focus();
      }
    },
    onPaletteRun(item) {
      if (item.kind === "nav") navigateToView(item.view);
      else if (item.id === "act:theme") this.toggleTheme();
      else if (item.id === "act:clearkey") this.clearApiKey();
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
          <div class="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${BRAND_ICON}"></path></svg>
          </div>
          <div class="brand-text">
            <div class="brand-name">AgenticGraphRAG</div>
            <div class="brand-tag">多跳图谱问答 · 工作台</div>
          </div>
          <button type="button" class="icon-btn" :aria-label="theme === 'dark' ? '切换亮色主题' : '切换暗色主题'" :title="theme === 'dark' ? '切换亮色主题' : '切换暗色主题'" @click="toggleTheme">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="theme === 'dark' ? '${SUN_ICON}' : '${MOON_ICON}'"></path></svg>
          </button>
        </div>
        <p class="rail-health" v-cloak>
          <span class="health-dot" :class="health.state" aria-hidden="true"></span>
          <span>{{ health.label }}</span>
        </p>
        <button type="button" class="palette-trigger" @click="paletteOpen = true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${SEARCH_ICON}"></path></svg>
          <span>搜索 / 跳转</span>
          <kbd class="kbd">⌘K</kbd>
        </button>
        <nav class="rail-nav" aria-label="视图导航">
          <a v-for="v in navViews" :key="v.id" class="nav-link" :class="{ active: v.id === currentId }" :href="'#/' + v.id" :aria-current="v.id === currentId ? 'page' : null">
            <nav-icon :d="v.icon"></nav-icon><span>{{ v.title }}</span>
          </a>
        </nav>
        <div class="rail-identity">
          <div class="card-label">身份</div>
          <p class="identity-line" v-cloak>
            <span class="identity-role" :data-role="identity.role">{{ roleLabel }}</span>
            <span class="identity-meta">{{ identityMeta }}</span>
          </p>
          <label class="field">
            <span>API Key</span>
            <input type="password" id="apiKey" autocomplete="off" placeholder="Bearer token（可选）" v-model="apiKey" @change="saveApiKey" />
          </label>
          <button v-if="apiKey" type="button" class="mini-btn" @click="clearApiKey">清除 Key（登出）</button>
        </div>
      </aside>
      <div class="stage">
        <header class="topbar">
          <h1>{{ currentView.title }}</h1>
          <p class="topbar-sub">{{ currentView.subtitle }}</p>
        </header>
        <transition name="view-fade" mode="out-in">
          <component v-if="currentAllowed" :key="currentId" :is="currentView.component"></component>
          <state-card v-else :key="'forbidden'" class="console-view" kind="forbidden" title="无权限访问该视图" detail="当前身份的角色不足以查看此视图；如需访问，请联系管理员调整 API Key 的角色。"></state-card>
        </transition>
      </div>
      <command-palette :open="paletteOpen" :items="paletteItems" @close="paletteOpen = false" @run="onPaletteRun"></command-palette>
      <toast-stack></toast-stack>
    </div>
  `,
};
