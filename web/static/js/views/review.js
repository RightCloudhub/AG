/* Review queue view (P5-UI-02). Lists pending/decided review items and lets
 * an admin/operator approve/reject each with an optional note. Renders the
 * type-specific payload (conflict triples, feedback, spotcheck) as readable
 * rows. All dynamic text via mustache only.
 */
import { decideReview, fetchReviewQueue, friendlyError } from "../api.js";

const STATUS_LABELS = Object.freeze({
  pending: "待审",
  approved: "已通过",
  rejected: "已驳回",
  skipped: "已跳过",
});

const TYPE_LABELS = Object.freeze({
  extraction: "抽取",
  resolution: "消解",
  conflict: "冲突",
  spotcheck: "抽查",
  feedback: "反馈",
});

export const ReviewView = {
  name: "ReviewView",
  data() {
    return {
      loading: false,
      error: "",
      status: "pending",
      items: [],
      decidingId: "",
      notes: {},
    };
  },
  computed: {
    statusLabel() {
      return STATUS_LABELS[this.status] || this.status;
    },
  },
  methods: {
    statusLabelFor(s) {
      return STATUS_LABELS[s] || s || "";
    },
    typeLabelFor(t) {
      return TYPE_LABELS[t] || t || "";
    },
    async refresh() {
      this.loading = true;
      this.error = "";
      try {
        const res = await fetchReviewQueue(this.status);
        this.items = res.rows;
      } catch (err) {
        this.error = friendlyError(err);
      } finally {
        this.loading = false;
      }
    },
    setStatus(status) {
      this.status = status;
      this.refresh();
    },
    payloadRows(item) {
      return describePayload(item);
    },
    noteFor(item) {
      return this.notes[item.id] || "";
    },
    async decide(item, decision) {
      this.decidingId = item.id;
      this.error = "";
      try {
        await decideReview(item.id, {
          decision,
          reviewer: "",
          note: this.notes[item.id] || "",
        });
        await this.refresh();
      } catch (err) {
        this.error = friendlyError(err);
      } finally {
        this.decidingId = "";
      }
    },
  },
  activated() {
    this.refresh();
  },
  template: `
    <div class="view-page">
      <header class="view-head">
        <h1>审核队列</h1>
        <p class="topbar-sub">知识三元组人工审核 · 通过/驳回</p>
      </header>

      <div class="toolbar">
        <span class="seg">
          <button
            v-for="s in ['pending', 'approved', 'rejected', 'skipped']"
            :key="s"
            type="button"
            class="seg-btn"
            :class="{ active: status === s }"
            @click="setStatus(s)"
          >{{ statusLabelFor(s) }}</button>
        </span>
        <button type="button" class="chip" @click="refresh" :disabled="loading">刷新</button>
      </div>

      <p v-if="error" class="view-error">{{ error }}</p>
      <p v-if="loading" class="muted">加载中…</p>
      <p v-else-if="!items.length" class="muted">当前状态「{{ statusLabel }}」无条目</p>

      <div v-else class="review-list">
        <article v-for="item in items" :key="item.id" class="card review-item">
          <div class="review-head">
            <span class="tag">{{ typeLabelFor(item.type) }}</span>
            <span class="tag" :class="'tag-' + item.status">{{ statusLabelFor(item.status) }}</span>
            <span class="muted">置信 {{ Math.round(item.confidence * 100) }}%</span>
            <span class="muted">id {{ item.id }}</span>
          </div>

          <ul class="plain-list">
            <li v-for="(row, i) in payloadRows(item)" :key="i">
              <span class="payload-label">{{ row.label }}</span>{{ row.value }}
            </li>
          </ul>

          <div v-if="item.status === 'pending'" class="review-actions">
            <input
              class="fb-input"
              type="text"
              placeholder="决策备注（可选）…"
              :value="noteFor(item)"
              @input="notes[item.id] = $event.target.value"
            />
            <button
              type="button"
              class="fb-btn good"
              :disabled="decidingId === item.id"
              @click="decide(item, 'approve')"
            >通过</button>
            <button
              type="button"
              class="fb-btn bad"
              :disabled="decidingId === item.id"
              @click="decide(item, 'reject')"
            >驳回</button>
            <button
              type="button"
              class="fb-btn"
              :disabled="decidingId === item.id"
              @click="decide(item, 'skip')"
            >跳过</button>
          </div>

          <p v-else class="muted">
            已由 {{ item.reviewer || "—" }} 处理
            <template v-if="item.decision_note">：{{ item.decision_note }}</template>
          </p>
        </article>
      </div>
    </div>
  `,
};

/* Flatten the type-specific payload into readable label/value rows. */
function describePayload(item) {
  const payload = item.payload || {};
  const rows = [];
  if (item.type === "conflict") {
    const incoming = payload.incoming || {};
    const existing = payload.existing;
    if (existing) rows.push({ label: "现存关系", value: describeRelation(existing) });
    if (incoming.head && incoming.tail) {
      rows.push({ label: "拟新增", value: describeTriple(incoming) });
    }
    if (payload.relation_key) rows.push({ label: "键", value: payload.relation_key });
    if (payload.reason) rows.push({ label: "原因", value: payload.reason });
  } else if (item.type === "feedback") {
    if (payload.query_id) rows.push({ label: "query_id", value: payload.query_id });
    if (payload.accurate != null) rows.push({ label: "准确", value: payload.accurate ? "是" : "否" });
    if (payload.reason) rows.push({ label: "原因", value: payload.reason });
    if (payload.user_id) rows.push({ label: "用户", value: payload.user_id });
  } else if (item.type === "spotcheck") {
    if (payload.task_id) rows.push({ label: "任务", value: payload.task_id });
    if (payload.doc_count != null) rows.push({ label: "文档数", value: payload.doc_count });
  }
  if (!rows.length) rows.push({ label: "payload", value: JSON.stringify(payload) });
  return rows;
}

function describeRelation(rel) {
  return `${rel.head_name || "?"} -[${rel.type || "?"}]-> ${rel.tail_name || "?"}`;
}

function describeTriple(triple) {
  const head = (triple.head && triple.head.name) || "?";
  const tail = (triple.tail && triple.tail.name) || "?";
  return `${head} -[${triple.relation || "?"}]-> ${tail}`;
}
