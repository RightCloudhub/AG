/* App shell layout (P5-UI-02): sidebar nav + health dot + routed view area.
 * Owns hash routing state and the health check. Advanced query settings live
 * in the chat view (single source of truth). Options API pure object.
 */
import { fetchHealth } from "../api.js";
import { NAV_ITEMS, isActive, navIcon, navLabel } from "../nav.js";
import { DEFAULT_VIEW, navigate, parseHash } from "../router.js";

const VIEW_COMPONENTS = Object.freeze({
  chat: "chat-view",
  graph: "graph-view",
  dashboard: "dashboard-view",
  review: "review-view",
  history: "history-view",
});

export const AppShell = {
  name: "AppShell",
  data() {
    return {
      view: parseHash(window.location.hash),
      health: { state: "checking", label: "检测服务中…" },
    };
  },
  computed: {
    viewComponent() {
      return VIEW_COMPONENTS[this.view] || "chat-view";
    },
    navItems() {
      return NAV_ITEMS;
    },
  },
  methods: {
    navLabel(name) {
      return navLabel(name);
    },
    navIcon(name) {
      return navIcon(name);
    },
    isActive(name) {
      return isActive(name, this.view);
    },
    goto(name) {
      navigate(name);
    },
    onHashChange() {
      this.view = parseHash(window.location.hash);
    },
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
  },
  mounted() {
    this.checkHealth();
    window.addEventListener("hashchange", this.onHashChange);
    if (!window.location.hash) navigate(DEFAULT_VIEW);
  },
  beforeUnmount() {
    window.removeEventListener("hashchange", this.onHashChange);
  },
  template: `
    <div class="shell">
      <aside class="rail" aria-label="Sidebar">
        <div class="brand">
          <div class="brand-mark" aria-hidden="true"></div>
          <div>
            <div class="brand-name">AgenticGraphRAG</div>
            <div class="brand-tag">多跳图谱问答 · 试用</div>
          </div>
        </div>

        <nav class="rail-nav" aria-label="视图导航">
          <button
            v-for="item in navItems"
            :key="item.name"
            type="button"
            class="nav-item"
            :class="{ active: isActive(item.name) }"
            @click="goto(item.name)"
          >
            <span class="nav-icon" aria-hidden="true">{{ navIcon(item.name) }}</span>
            <span>{{ navLabel(item.name) }}</span>
          </button>
        </nav>

        <p class="rail-health" v-cloak>
          <span class="health-dot" :class="health.state" aria-hidden="true"></span>
          <span>{{ health.label }}</span>
        </p>
      </aside>

      <div class="stage">
        <keep-alive>
          <component :is="viewComponent"></component>
        </keep-alive>
      </div>
    </div>
  `,
};
