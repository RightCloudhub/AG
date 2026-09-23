import {
  fetchAuditEvents,
  fetchAuditQuery,
  fetchBudget,
  fetchMetrics,
  fetchTrace,
  friendlyError,
} from "../api.js";

const EVENT_ACTIONS = Object.freeze([
  ["", "全部事件"],
  ["auth_failure", "认证失败"],
  ["rate_limited", "请求限流"],
  ["budget_exceeded", "预算触顶"],
  ["review_decision", "审核决策"],
  ["doc_upload", "文档接入"],
  ["feedback_submitted", "用户反馈"],
]);

export const OpsView = {
  name: "OpsView",
  template: `
    <section class="view-stack ops-view">
      <div class="ops-toolbar"><div><p class="eyebrow">SERVICE OBSERVABILITY</p><h2>运行概览</h2><p>服务端进程内统计与安全事件，用于排障和审计。</p></div>
        <button class="button button-secondary" type="button" :disabled="refreshing" @click="refreshAll">{{ refreshing ? '刷新中…' : '刷新数据' }}</button></div>
      <div v-if="metricsError" class="inline-alert alert-warning">指标暂不可用：{{ metricsError }}</div>
      <div class="metric-strip ops-metrics">
        <article class="metric-tile"><span>已处理查询</span><strong>{{ metrics.count ?? '—' }}</strong><small>当前服务进程</small></article>
        <article class="metric-tile"><span>P50 延迟</span><strong>{{ formatMs(metrics.latency_p50_ms) }}</strong><small>毫秒</small></article>
        <article class="metric-tile"><span>P95 延迟</span><strong>{{ formatMs(metrics.latency_p95_ms) }}</strong><small>毫秒</small></article>
        <article class="metric-tile"><span>平均推理跳数</span><strong>{{ formatNumber(metrics.hops_avg) }}</strong><small>查询级统计</small></article>
      </div>
      <div class="ops-grid">
        <section class="panel"><div class="panel-heading"><div><p class="eyebrow">BUDGET</p><h3>预算快照</h3></div></div>
          <div v-if="budgetError" class="inline-alert alert-warning">{{ budgetError }}</div>
          <div v-else-if="refreshing && !budgetLoaded" class="state-inline" role="status">正在读取预算…</div>
          <div v-else-if="!budgetRows.length" class="state-inline">当前没有预算消耗记录。</div>
          <dl v-else class="budget-list"><div v-for="entry in budgetRows" :key="entry[0]"><dt>{{ entry[0] }}</dt><dd>{{ formatJsonValue(entry[1]) }}</dd></div></dl>
        </section>
        <section class="panel"><div class="panel-heading"><div><p class="eyebrow">QUERY ROUTES</p><h3>查询路径</h3></div></div>
          <div v-if="routeRows.length" class="route-list"><div v-for="entry in routeRows" :key="entry[0]" class="route-row"><span>{{ routeLabel(entry[0]) }}</span><strong>{{ entry[1] }}</strong></div></div>
          <div v-else-if="refreshing && !metricsLoaded" class="state-inline" role="status">正在读取指标…</div>
          <div v-else class="state-inline">尚无路径统计。</div>
          <div class="ops-substat"><span>预算触顶</span><strong>{{ metrics.budget_trips ?? 0 }}</strong></div>
          <details class="payload-details"><summary>查看错误计数</summary><pre class="json-view">{{ formatJson(metrics.error_counts || {}) }}</pre></details>
        </section>
      </div>

      <section class="panel audit-panel"><div class="panel-heading"><div><p class="eyebrow">SECURITY AUDIT</p><h3>安全事件</h3><p>默认查询最近 24 小时，筛选条件仅影响当前列表。</p></div></div>
        <form class="filter-toolbar audit-filters" @submit.prevent="loadEvents">
          <label class="filter-field"><span>事件类型</span><select v-model="eventFilters.action"><option v-for="option in actions" :key="option[0]" :value="option[0]">{{ option[1] }}</option></select></label>
          <label class="filter-field"><span>租户 ID</span><input v-model.trim="eventFilters.tenant_id" type="text" placeholder="所有租户" /></label>
          <label class="filter-field"><span>时间范围</span><select v-model.number="eventWindow"><option :value="24">最近 24 小时</option><option :value="72">最近 3 天</option><option :value="168">最近 7 天</option></select></label>
          <label class="filter-field"><span>最多显示</span><select v-model.number="eventFilters.limit"><option :value="50">50 条</option><option :value="100">100 条</option><option :value="200">200 条</option><option :value="500">500 条</option></select></label>
          <button class="button button-primary" type="submit" :disabled="eventsLoading">{{ eventsLoading ? '读取中…' : '筛选' }}</button>
        </form>
        <div v-if="eventsError" class="inline-alert alert-warning">{{ eventsError }}</div>
        <div v-else-if="eventsLoading && !events.length" class="state-inline" role="status">正在读取安全事件…</div>
        <div v-else-if="!events.length && !eventsLoading" class="state-inline">该时间范围内没有安全事件。</div>
        <div v-else class="audit-event-list" aria-live="polite">
          <details v-for="event in events" :key="event.event_id" class="audit-event">
            <summary><time>{{ formatDate(event.ts) }}</time><span class="event-action">{{ eventLabel(event.action) }}</span><span>{{ event.outcome || '—' }}</span><code>{{ event.tenant_id || '—' }}</code><span class="event-target">{{ event.target || '—' }}</span></summary>
            <pre class="json-view">{{ formatJson(event) }}</pre>
          </details>
        </div>
      </section>

      <section class="panel trace-panel"><div class="panel-heading"><div><p class="eyebrow">QUERY FORENSICS</p><h3>查询审计回查</h3><p>输入查询 ID，查看答案、证据与执行 trace。</p></div></div>
        <form class="lookup-form" @submit.prevent="lookupQuery"><label class="sr-only" for="queryLookup">查询 ID</label><input id="queryLookup" v-model.trim="queryId" type="text" placeholder="粘贴 query_id" />
          <button class="button button-primary" type="submit" :disabled="lookupLoading || !queryId">{{ lookupLoading ? '查询中…' : '查找查询' }}</button></form>
        <div v-if="lookupError" class="inline-alert alert-warning" role="alert">{{ lookupError }}</div>
        <article v-if="auditRecord" class="audit-result">
          <div class="audit-result-head"><span class="status-pill status-ready">{{ auditRecord.status || '已记录' }}</span><code>{{ auditRecord.query_id }}</code></div>
          <div class="query-pair"><span>问题</span><p>{{ auditRecord.question || '—' }}</p></div>
          <div class="query-pair"><span>答案</span><p>{{ auditRecord.answer || '—' }}</p></div>
          <details class="payload-details" open><summary>推理步骤（{{ auditRecord.steps?.length || 0 }}）</summary>
            <ol class="forensic-steps"><li v-for="(step, index) in auditRecord.steps || []" :key="index"><strong>{{ step.sub_question || ('步骤 ' + (index + 1)) }}</strong><p>{{ step.conclusion || '没有记录结论' }}</p><small>Hop {{ step.hop ?? index + 1 }} · {{ (step.evidence_ids || []).join(', ') || '无证据 ID' }}</small></li></ol></details>
          <details class="payload-details"><summary>完整审计数据</summary><pre class="json-view">{{ formatJson(auditRecord) }}</pre></details>
        </article>
        <div v-if="traceError" class="inline-alert alert-warning">Trace：{{ traceError }}</div>
        <section v-if="traceRecord" class="trace-result"><h4>执行 Trace · {{ traceRecord.spans?.length || 0 }} 个 span</h4>
          <div v-for="span in traceRecord.spans || []" :key="span.span_id" class="trace-row"><span>{{ span.name }}</span><code>{{ span.span_id }}</code><strong>{{ span.duration_ms ?? '—' }} ms</strong><time>{{ formatDate(span.started_at) }}</time></div>
        </section>
      </section>
    </section>
  `,
  data() {
    return {
      actions: EVENT_ACTIONS,
      metrics: {},
      metricsLoaded: false,
      budget: {},
      budgetLoaded: false,
      events: [],
      eventFilters: { action: "", tenant_id: "", limit: 100 },
      eventWindow: 24,
      queryId: "",
      auditRecord: null,
      traceRecord: null,
      metricsError: "",
      budgetError: "",
      eventsError: "",
      lookupError: "",
      traceError: "",
      refreshing: false,
      eventsLoading: false,
      lookupLoading: false,
    };
  },
  computed: {
    budgetRows() { return Object.entries(this.budget || {}); },
    routeRows() { return Object.entries(this.metrics.route_counts || {}); },
  },
  activated() { this.refreshAll(); },
  methods: {
    async refreshAll() {
      this.refreshing = true;
      const results = await Promise.allSettled([fetchMetrics(), fetchBudget()]);
      this.metricsError = results[0].status === "rejected" ? friendlyError(results[0].reason) : "";
      this.budgetError = results[1].status === "rejected" ? friendlyError(results[1].reason) : "";
      if (!this.metricsError) {
        this.metrics = results[0].value;
        this.metricsLoaded = true;
      }
      if (!this.budgetError) {
        this.budget = results[1].value;
        this.budgetLoaded = true;
      }
      await this.loadEvents();
      this.refreshing = false;
    },
    async loadEvents() {
      this.eventsLoading = true;
      this.eventsError = "";
      try {
        const until = Date.now() / 1000;
        const result = await fetchAuditEvents({
          ...this.eventFilters,
          since: until - this.eventWindow * 60 * 60,
          until,
        });
        this.events = result;
      } catch (error) {
        this.eventsError = friendlyError(error);
      } finally {
        this.eventsLoading = false;
      }
    },
    async lookupQuery() {
      if (!this.queryId || this.lookupLoading) return;
      this.lookupLoading = true;
      this.lookupError = "";
      this.traceError = "";
      this.auditRecord = null;
      this.traceRecord = null;
      try {
        this.auditRecord = await fetchAuditQuery(this.queryId);
        try {
          this.traceRecord = await fetchTrace(this.queryId);
        } catch (error) {
          this.traceError = friendlyError(error);
        }
      } catch (error) {
        this.lookupError = friendlyError(error);
      } finally {
        this.lookupLoading = false;
      }
    },
    formatMs(value) { return Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString() : "—"; },
    formatNumber(value) { return Number.isFinite(Number(value)) ? Number(value).toFixed(1) : "—"; },
    formatDate(value) { return value ? new Date(Number(value) * 1000).toLocaleString() : "—"; },
    formatJson(value) { return JSON.stringify(value || {}, null, 2); },
    formatJsonValue(value) { return typeof value === "object" ? JSON.stringify(value) : String(value ?? "—"); },
    routeLabel(value) { return ({ fast: "快速路径", agentic: "Agent 推理" })[value] || value; },
    eventLabel(value) { return Object.fromEntries(EVENT_ACTIONS)[value] || value || "未知事件"; },
  },
};
