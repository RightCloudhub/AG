/* Review view (P5-UI-02 U-08, operator+): queue list + decisions. Duplicate
 * decisions surface 409 and cross-tenant/unknown ids 404 explicitly. BL-03 is
 * implemented: the decision response carries graph_effects, so the UI reports
 * both the recorded decision and the applied/failed graph write-back.
 */
import { fetchReviewQueue, postReviewDecision } from "../api-console.js";
import { errorState, formatTimestamp, shortId, truncateText } from "../components/console-widgets.js";

const STATUS_OPTIONS = [
  { value: "pending", label: "待处理" },
  { value: "approved", label: "已通过" },
  { value: "rejected", label: "已拒绝" },
  { value: "skipped", label: "已跳过" },
];
const TYPE_OPTIONS = [
  { value: "", label: "全部类型" },
  { value: "extraction", label: "extraction" },
  { value: "resolution", label: "resolution" },
  { value: "conflict", label: "conflict" },
  { value: "spotcheck", label: "spotcheck" },
  { value: "feedback", label: "feedback" },
];
const DECISION_LABELS = { approve: "通过", reject: "拒绝", skip: "跳过" };
const PAGE_SIZE = 20;

export const ReviewView = {
  name: "ReviewView",
  data() {
    return {
      status: "pending",
      type: "",
      items: [],
      total: 0,
      offset: 0,
      selected: null,
      note: "",
      deciding: false,
      error: null,
      lastDecision: null,
      typeOptions: TYPE_OPTIONS,
      statusOptions: STATUS_OPTIONS,
      columns: [
        { key: "id", label: "ID", render: (row) => shortId(row.id) },
        { key: "type", label: "类型" },
        { key: "status", label: "状态" },
        { key: "confidence", label: "置信度" },
        { key: "created", label: "创建时间", render: (row) => formatTimestamp(row.created_at) },
        { key: "payload", label: "上下文", render: (row) => truncateText(JSON.stringify(row.payload), 60) },
      ],
    };
  },
  computed: {
    page() {
      return Math.floor(this.offset / PAGE_SIZE) + 1;
    },
    pageCount() {
      return Math.max(1, Math.ceil(this.total / PAGE_SIZE));
    },
    selectedPending() {
      return Boolean(this.selected && this.selected.status === "pending");
    },
  },
  mounted() {
    this.load();
  },
  methods: {
    shortId,
    onFilter({ key, value }) {
      this[key] = value;
    },
    applyFilters() {
      this.offset = 0;
      this.selected = null;
      this.load();
    },
    turnPage(delta) {
      const next = (this.page + delta - 1) * PAGE_SIZE;
      if (next < 0 || (delta > 0 && this.page >= this.pageCount)) return;
      this.offset = next;
      this.load();
    },
    async load() {
      this.error = null;
      try {
        const env = await fetchReviewQueue({
          status: this.status,
          type: this.type,
          limit: PAGE_SIZE,
          offset: this.offset,
        });
        this.items = env.data || [];
        this.total = (env.meta && env.meta.total) || this.items.length;
      } catch (err) {
        this.error = errorState(err);
      }
    },
    pick(row) {
      this.selected = row;
      this.note = "";
      this.lastDecision = null;
    },
    async decide(decision) {
      if (!this.selected || this.deciding) return;
      this.deciding = true;
      this.lastDecision = null;
      try {
        const result = await postReviewDecision(this.selected.id, {
          decision,
          reviewer: "console",
          note: this.note || "",
        });
        this.lastDecision = this.describeWriteBack(decision, result);
        await this.load();
      } catch (err) {
        const code = err && err.code;
        if (code === "CONFLICT" || (err && err.status === 409)) {
          this.lastDecision = { kind: "error", text: "该项已被决策过（重复提交被拒绝，请刷新队列）" };
        } else if (err && err.status === 404) {
          this.lastDecision = { kind: "error", text: "该项不存在或不属于当前租户" };
        } else {
          this.lastDecision = { kind: "error", text: `决策失败: ${(err && err.message) || "未知错误"}` };
        }
      } finally {
        this.deciding = false;
      }
    },
    describeWriteBack(decision, result) {
      const label = DECISION_LABELS[decision] || decision;
      const effects = (result && result.graph_effects) || {};
      if (!effects.applied) {
        const reason = effects.reason || "无图谱副作用";
        return { kind: "done", text: `决策已记录：${label}；图谱写回：未应用（${reason}）` };
      }
      const relations = (effects.relations || []).length;
      const extra = effects.retired ? `，退役 ${effects.retired.length} 条` : "";
      return {
        kind: "done",
        text: `决策已记录：${label}；图谱写回：已应用 ${effects.action || "upsert"}（写入 ${relations} 条关系${extra}）`,
      };
    },
  },
  template: `
    <section class="view console-view">
      <state-card v-if="error" :kind="error.kind" :title="error.title" :detail="error.detail"></state-card>
      <template v-else>
        <div class="panel">
          <div class="panel-head"><h2>审核队列</h2></div>
          <filter-bar
            :filters="[
              { key: 'status', label: '状态', type: 'select', options: statusOptions },
              { key: 'type', label: '类型', type: 'select', options: typeOptions },
            ]"
            :values="{ status: status, type: type }"
            @update="onFilter"
            @apply="applyFilters"
          ></filter-bar>
          <data-table :columns="columns" :rows="items" empty-text="队列为空" clickable @row-click="pick"></data-table>
          <div class="pager">
            <span class="muted">第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }} 条</span>
            <span class="pager-btns">
              <button type="button" class="mini-btn" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
              <button type="button" class="mini-btn" :disabled="page >= pageCount" @click="turnPage(1)">下一页</button>
            </span>
          </div>
        </div>
        <div class="panel" v-if="selected">
          <div class="panel-head">
            <h2>决策 · {{ shortId(selected.id) }}</h2>
            <span class="muted">{{ selected.type }} · {{ selected.status }}</span>
          </div>
          <pre class="mono payload-box">{{ JSON.stringify(selected.payload, null, 2) }}</pre>
          <label class="field decision-note">
            <span>备注（可选）</span>
            <input type="text" v-model="note" placeholder="决策备注…" />
          </label>
          <div class="decision-row" v-if="selectedPending">
            <button type="button" class="chip good" :disabled="deciding" @click="decide('approve')">通过</button>
            <button type="button" class="chip bad" :disabled="deciding" @click="decide('reject')">拒绝</button>
            <button type="button" class="chip" :disabled="deciding" @click="decide('skip')">跳过</button>
          </div>
          <p v-else class="muted">该项已决策（{{ selected.status }}），不可重复决策。</p>
          <p v-if="lastDecision" class="feedback-note" :class="{ bad: lastDecision.kind === 'error' }">
            {{ lastDecision.text }}
          </p>
        </div>
        <state-card v-else kind="empty" title="未选择队列项" detail="点击上方表格中的行以查看上下文并决策。"></state-card>
      </template>
    </section>
  `,
};
