/* 审核队列: status tabs, type filter, context cards, approve / reject / skip with a note. */
import { decideReview, fetchReviewQueue } from "../api.js";
import { vue } from "../runtime.js";
import { fmtTime, workspace } from "../store.js";
import { toast } from "../components/widgets.js";

const PAGE = 12;
const STATUSES = [
  { id: "pending", label: "待审核" },
  { id: "approved", label: "已通过" },
  { id: "rejected", label: "已驳回" },
  { id: "skipped", label: "已跳过" },
];
const TYPES = [
  { id: "", label: "全部类型" },
  { id: "extraction", label: "抽取" },
  { id: "resolution", label: "实体对齐" },
  { id: "conflict", label: "冲突" },
  { id: "spotcheck", label: "抽检" },
  { id: "feedback", label: "用户反馈" },
];
const TYPE_BADGE = { conflict: "badge--warn", feedback: "badge--accent", spotcheck: "badge--info", extraction: "badge--good" };
const PAYLOAD_LABELS = {
  query_id: "查询", accurate: "评价", reason: "原因", user_id: "用户", task_id: "任务",
  doc_count: "文档数", doc_ids: "文档", head: "头实体", relation: "关系", tail: "尾实体",
  existing: "已有事实", incoming: "新事实", confidence: "置信度",
};

function show(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  if (Array.isArray(value)) return value.map(show).join("、");
  if (value && typeof value === "object") return value.name || JSON.stringify(value);
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export default {
  name: "ReviewView",
  setup() {
    const { ref, reactive, onActivated } = vue();
    const ws = workspace();
    const items = ref([]);
    const total = ref(0);
    const page = ref(1);
    const status = ref("pending");
    const type = ref("");
    const loading = ref(false);
    const error = ref("");
    const notes = reactive({});
    const busy = reactive({});
    const raw = reactive({});

    async function load() {
      loading.value = true;
      error.value = "";
      try {
        const { data, meta } = await fetchReviewQueue({
          limit: PAGE, offset: (page.value - 1) * PAGE, status: status.value, type: type.value,
        });
        items.value = data || [];
        total.value = meta.total ?? items.value.length;
        if (meta.extra && meta.extra.pending_count !== undefined) ws.reviewPending = meta.extra.pending_count;
      } catch (err) {
        items.value = [];
        error.value = err.message;
      } finally {
        loading.value = false;
      }
    }

    function fields(item) {
      return Object.entries(item.payload || {})
        .filter(([, v]) => v !== null && v !== undefined && v !== "")
        .slice(0, 8)
        .map(([k, v]) => ({ k: PAYLOAD_LABELS[k] || k, v: show(v) }));
    }

    async function decide(item, decision) {
      busy[item.id] = decision;
      try {
        const { data } = await decideReview(item.id, { decision, note: notes[item.id] || "", reviewer: ws.me?.user_id || "" });
        const effect = data && data.effect && data.effect !== "none" ? `（${data.effect}）` : "";
        toast(`已${decision === "approve" ? "通过" : decision === "reject" ? "驳回" : "跳过"}${effect}`);
        await load();
      } catch (err) {
        toast(`决策失败：${err.message}`, "error");
      } finally {
        delete busy[item.id];
      }
    }

    function setStatus(id) {
      status.value = id;
      page.value = 1;
      load();
    }

    function setType(id) {
      type.value = id;
      page.value = 1;
      load();
    }

    function go(p) {
      page.value = p;
      load();
    }

    onActivated(load);
    return {
      PAGE, STATUSES, TYPES, TYPE_BADGE, items, total, page, status, type, loading, error, notes, busy, raw,
      load, fields, decide, setStatus, setType, go, fmtTime, ws,
    };
  },
  template: `
<div class="view-pad">
  <div class="toolbar">
    <div class="segmented" role="group" aria-label="审核状态">
      <button v-for="s in STATUSES" :key="s.id" :aria-pressed="status === s.id" @click="setStatus(s.id)">{{ s.label }}<template v-if="s.id === 'pending' && ws.reviewPending"> · {{ ws.reviewPending }}</template></button>
    </div>
    <label class="sr-only" for="reviewType">类型</label>
    <select id="reviewType" class="select" style="width:auto" :value="type" @change="setType($event.target.value)">
      <option v-for="t in TYPES" :key="t.id" :value="t.id">{{ t.label }}</option>
    </select>
    <div class="toolbar__end">
      <span class="muted" style="font-size:12px">决策写入审核记录；通过的冲突项由执行器落图。</span>
      <button class="btn btn--sm" @click="load" :disabled="loading"><app-icon name="refresh" size="sm" />刷新</button>
    </div>
  </div>

  <state-card v-if="loading && !items.length" loading />
  <state-card v-else-if="error" error icon="alert" title="审核队列加载失败" :text="error"><button class="btn btn--sm" @click="load">重试</button></state-card>
  <state-card v-else-if="!items.length" icon="review" title="这里很干净" text="当前筛选条件下没有审核项。" />

  <template v-else>
    <div class="review-grid">
      <article v-for="item in items" :key="item.id" class="review-card">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <span class="badge" :class="TYPE_BADGE[item.type] || ''">{{ (TYPES.find(t => t.id === item.type) || {}).label || item.type }}</span>
          <span class="badge">置信度 {{ Number(item.confidence || 0).toFixed(2) }}</span>
          <span class="mono muted" style="margin-left:auto;font-size:11px">{{ (item.id || '').slice(0, 8) }}</span>
        </div>
        <dl class="kv">
          <template v-for="f in fields(item)" :key="f.k"><dt>{{ f.k }}</dt><dd>{{ f.v }}</dd></template>
          <dt>创建</dt><dd class="mono">{{ fmtTime(item.created_at) }}</dd>
          <template v-if="item.decided_at"><dt>决策</dt><dd>{{ item.reviewer || '—' }} · {{ fmtTime(item.decided_at) }}</dd></template>
          <template v-if="item.decision_note"><dt>备注</dt><dd>{{ item.decision_note }}</dd></template>
        </dl>
        <button class="mini-btn" style="align-self:flex-start" @click="raw[item.id] = !raw[item.id]"><app-icon name="layers" size="sm" />{{ raw[item.id] ? '收起' : '查看' }} JSON</button>
        <pre v-if="raw[item.id]" class="json-view">{{ JSON.stringify(item.payload, null, 2) }}</pre>
        <template v-if="item.status === 'pending'">
          <input class="input" v-model="notes[item.id]" maxlength="500" placeholder="决策备注（可选）" :aria-label="'备注 ' + item.id" />
          <div class="review-card__actions">
            <button class="btn btn--sm btn--good" :disabled="!!busy[item.id]" @click="decide(item, 'approve')"><app-icon name="check" size="sm" />通过</button>
            <button class="btn btn--sm btn--danger" :disabled="!!busy[item.id]" @click="decide(item, 'reject')"><app-icon name="x" size="sm" />驳回</button>
            <button class="btn btn--sm btn--ghost" :disabled="!!busy[item.id]" @click="decide(item, 'skip')"><app-icon name="skip" size="sm" />跳过</button>
          </div>
        </template>
      </article>
    </div>
    <div class="card"><data-pager :page="page" :total="total" :limit="PAGE" unit="项" @go="go" /></div>
  </template>
</div>`,
};
