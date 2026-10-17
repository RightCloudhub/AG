/* 运行观测 (admin): process metrics, budget windows, security events, query trace-back. */
import { fetchAuditEvents, fetchAuditQuery, fetchBudgetSnapshot, fetchHealth, fetchMetrics, fetchTrace } from "../api.js";
import { vue } from "../runtime.js";
import { fmtTime, workspace } from "../store.js";

const REFRESH_MS = 15000;
const TABS = [
  { id: "overview", label: "概览", icon: "ops" },
  { id: "budget", label: "预算", icon: "coins" },
  { id: "audit", label: "安全事件", icon: "shield" },
  { id: "trace", label: "查询追溯", icon: "link" },
];

function bars(map) {
  const rows = Object.entries(map || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, v]) => v));
  return rows.map(([label, value]) => ({ label, value, pct: Math.round((value / max) * 100) }));
}

function waterfall(spans) {
  const list = (spans || []).filter((s) => s.started_at);
  if (!list.length) return [];
  const t0 = Math.min(...list.map((s) => s.started_at));
  const t1 = Math.max(...list.map((s) => s.ended_at || s.started_at));
  const span = Math.max(1e-6, t1 - t0);
  return list.map((s) => ({
    id: s.span_id,
    name: s.name,
    ms: s.duration_ms ?? Math.round(((s.ended_at || s.started_at) - s.started_at) * 1000),
    left: ((s.started_at - t0) / span) * 100,
    width: Math.max(0.6, (((s.ended_at || s.started_at) - s.started_at) / span) * 100),
  }));
}

export default {
  name: "OpsView",
  setup() {
    const { ref, reactive, computed, onActivated, onDeactivated } = vue();
    const ws = workspace();
    const tab = ref("overview");
    const s = reactive({ metrics: null, health: null, budget: null, audit: [], trace: null, chain: null });
    const loading = ref(false);
    const error = ref("");
    const action = ref("");
    const queryId = ref("");
    let timer = 0;

    const routes = computed(() => bars(s.metrics?.route_counts));
    const errors = computed(() => bars(s.metrics?.error_counts));
    const failures = computed(() => Object.values(s.metrics?.event_handler_failures || {}).reduce((a, b) => a + b, 0));
    const spans = computed(() => waterfall(s.trace?.spans));
    const checks = computed(() => Object.entries(s.health?.checks || {}));

    async function run(fn) {
      loading.value = true;
      error.value = "";
      try {
        await fn();
      } catch (err) {
        error.value = err.message;
      } finally {
        loading.value = false;
      }
    }

    const loaders = {
      overview: () => run(async () => {
        const [m, h] = await Promise.all([fetchMetrics(), fetchHealth().catch(() => null)]);
        s.metrics = m.data;
        s.health = h;
      }),
      budget: () => run(async () => (s.budget = (await fetchBudgetSnapshot()).data)),
      audit: () => run(async () => (s.audit = (await fetchAuditEvents({ limit: 100, action: action.value })).data || [])),
      trace: () => queryId.value.trim() && lookup(),
    };

    function lookup() {
      const id = queryId.value.trim();
      if (!id) return;
      return run(async () => {
        const [trace, chain] = await Promise.all([fetchTrace(id).catch(() => null), fetchAuditQuery(id).catch(() => null)]);
        if (!trace && !chain) throw new Error(`没有找到 query_id ${id} 的追溯记录`);
        s.trace = trace?.data || null;
        s.chain = chain?.data || null;
      });
    }

    function open(id) {
      tab.value = id;
      loaders[id]();
    }

    function tick() {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => (tab.value === "overview" && loaders.overview(), tick()), REFRESH_MS);
    }

    function takeHandoff() {
      if (!ws.traceQueryId) return false;
      queryId.value = ws.traceQueryId;
      ws.traceQueryId = "";
      open("trace");
      return true;
    }

    onActivated(() => (takeHandoff() || loaders[tab.value](), tick()));
    onDeactivated(() => window.clearTimeout(timer));

    return { TABS, tab, s, loading, error, action, queryId, routes, errors, failures, spans, checks, open, lookup, loaders, fmtTime };
  },
  template: `
<div class="view-pad">
  <div class="toolbar">
    <div class="segmented" role="tablist">
      <button v-for="t in TABS" :key="t.id" role="tab" :aria-pressed="tab === t.id" @click="open(t.id)">{{ t.label }}</button>
    </div>
    <div class="toolbar__end"><button class="btn btn--sm" @click="loaders[tab]()" :disabled="loading"><app-icon name="refresh" size="sm" />刷新</button></div>
  </div>
  <state-card v-if="error" error icon="alert" title="请求失败" :text="error" />

  <template v-if="tab === 'overview' && s.metrics">
    <div class="stats">
      <stat-card icon="chat" label="查询数" :value="s.metrics.count" hint="进程启动以来" />
      <stat-card icon="clock" label="P50 延迟" :value="Math.round(s.metrics.latency_p50_ms)" unit="ms" />
      <stat-card icon="clock" label="P95 延迟" :value="Math.round(s.metrics.latency_p95_ms)" unit="ms" :hint="'P99 ' + Math.round(s.metrics.latency_p99_ms) + ' ms'" />
      <stat-card icon="route" label="平均跳数" :value="Number(s.metrics.hops_avg || 0).toFixed(2)" />
      <stat-card icon="coins" label="预算熔断" :value="s.metrics.budget_trips" />
      <stat-card icon="alert" label="关键事件失败" :value="failures" hint="缓存失效等，>0 需排查" />
    </div>
    <div class="ops-grid">
      <section class="card"><div class="card__head"><span class="card__title">路由分布</span></div>
        <div class="card__body bars">
          <div v-for="r in routes" :key="r.label" class="bar-row"><span class="bar-row__label">{{ r.label }}</span><span class="bar-row__track"><span class="bar-row__fill" :style="{ width: r.pct + '%' }"></span></span><span class="bar-row__value">{{ r.value }}</span></div>
          <p v-if="!routes.length" class="muted">暂无查询。</p>
        </div>
        <div class="card__head"><span class="card__title">错误码</span></div>
        <div class="card__body bars">
          <div v-for="r in errors" :key="r.label" class="bar-row"><span class="bar-row__label">{{ r.label }}</span><span class="bar-row__track"><span class="bar-row__fill bar-row__fill--bad" :style="{ width: r.pct + '%' }"></span></span><span class="bar-row__value">{{ r.value }}</span></div>
          <p v-if="!errors.length" class="muted">没有错误。</p>
        </div>
      </section>
      <section class="card"><div class="card__head"><span class="card__title">健康检查</span><span class="badge" :class="s.health && s.health.status === 'ok' ? 'badge--good' : 'badge--bad'">{{ s.health ? s.health.status : 'unreachable' }}</span></div>
        <div class="card__body"><dl class="kv"><template v-for="[k, v] in checks" :key="k"><dt>{{ k }}</dt><dd class="mono">{{ v }}</dd></template></dl></div>
      </section>
    </div>
  </template>

  <template v-if="tab === 'budget' && s.budget">
    <section v-for="scope in [['tenants', '租户窗口'], ['users', '用户窗口']]" :key="scope[0]" class="card">
      <div class="card__head"><span class="card__title">{{ scope[1] }}</span></div>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th>主体</th><th>LLM 调用</th><th>Tokens</th><th>成本单位</th><th>窗口起点</th></tr></thead>
        <tbody><tr v-for="(row, key) in s.budget[scope[0]]" :key="key"><td class="mono">{{ key }}</td><td class="mono">{{ row.llm_calls }}</td><td class="mono">{{ row.tokens }}</td><td class="mono">{{ Number(row.cost_units || 0).toFixed(2) }}</td><td class="mono muted">{{ fmtTime(row.window_start) }}</td></tr></tbody>
      </table></div>
    </section>
    <section class="card"><div class="card__head"><span class="card__title">熔断记录</span><span class="badge">{{ (s.budget.trips || []).length }}</span></div>
      <pre class="json-view" style="margin:12px">{{ JSON.stringify(s.budget.trips || [], null, 2) }}</pre></section>
  </template>

  <section v-if="tab === 'audit'" class="card">
    <div class="card__head"><span class="card__title">安全与操作事件</span>
      <div class="toolbar__end"><input class="input" style="width:200px" v-model="action" placeholder="按 action 过滤，如 auth_failure" @keydown.enter="loaders.audit()" /></div></div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>时间</th><th>动作</th><th>结果</th><th>租户 / 用户</th><th>目标</th></tr></thead>
      <tbody><tr v-for="e in s.audit" :key="e.event_id"><td class="mono muted">{{ fmtTime(e.ts) }}</td><td><span class="badge">{{ e.action }}</span></td><td><span class="audit-outcome" :class="'audit-outcome--' + e.outcome">{{ e.outcome }}</span></td><td class="mono">{{ e.tenant_id }} / {{ e.user_id || '—' }}</td><td class="mono break">{{ e.target }}</td></tr></tbody>
    </table></div>
    <div v-if="!s.audit.length && !loading" class="card__body"><state-card icon="shield" title="没有事件" /></div>
  </section>

  <section v-if="tab === 'trace'" class="card">
    <div class="card__head"><span class="card__title">按 query_id 回查</span></div>
    <div class="card__body">
      <form class="trace-form" @submit.prevent="lookup"><input class="input mono" v-model="queryId" placeholder="粘贴 query_id（答案卡片右上角 # 号）" /><button class="btn btn--primary" type="submit">查询</button></form>
      <div v-if="s.chain" style="margin-top:16px"><div class="card__title">{{ s.chain.question }}</div>
        <p class="secondary" style="margin-top:4px">{{ s.chain.answer }} · <span class="badge">{{ s.chain.status }}</span> <span class="badge badge--info">{{ s.chain.route }}</span></p></div>
      <div v-if="spans.length" class="waterfall" aria-label="Trace 瀑布图">
        <div v-for="sp in spans" :key="sp.id" class="wf-row"><span class="wf-row__name" :title="sp.name">{{ sp.name }}</span><span class="wf-row__track"><span class="wf-row__bar" :style="{ left: sp.left + '%', width: sp.width + '%' }"></span></span><span class="wf-row__ms">{{ sp.ms }} ms</span></div>
      </div>
      <p v-if="s.chain && !spans.length" class="muted" style="margin-top:12px">没有 trace span：该答案可能来自缓存，或 trace 已轮转出进程内存。</p>
      <pre v-if="s.chain" class="json-view" style="margin-top:16px">{{ JSON.stringify(s.chain.steps || [], null, 2) }}</pre>
    </div>
  </section>
</div>`,
};
