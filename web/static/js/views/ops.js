/* Observability view (P5-UI-02 U-09/U-10, admin): metric stat cards, budget
 * snapshot, audit-event browsing (paged, default 24h window) and per-query
 * audit lookup that reuses the chat chain components for the full reasoning
 * chain.
 */
import { fetchAuditEvents, fetchAuditQuery, fetchBudgetSnapshot, fetchMetrics } from "../api-console.js";
import { buildClaimItems } from "../chain-view.js";
import { errorState, formatTimestamp, truncateText } from "../components/console-widgets.js";

const WINDOW_OPTIONS = [
  { value: "24h", label: "近 24 小时" },
  { value: "7d", label: "近 7 天" },
  { value: "all", label: "全部" },
];
const PAGE_SIZE = 50;
const HOUR_SECONDS = 3600;

export const OpsView = {
  name: "OpsView",
  data() {
    return {
      metrics: null,
      budget: null,
      loadingMetrics: true,
      events: [],
      eventTotal: 0,
      offset: 0,
      loadingEvents: false,
      filters: { window: "24h", tenant_id: "", action: "" },
      windowOptions: WINDOW_OPTIONS,
      lookupId: "",
      chain: null,
      chainError: "",
      loadingChain: false,
      error: null,
      eventColumns: [
        { key: "ts", label: "时间", render: (row) => formatTimestamp(row.ts) },
        { key: "action", label: "动作" },
        { key: "tenant_id", label: "租户" },
        { key: "user_id", label: "用户", render: (row) => truncateText(row.user_id, 20) },
        { key: "target", label: "对象", render: (row) => truncateText(row.target, 28) },
        { key: "outcome", label: "结果", badge: true },
      ],
    };
  },
  computed: {
    metricCards() {
      const m = this.metrics || {};
      return [
        { label: "查询总数", value: m.count ?? 0 },
        { label: "P50 延迟 (ms)", value: m.latency_p50_ms ?? "-" },
        { label: "P95 延迟 (ms)", value: m.latency_p95_ms ?? "-" },
        { label: "平均跳数", value: m.hops_avg ?? "-" },
        { label: "预算触顶", value: m.budget_trips ?? 0 },
      ];
    },
    routeLine() {
      const m = this.metrics || {};
      const fmt = (map) =>
        Object.entries(map || {})
          .map(([k, v]) => `${k}: ${v}`)
          .join(" · ") || "-";
      return `路由 ${fmt(m.route_counts)} · 错误 ${fmt(m.error_counts)}`;
    },
    tenantRows() {
      const tenants = (this.budget && this.budget.tenants) || {};
      return Object.entries(tenants).map(([tenant, usage]) => ({
        tenant,
        llm_calls: usage.llm_calls ?? 0,
        tokens: usage.tokens ?? 0,
        cost_units: usage.cost_units ?? 0,
      }));
    },
    claimItems() {
      if (!this.chain) return [];
      const catalog = this.chain.evidence || (this.chain.metadata || {}).evidence || [];
      return buildClaimItems(this.chain.claims || [], catalog);
    },
    page() {
      return Math.floor(this.offset / PAGE_SIZE) + 1;
    },
    pageCount() {
      return Math.max(1, Math.ceil(this.eventTotal / PAGE_SIZE));
    },
  },
  mounted() {
    this.loadAll();
  },
  methods: {
    onFilter({ key, value }) {
      this.filters[key] = value;
    },
    applyFilters() {
      this.offset = 0;
      this.loadEvents();
    },
    turnPage(delta) {
      const next = (this.page + delta - 1) * PAGE_SIZE;
      if (next < 0 || (delta > 0 && this.page >= this.pageCount)) return;
      this.offset = next;
      this.loadEvents();
    },
    async loadAll() {
      this.error = null;
      this.loadingMetrics = true;
      try {
        const [metrics, budget] = await Promise.all([fetchMetrics(), fetchBudgetSnapshot()]);
        this.metrics = metrics.data;
        this.budget = budget.data;
      } catch (err) {
        this.error = errorState(err);
      } finally {
        this.loadingMetrics = false;
      }
      await this.loadEvents();
    },
    async loadEvents() {
      const sinceDays = { "24h": 1, "7d": 7 }[this.filters.window];
      const since =
        sinceDays !== undefined ? Date.now() / 1000 - sinceDays * 24 * HOUR_SECONDS : undefined;
      this.loadingEvents = true;
      try {
        const env = await fetchAuditEvents({
          since,
          until: undefined,
          tenant_id: this.filters.tenant_id,
          action: this.filters.action,
          limit: PAGE_SIZE,
          offset: this.offset,
        });
        this.events = env.data || [];
        this.eventTotal = (env.meta && env.meta.total) || this.events.length;
      } catch (err) {
        this.events = [];
        this.eventTotal = 0;
        this.chainError = errorState(err).detail;
      } finally {
        this.loadingEvents = false;
      }
    },
    async lookup() {
      const qid = this.lookupId.trim();
      if (!qid || this.loadingChain) return;
      this.loadingChain = true;
      this.chain = null;
      this.chainError = "";
      try {
        const env = await fetchAuditQuery(qid);
        this.chain = env.data;
      } catch (err) {
        this.chainError =
          err && err.status === 404
            ? "未找到该 query_id 的推理链（不存在或不属于当前租户）"
            : (err && err.message) || "查询失败";
      } finally {
        this.loadingChain = false;
      }
    },
  },
  template: `
    <section class="view console-view">
      <state-card v-if="error" :kind="error.kind" :title="error.title" :detail="error.detail"></state-card>
      <template v-else>
        <div class="panel">
          <div class="panel-head"><h2>指标</h2></div>
          <div v-if="loadingMetrics" class="stat-grid" aria-hidden="true">
            <div v-for="i in 5" :key="'msk' + i" class="stat-card">
              <span class="skeleton" style="width: 45%"></span>
              <span class="skeleton" style="width: 72%; margin-top: 0.45rem"></span>
            </div>
          </div>
          <template v-else>
            <div class="stat-grid">
              <stat-card v-for="c in metricCards" :key="c.label" :label="c.label" :value="c.value"></stat-card>
            </div>
            <p class="muted">{{ routeLine }}</p>
          </template>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>预算快照（当前窗口）</h2></div>
          <data-table
            :columns="[
              { key: 'tenant', label: '租户' },
              { key: 'llm_calls', label: 'LLM 调用' },
              { key: 'tokens', label: 'Tokens' },
              { key: 'cost_units', label: '成本单位' },
            ]"
            :rows="tenantRows"
            empty-text="当前窗口暂无用量"
          ></data-table>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>安全事件</h2></div>
          <filter-bar
            :filters="[
              { key: 'window', label: '时间窗', type: 'select', options: windowOptions },
              { key: 'tenant_id', label: '租户', type: 'text', placeholder: '留空为全部' },
              { key: 'action', label: '动作', type: 'text', placeholder: '如 doc_upload' },
            ]"
            :values="filters"
            @update="onFilter"
            @apply="applyFilters"
          ></filter-bar>
          <data-table :columns="eventColumns" :rows="events" empty-text="窗口内暂无事件" :loading="loadingEvents"></data-table>
          <div class="pager">
            <span class="muted">第 {{ page }} / {{ pageCount }} 页 · 共 {{ eventTotal }} 条</span>
            <span class="pager-btns">
              <button type="button" class="mini-btn" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
              <button type="button" class="mini-btn" :disabled="page >= pageCount" @click="turnPage(1)">下一页</button>
            </span>
          </div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>审计回查</h2></div>
          <div class="lookup-row">
            <input type="text" v-model="lookupId" placeholder="输入 query_id…" @keydown.enter.prevent="lookup" />
            <button type="button" class="send-btn" :disabled="loadingChain || !lookupId.trim()" @click="lookup">
              <span>{{ loadingChain ? "查询中…" : "回查" }}</span>
            </button>
          </div>
          <p v-if="chainError" class="feedback-note bad">{{ chainError }}</p>
          <div v-if="chain" class="chain-result">
            <div class="bubble-meta">{{ chain.route }} · {{ chain.status }} · {{ chain.query_id }}</div>
            <div class="chain-question">{{ chain.question }}</div>
            <div class="answer-text">{{ chain.answer }}</div>
            <div v-if="claimItems.length" class="claims-panel">
              <div class="card-label">论断与证据</div>
              <ol class="claims-list">
                <li v-for="c in claimItems" :key="c.n">
                  <div class="claim-text">{{ c.text }}</div>
                  <div class="claim-ev">{{ c.evidence }}</div>
                </li>
              </ol>
            </div>
            <details class="fold" open><summary>子问题分解树</summary><plan-tree :steps="chain.steps || []"></plan-tree></details>
            <details class="fold" open><summary>图路径</summary><path-list :paths="chain.explored_paths || []"></path-list></details>
            <details class="fold"><summary>步骤与证据</summary><steps-list :steps="chain.steps || []"></steps-list></details>
            <details class="fold"><summary>推理链 JSON</summary><pre class="mono">{{ JSON.stringify(chain, null, 2) }}</pre></details>
          </div>
        </div>
      </template>
    </section>
  `,
};
