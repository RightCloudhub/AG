// @ts-check
/**
 * Ops view — admin dashboard: metrics, budget, audit events.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchMetrics, fetchBudgetSnapshot, fetchAuditEvents, fetchHealth } from '../api.js';
import { OpsTab, HealthStatus, AuditOutcome, UiPolicy } from '../contracts.js';
import { text as T } from '../i18n.js';
import { openNotebook } from '../store/shell.js';

/** @param {number} ts */
function fmtTime(ts) {
  if (!ts) return '—';
  try { return new Date(ts * UiPolicy.MILLISECONDS_PER_SECOND).toLocaleString('zh-CN'); } catch { return String(ts); }
}

export default {
  name: 'OpsView',
  setup() {
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../contracts.js').OpsTabName>} */
    const activeTab = ref(OpsTab.METRICS);
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').Metrics | null>} */
    const metrics = ref(null);
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<Record<string, unknown> | null>} */
    const budget = ref(null);
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').AuditEvent[]>} */
    const auditEvents = ref([]);
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').Health | null>} */
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
        const { data } = await fetchAuditEvents({ limit: UiPolicy.AUDIT_PAGE_SIZE });
        auditEvents.value = data || [];
      } catch { auditEvents.value = []; }
      finally { loading.value = false; }
    }

    /** @param {import('../contracts.js').OpsTabName} tab */
    function switchTab(tab) {
      activeTab.value = tab;
      if (tab === OpsTab.METRICS) loadMetrics();
      else if (tab === OpsTab.BUDGET) loadBudget();
      else if (tab === OpsTab.AUDIT) loadAudit();
    }

    onMounted(loadMetrics);

    return { activeTab, metrics, budget, auditEvents, health, loading,
             switchTab, loadMetrics, loadBudget, loadAudit, fmtTime, openNotebook,
             OpsTab, HealthStatus, AuditOutcome, UiPolicy, T };
  },

  template: `
<div class="view">
  <div class="view-header">
    <button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="T.notebook.open"><ag-icon name="menu"></ag-icon></button>
    <h1 class="view-header__title">{{ T.ops.title }}</h1>
    <div class="view-header__actions">
      <span v-if="health" class="tag" :class="health.status === HealthStatus.OK ? 'tag--ok' : 'tag--warn'">{{ health.status }}</span>
    </div>
  </div>
  <div class="view-body">
    <div class="tab-bar">
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === OpsTab.METRICS }" @click="switchTab(OpsTab.METRICS)">{{ T.ops.metrics }}</button>
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === OpsTab.BUDGET }" @click="switchTab(OpsTab.BUDGET)">{{ T.ops.budget }}</button>
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': activeTab === OpsTab.AUDIT }" @click="switchTab(OpsTab.AUDIT)">{{ T.ops.audit }}</button>
    </div>

    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>

    <template v-else-if="activeTab === OpsTab.METRICS">
      <div v-if="!metrics" class="empty-state empty-state--slot"><div class="empty-state__text">{{ T.ops.noMetrics }}</div></div>
      <template v-else>
        <div class="stat-grid">
          <div class="stat-card"><div class="stat-card__label">{{ T.ops.totalQueries }}</div><div class="stat-card__value">{{ metrics.count ?? 0 }}</div></div>
          <div class="stat-card"><div class="stat-card__label">{{ T.ops.p50 }}</div><div class="stat-card__value">{{ (metrics.latency_p50_ms ?? 0).toFixed(0) }}<small> ms</small></div></div>
          <div class="stat-card"><div class="stat-card__label">{{ T.ops.p95 }}</div><div class="stat-card__value">{{ (metrics.latency_p95_ms ?? 0).toFixed(0) }}<small> ms</small></div></div>
          <div class="stat-card"><div class="stat-card__label">{{ T.ops.p99 }}</div><div class="stat-card__value">{{ (metrics.latency_p99_ms ?? 0).toFixed(0) }}<small> ms</small></div></div>
          <div class="stat-card"><div class="stat-card__label">{{ T.ops.budgetTrips }}</div><div class="stat-card__value">{{ metrics.budget_trips ?? 0 }}</div></div>
        </div>

        <div v-if="metrics.route_counts" class="ops-section">
          <h2 class="ops-section__title">{{ T.ops.routes }}</h2>
          <div class="stat-grid">
            <div v-for="(v, k) in metrics.route_counts" :key="k" class="stat-card">
              <div class="stat-card__label">{{ k }}</div><div class="stat-card__value">{{ v }}</div>
            </div>
          </div>
        </div>

        <div v-if="metrics.error_counts && Object.keys(metrics.error_counts).length" class="ops-section">
          <h2 class="ops-section__title">{{ T.ops.errors }}</h2>
          <div class="stat-grid">
            <div v-for="(v, k) in metrics.error_counts" :key="k" class="stat-card">
              <div class="stat-card__label">{{ k }}</div><div class="stat-card__value cell-danger">{{ v }}</div>
            </div>
          </div>
        </div>

        <div v-if="health?.checks" class="ops-section">
          <h2 class="ops-section__title">{{ T.ops.health }}</h2>
          <div class="data-table-wrap">
            <table class="data-table">
              <thead><tr><th>{{ T.ops.check }}</th><th>{{ T.ops.status_ }}</th></tr></thead>
              <tbody>
                <tr v-for="(v, k) in health.checks" :key="k">
                  <td class="mono">{{ k }}</td>
                  <td><span class="tag" :class="v === HealthStatus.OK || v === HealthStatus.PASSED ? 'tag--ok' : ''">{{ v }}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </template>

    <template v-else-if="activeTab === OpsTab.BUDGET">
      <div v-if="!budget || Object.keys(budget).length === 0" class="empty-state empty-state--slot"><div class="empty-state__text">{{ T.ops.noBudget }}</div></div>
      <pre v-else class="ops-pre">{{ JSON.stringify(budget, null, 2) }}</pre>
    </template>

    <template v-else-if="activeTab === OpsTab.AUDIT">
      <div v-if="auditEvents.length === 0" class="empty-state empty-state--slot"><div class="empty-state__text">{{ T.ops.noAudit }}</div></div>
      <div v-else class="data-table-wrap">
        <table class="data-table">
          <thead>
            <tr><th>{{ T.ops.time }}</th><th>{{ T.ops.action }}</th><th>{{ T.ops.target }}</th><th>{{ T.ops.outcome }}</th><th>{{ T.ops.tenant }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(ev, i) in auditEvents" :key="i">
              <td class="cell-time">{{ fmtTime(ev.ts || ev.timestamp) }}</td>
              <td><span class="tag tag--accent">{{ ev.action }}</span></td>
              <td class="cell-id">{{ ev.target || '—' }}</td>
              <td><span class="tag" :class="ev.outcome === AuditOutcome.SUCCESS ? 'tag--ok' : ''">{{ ev.outcome || '—' }}</span></td>
              <td class="mono">{{ ev.tenant_id || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</div>
`
};
