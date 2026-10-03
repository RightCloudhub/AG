/**
 * Ops view — admin dashboard: metrics, budget, audit events.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchMetrics, fetchBudgetSnapshot, fetchAuditEvents, fetchHealth } from '../api.js';

export default {
  name: 'OpsView',
  setup() {
    const activeTab = ref('metrics');
    const metrics = ref(null);
    const budget = ref(null);
    const auditEvents = ref([]);
    const health = ref(null);
    const loading = ref(false);

    async function loadMetrics() {
      loading.value = true;
      try {
        const [m, h] = await Promise.all([
          fetchMetrics().catch(() => ({ data: null })),
          fetchHealth().catch(() => null),
        ]);
        metrics.value = m.data;
        health.value = h;
      } finally { loading.value = false; }
    }

    async function loadBudget() {
      loading.value = true;
      try {
        const { data } = await fetchBudgetSnapshot();
        budget.value = data;
      } catch { budget.value = null; }
      finally { loading.value = false; }
    }

    async function loadAudit() {
      loading.value = true;
      try {
        const { data } = await fetchAuditEvents({ limit: 100 });
        auditEvents.value = data || [];
      } catch { auditEvents.value = []; }
      finally { loading.value = false; }
    }

    function switchTab(tab) {
      activeTab.value = tab;
      if (tab === 'metrics') loadMetrics();
      else if (tab === 'budget') loadBudget();
      else if (tab === 'audit') loadAudit();
    }

    function fmtTime(ts) {
      if (!ts) return '—';
      try { return new Date(ts * 1000).toLocaleString(); } catch { return String(ts); }
    }

    onMounted(loadMetrics);

    return { activeTab, metrics, budget, auditEvents, health, loading,
             switchTab, loadMetrics, loadBudget, loadAudit, fmtTime };
  },

  template: `
<div>
  <div class="view-header">
    <div class="view-header__title">📡 系统运维</div>
    <div class="view-header__actions">
      <span v-if="health" class="badge" :class="health.status === 'ok' ? 'badge--success' : 'badge--warning'">
        {{ health.status }}
      </span>
    </div>
  </div>
  <div class="view-body">
    <div class="tab-bar" style="max-width:400px;">
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === 'metrics' }" @click="switchTab('metrics')">监控指标</button>
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === 'budget' }" @click="switchTab('budget')">配额分析</button>
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === 'audit' }" @click="switchTab('audit')">安全审计</button>
    </div>

    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>

    <!-- Metrics -->
    <template v-else-if="activeTab === 'metrics'">
      <div v-if="!metrics" class="empty-state">
        <div class="empty-state__icon">📊</div>
        <div class="empty-state__text">暂无指标数据</div>
      </div>
      <template v-else>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="stat-card__label">总查询数</div>
            <div class="stat-card__value">{{ metrics.count ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="stat-card__label">延迟 P50</div>
            <div class="stat-card__value">{{ (metrics.latency_p50_ms ?? 0).toFixed(0) }}<span style="font-size:var(--text-sm);color:var(--color-text-muted);"> ms</span></div>
          </div>
          <div class="stat-card">
            <div class="stat-card__label">延迟 P95</div>
            <div class="stat-card__value">{{ (metrics.latency_p95_ms ?? 0).toFixed(0) }}<span style="font-size:var(--text-sm);color:var(--color-text-muted);"> ms</span></div>
          </div>
          <div class="stat-card">
            <div class="stat-card__label">延迟 P99</div>
            <div class="stat-card__value">{{ (metrics.latency_p99_ms ?? 0).toFixed(0) }}<span style="font-size:var(--text-sm);color:var(--color-text-muted);"> ms</span></div>
          </div>
          <div class="stat-card">
            <div class="stat-card__label">配额耗尽次数</div>
            <div class="stat-card__value">{{ metrics.budget_trips ?? 0 }}</div>
          </div>
        </div>

        <div v-if="metrics.route_counts" class="ops-section" style="margin-top:var(--space-6);">
          <div class="ops-section__title">📍 路由分布</div>
          <div class="stat-grid">
            <div v-for="(v, k) in metrics.route_counts" :key="k" class="stat-card">
              <div class="stat-card__label">{{ k }}</div>
              <div class="stat-card__value">{{ v }}</div>
            </div>
          </div>
        </div>

        <div v-if="metrics.error_counts && Object.keys(metrics.error_counts).length" class="ops-section" style="margin-top:var(--space-6);">
          <div class="ops-section__title">⚠ 错误统计</div>
          <div class="stat-grid">
            <div v-for="(v, k) in metrics.error_counts" :key="k" class="stat-card">
              <div class="stat-card__label">{{ k }}</div>
              <div class="stat-card__value" style="color:var(--color-danger);">{{ v }}</div>
            </div>
          </div>
        </div>

        <!-- Health checks -->
        <div v-if="health?.checks" class="ops-section" style="margin-top:var(--space-6);">
          <div class="ops-section__title">🏥 健康检查</div>
          <div class="data-table-wrap">
            <table class="data-table">
              <thead><tr><th>检查项</th><th>状态</th></tr></thead>
              <tbody>
                <tr v-for="(v, k) in health.checks" :key="k">
                  <td>{{ k }}</td>
                  <td><span class="badge" :class="v === 'ok' || v === '1' ? 'badge--success' : 'badge--warning'">{{ v }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </template>

    <!-- Budget -->
    <template v-else-if="activeTab === 'budget'">
      <div v-if="!budget || Object.keys(budget).length === 0" class="empty-state">
        <div class="empty-state__icon">💰</div>
        <div class="empty-state__text">暂无配额数据</div>
      </div>
      <div v-else>
        <pre style="background:var(--color-bg-tertiary);padding:var(--space-5);border-radius:var(--radius-lg);border:1px solid var(--color-border);overflow-x:auto;font-size:var(--text-sm);color:var(--color-text-secondary);">{{ JSON.stringify(budget, null, 2) }}</pre>
      </div>
    </template>

    <!-- Audit Events -->
    <template v-else-if="activeTab === 'audit'">
      <div v-if="auditEvents.length === 0" class="empty-state">
        <div class="empty-state__icon">🔒</div>
        <div class="empty-state__text">暂无审计事件</div>
      </div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>操作</th>
              <th>目标</th>
              <th>结果</th>
              <th>租户</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(ev, i) in auditEvents" :key="i">
              <td style="font-size:var(--text-xs);white-space:nowrap;">{{ fmtTime(ev.ts || ev.timestamp) }}</td>
              <td><span class="badge badge--accent">{{ ev.action }}</span></td>
              <td style="font-family:var(--font-mono);font-size:var(--text-xs);max-width:160px;overflow:hidden;text-overflow:ellipsis;">
                {{ ev.target || '—' }}
              </td>
              <td><span class="badge" :class="ev.outcome === 'success' ? 'badge--success' : 'badge--muted'">{{ ev.outcome || '—' }}</span></td>
              <td style="font-size:var(--text-xs);">{{ ev.tenant_id || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</div>
`
};
