/* Metrics dashboard view (P5-UI-02). Renders /v1/metrics summary + budget
 * snapshot as stat cards and CSS bar charts. Pure presentational.
 */
import { fetchBudgetSnapshot, fetchMetrics, friendlyError } from "../api.js";

export const DashboardView = {
  name: "DashboardView",
  data() {
    return {
      loading: false,
      error: "",
      metrics: {},
      budget: {},
    };
  },
  computed: {
    routeBars() {
      return barModel(this.metrics.route_counts);
    },
    errorBars() {
      return barModel(this.metrics.error_counts);
    },
    tenantRows() {
      const tenants = (this.budget && this.budget.tenants) || {};
      return Object.entries(tenants).map(([name, usage]) => ({
        name,
        llm_calls: usage.llm_calls || 0,
        tokens: usage.tokens || 0,
      }));
    },
  },
  methods: {
    barStyle(bar) {
      return { width: `${bar.pct}%` };
    },
    async refresh() {
      this.loading = true;
      this.error = "";
      try {
        const [m, b] = await Promise.all([fetchMetrics(), fetchBudgetSnapshot()]);
        this.metrics = m;
        this.budget = b;
      } catch (err) {
        this.error = friendlyError(err);
      } finally {
        this.loading = false;
      }
    },
  },
  activated() {
    this.refresh();
  },
  template: `
    <div class="view-page">
      <header class="view-head">
        <h1>指标仪表盘</h1>
        <p class="topbar-sub">查询量 · 延迟分位 · 路由分布 · 预算快照</p>
      </header>

      <div class="toolbar">
        <button type="button" class="chip" @click="refresh" :disabled="loading">刷新</button>
        <span class="muted">进程内指标（重启清零）</span>
      </div>

      <p v-if="error" class="view-error">{{ error }}</p>
      <p v-if="loading" class="muted">加载中…</p>

      <template v-else>
        <section class="stat-grid">
          <div class="stat-card">
            <div class="stat-value">{{ metrics.count || 0 }}</div>
            <div class="stat-label">查询总数</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ (metrics.latency_p50_ms || 0).toFixed(0) }}ms</div>
            <div class="stat-label">P50 延迟</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ (metrics.latency_p95_ms || 0).toFixed(0) }}ms</div>
            <div class="stat-label">P95 延迟</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ (metrics.hops_avg || 0).toFixed(2) }}</div>
            <div class="stat-label">平均跳数</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">{{ metrics.budget_trips || 0 }}</div>
            <div class="stat-label">预算触发</div>
          </div>
        </section>

        <section class="card">
          <h2 class="card-title">路由分布</h2>
          <div v-if="!routeBars.length" class="muted">暂无数据</div>
          <div v-for="bar in routeBars" :key="bar.label" class="bar-row">
            <span class="bar-label">{{ bar.label }}</span>
            <span class="bar-track"><span class="bar-fill" :style="barStyle(bar)"></span></span>
            <span class="bar-value">{{ bar.value }}</span>
          </div>
        </section>

        <section class="card">
          <h2 class="card-title">错误分布</h2>
          <div v-if="!errorBars.length" class="muted">暂无错误</div>
          <div v-for="bar in errorBars" :key="bar.label" class="bar-row">
            <span class="bar-label">{{ bar.label }}</span>
            <span class="bar-track"><span class="bar-fill err" :style="barStyle(bar)"></span></span>
            <span class="bar-value">{{ bar.value }}</span>
          </div>
        </section>

        <section class="card">
          <h2 class="card-title">租户用量</h2>
          <table v-if="tenantRows.length" class="data-table">
            <thead><tr><th>租户</th><th>LLM 调用</th><th>Tokens</th></tr></thead>
            <tbody>
              <tr v-for="t in tenantRows" :key="t.name">
                <td>{{ t.name }}</td>
                <td>{{ t.llm_calls }}</td>
                <td>{{ t.tokens }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">暂无预算快照</p>
        </section>
      </template>
    </div>
  `,
};

function barModel(counts) {
  const map = counts || {};
  const entries = Object.entries(map);
  const max = Math.max(1, ...entries.map(([, v]) => Number(v) || 0));
  return entries.map(([label, value]) => ({
    label,
    value: Number(value) || 0,
    pct: Math.max(2, Math.round((Number(value) / max) * 100)),
  }));
}
