import { decideReview, friendlyError, listReviewItems } from "../api.js";

const REVIEW_TYPES = Object.freeze([
  ["", "全部类型"],
  ["extraction", "抽取结果"],
  ["resolution", "实体消歧"],
  ["conflict", "事实冲突"],
  ["spotcheck", "抽样检查"],
  ["feedback", "用户反馈"],
]);
const STATUS_LABELS = Object.freeze({
  pending: "待处理",
  approved: "已通过",
  rejected: "已驳回",
  skipped: "已跳过",
});
const PAGE_SIZE = 50;

export const ReviewView = {
  name: "ReviewView",
  template: `
    <section class="view-stack review-view">
      <div class="review-summary">
        <div><p class="eyebrow">HUMAN QUALITY LOOP</p><h2>审核队列</h2><p>审核决策会留痕。此操作不会将内容写回知识图谱。</p></div>
        <div class="review-count"><strong>{{ pendingCount }}</strong><span>待处理项目</span></div>
      </div>
      <section class="panel">
        <div class="filter-toolbar">
          <label class="filter-field"><span>处理状态</span><select v-model="filters.status" @change="changeFilters"><option value="pending">待处理</option><option value="">全部状态</option><option value="approved">已通过</option><option value="rejected">已驳回</option><option value="skipped">已跳过</option></select></label>
          <label class="filter-field"><span>来源类型</span><select v-model="filters.type" @change="changeFilters"><option v-for="type in types" :key="type[0]" :value="type[0]">{{ type[1] }}</option></select></label>
          <button class="button button-secondary filter-refresh" type="button" :disabled="loading" @click="loadItems">{{ loading ? '加载中…' : '刷新队列' }}</button>
        </div>
        <div v-if="error" class="state-card state-error"><strong>无法读取审核队列</strong><p>{{ error }}</p><button class="button button-secondary" @click="loadItems">重试</button></div>
        <div v-else-if="loading && !items.length" class="state-card" role="status">正在读取审核队列…</div>
        <div v-else-if="!items.length && !loading" class="state-card"><span class="state-mark" aria-hidden="true">✓</span><strong>{{ filters.status === 'pending' ? (totalItems ? '当前页没有待处理项' : '队列已清空') : '没有符合条件的记录' }}</strong><p>{{ filters.status === 'pending' ? (totalItems ? '其他待处理项位于上一页。' : '新的待复核内容会显示在这里。') : '尝试调整筛选条件。' }}</p></div>
        <div v-else class="review-list">
          <article v-for="item in items" :key="item.id" class="review-card" :class="{ selected: selected?.id === item.id }">
            <button class="review-card-main" type="button" @click="selectItem(item)">
              <span class="review-type-icon">{{ typeShort(item.type) }}</span>
              <span class="review-card-copy"><span class="review-card-title"><strong>{{ typeLabel(item.type) }}</strong><span class="status-pill" :class="'status-' + item.status">{{ statusLabel(item.status) }}</span></span>
                <small>{{ summary(item.payload) }}</small><small>{{ formatDate(item.created_at) }} · {{ item.id }}</small></span>
              <span class="review-confidence"><strong>{{ Math.round((Number(item.confidence) || 0) * 100) }}%</strong><small>置信度</small></span>
            </button>
            <div v-if="selected?.id === item.id" class="review-detail">
              <div class="detail-grid"><div><span class="detail-label">批次</span><code>{{ item.batch_id || '—' }}</code></div><div><span class="detail-label">审核对象</span><code>{{ item.id }}</code></div></div>
              <details class="payload-details"><summary>查看完整上下文</summary><pre class="json-view">{{ formatJson(item.payload) }}</pre></details>
              <div v-if="item.status === 'pending'" class="decision-form">
                <label class="field-label" :for="'reviewNote-' + item.id">审核备注（可选）</label>
                <textarea :id="'reviewNote-' + item.id" v-model="decisionNote" rows="2" maxlength="1000" placeholder="记录判断依据或需要后续处理的说明"></textarea>
                <div class="decision-actions">
                  <button class="button button-primary" type="button" :disabled="saving" @click="submitDecision(item, 'approve')">通过</button>
                  <button class="button button-danger" type="button" :disabled="saving" @click="submitDecision(item, 'reject')">驳回</button>
                  <button class="button button-secondary" type="button" :disabled="saving" @click="submitDecision(item, 'skip')">跳过</button>
                </div>
                <p v-if="decisionError" class="inline-alert alert-error" role="alert">{{ decisionError }}</p>
              </div>
              <div v-else class="decision-record"><strong>决策记录</strong><span>{{ item.reviewer || '未知审核人' }} · {{ formatDate(item.decided_at) }}</span><p>{{ item.decision_note || '未填写备注' }}</p></div>
            </div>
          </article>
        </div>
        <footer v-if="offset > 0 || offset + items.length < totalItems" class="table-footer">
          <span>{{ rangeLabel }}</span><div class="pagination">
            <button class="button button-secondary" type="button" :disabled="offset === 0 || loading" @click="loadItems(Math.max(0, offset - PAGE_SIZE))">上一页</button>
            <button class="button button-secondary" type="button" :disabled="offset + items.length >= totalItems || loading" @click="loadItems(offset + PAGE_SIZE)">下一页</button>
          </div>
        </footer>
      </section>
    </section>
  `,
  props: { identity: { type: Object, required: true } },
  data() {
    return {
      items: [],
      totalItems: 0,
      pendingTotal: 0,
      offset: 0,
      PAGE_SIZE,
      types: REVIEW_TYPES,
      filters: { status: "pending", type: "" },
      loading: false,
      saving: false,
      error: "",
      decisionError: "",
      decisionNote: "",
      selected: null,
    };
  },
  computed: {
    pendingCount() { return this.pendingTotal; },
    rangeLabel() {
      if (!this.totalItems) return "0 条记录";
      const start = this.offset + 1;
      const end = Math.min(this.offset + this.items.length, this.totalItems);
      return `显示 ${start}–${end} 条，共 ${this.totalItems} 条`;
    },
  },
  watch: {
    pendingCount(value) { this.$emit("pending-count", value); },
  },
  activated() { this.loadItems(); },
  methods: {
    changeFilters() {
      this.offset = 0;
      this.loadItems(0);
    },
    async loadItems(offset = this.offset) {
      this.loading = true;
      this.error = "";
      this.selected = null;
      try {
        const result = await listReviewItems({ ...this.filters, limit: PAGE_SIZE, offset });
        this.items = result.data;
        this.totalItems = Number(result.meta.total || 0);
        this.pendingTotal = Number(result.meta.extra?.pending_count || 0);
        this.offset = offset;
      } catch (error) {
        this.error = friendlyError(error);
      } finally {
        this.loading = false;
      }
    },
    selectItem(item) {
      this.selected = this.selected?.id === item.id ? null : item;
      this.decisionNote = "";
      this.decisionError = "";
    },
    async submitDecision(item, decision) {
      if (this.saving) return;
      this.saving = true;
      this.decisionError = "";
      try {
        await decideReview(item.id, {
          decision,
          reviewer: this.identity.user_id,
          note: this.decisionNote.trim(),
        });
        const label = { approve: "通过", reject: "驳回", skip: "跳过" }[decision];
        this.$emit("notify", `已记录“${label}”决策。该操作不会写入知识图谱。`, "success");
        await this.loadItems();
      } catch (error) {
        this.decisionError = friendlyError(error);
        if (error.status === 404 || error.status === 409) await this.loadItems();
      } finally {
        this.saving = false;
      }
    },
    typeLabel(value) {
      return ({ extraction: "抽取结果", resolution: "实体消歧", conflict: "事实冲突", spotcheck: "抽样检查", feedback: "用户反馈" })[value] || value || "审核项目";
    },
    typeShort(value) { return this.typeLabel(value).slice(0, 1); },
    statusLabel(value) { return STATUS_LABELS[value] || value || "未知"; },
    summary(payload) {
      const value = payload || {};
      return value.question || value.text || value.name || value.task_id || `${Object.keys(value).length} 项上下文`;
    },
    formatJson(value) { return JSON.stringify(value || {}, null, 2); },
    formatDate(value) { return value ? new Date(Number(value) * 1000).toLocaleString() : "—"; },
  },
};
