/* 知识接入: validated multi-file upload + paginated ingest-task list (polls while active).
 * Upload only creates a task; a worker (AGR_INGEST_INLINE=1 or agr-ingest-worker) runs it. */
import { fetchIngestTasks, uploadDocs } from "../api.js";
import { vue } from "../runtime.js";
import { fmtBytes, fmtTime } from "../store.js";
import { toast } from "../components/widgets.js";

const PAGE = 15;
const MAX_FILES = 20;
const MAX_BYTES = 5 * 1024 * 1024;
const EXTENSIONS = [".md", ".txt"];
const POLL_MS = 4000;
const STAGES = ["queued", "extracting", "review", "done"];
const STATUS = {
  queued: { text: "排队中", cls: "badge--info" },
  extracting: { text: "抽取中", cls: "badge--accent" },
  review: { text: "待审核", cls: "badge--warn" },
  done: { text: "已完成", cls: "badge--good" },
  empty: { text: "无可抽取内容", cls: "" },
  failed: { text: "失败", cls: "badge--bad" },
};
const ACTIVE = new Set(["queued", "extracting"]);

export function checkFile(file) {
  const name = file.name.toLowerCase();
  if (!EXTENSIONS.some((ext) => name.endsWith(ext))) return "仅支持 .md / .txt";
  if (file.size > MAX_BYTES) return "超过 5 MiB";
  if (file.size === 0) return "空文件";
  return "";
}

export default {
  name: "KnowledgeView",
  setup() {
    const { ref, computed, onActivated, onDeactivated, onBeforeUnmount } = vue();
    const tasks = ref([]);
    const total = ref(0);
    const page = ref(1);
    const loading = ref(false);
    const error = ref("");
    const picked = ref([]);
    const over = ref(false);
    const uploading = ref(false);
    const picker = ref(null);
    let timer = 0;

    const valid = computed(() => picked.value.filter((f) => !f.problem));
    const hasActive = computed(() => tasks.value.some((t) => ACTIVE.has(t.status)));

    async function load({ quiet = false } = {}) {
      if (!quiet) loading.value = true;
      try {
        const { data, meta } = await fetchIngestTasks({ limit: PAGE, offset: (page.value - 1) * PAGE });
        tasks.value = data || [];
        total.value = meta.total ?? tasks.value.length;
        error.value = "";
      } catch (err) {
        error.value = err.message;
      } finally {
        loading.value = false;
      }
      schedule();
    }

    function schedule() {
      window.clearTimeout(timer);
      if (hasActive.value) timer = window.setTimeout(() => load({ quiet: true }), POLL_MS);
    }

    function addFiles(list) {
      const incoming = Array.from(list || []).map((file) => ({ file, name: file.name, size: file.size, problem: checkFile(file) }));
      if (picked.value.length + incoming.length > MAX_FILES) toast(`单批最多 ${MAX_FILES} 个文件`, "error");
      picked.value = [...picked.value, ...incoming].slice(0, MAX_FILES);
    }

    function onDrop(e) {
      over.value = false;
      addFiles(e.dataTransfer?.files);
    }

    async function upload() {
      if (!valid.value.length || uploading.value) return;
      uploading.value = true;
      try {
        const { data } = await uploadDocs(valid.value.map((f) => f.file));
        toast(`已创建接入任务 ${String(data?.id || "").slice(0, 8)}`);
        picked.value = [];
        page.value = 1;
        await load();
      } catch (err) {
        toast(`上传失败：${err.message}`, "error");
      } finally {
        uploading.value = false;
      }
    }

    function stageClass(task, stage) {
      if (task.status === "failed") return "pipeline__step--fail";
      const at = STAGES.indexOf(task.status === "empty" ? "done" : task.status);
      const i = STAGES.indexOf(stage);
      if (i < at || task.status === "done") return "pipeline__step--done";
      return i === at ? "pipeline__step--now" : "";
    }

    function go(p) {
      page.value = p;
      load();
    }

    onActivated(() => load({ quiet: true }));
    onDeactivated(() => window.clearTimeout(timer));
    onBeforeUnmount(() => window.clearTimeout(timer));

    return {
      PAGE, STAGES, STATUS, MAX_FILES, tasks, total, page, loading, error, picked, over, uploading, picker,
      valid, hasActive, load, addFiles, onDrop, upload, stageClass, go, fmtBytes, fmtTime,
    };
  },
  template: `
<div class="view-pad">
  <div class="dropzone" :class="{ 'dropzone--over': over }" role="button" tabindex="0"
       @click="picker.click()" @keydown.enter="picker.click()" @dragover.prevent="over = true"
       @dragleave="over = false" @drop.prevent="onDrop">
    <div class="dropzone__icon"><app-icon name="upload" /></div>
    <div>
      <div class="card__title">拖入文档，或点击选择文件</div>
      <p class="secondary" style="font-size:13px">UTF-8 的 .md / .txt · 单文件 ≤ 5 MiB · 每批 ≤ {{ MAX_FILES }} 个。上传只创建接入任务，需由 worker 完成索引与抽取。</p>
    </div>
    <input ref="picker" type="file" multiple accept=".md,.txt,text/markdown,text/plain" hidden @change="addFiles($event.target.files); $event.target.value = ''" />
  </div>

  <section v-if="picked.length" class="card">
    <div class="card__head"><span class="card__title">待上传</span><span class="badge">{{ valid.length }} / {{ picked.length }} 个可上传</span>
      <div class="toolbar__end"><button class="btn btn--sm btn--ghost" @click="picked = []">清空</button>
        <button class="btn btn--sm btn--primary" :disabled="!valid.length || uploading" @click="upload"><span v-if="uploading" class="spinner"></span><app-icon v-else name="upload" size="sm" />{{ uploading ? '上传中…' : '开始上传' }}</button></div>
    </div>
    <div class="table-wrap"><table class="data-table">
      <thead><tr><th>文件</th><th>大小</th><th>校验</th></tr></thead>
      <tbody><tr v-for="(f, i) in picked" :key="i">
        <td class="break"><app-icon name="file" size="sm" /> {{ f.name }}</td><td class="mono">{{ fmtBytes(f.size) }}</td>
        <td><span class="badge" :class="f.problem ? 'badge--bad' : 'badge--good'">{{ f.problem || '通过' }}</span></td>
      </tr></tbody>
    </table></div>
  </section>

  <section class="card">
    <div class="card__head"><span class="card__title">接入任务</span>
      <span v-if="hasActive" class="badge badge--accent"><span class="spinner" style="width:10px;height:10px"></span>自动刷新中</span>
      <div class="toolbar__end"><button class="btn btn--sm" @click="load()" :disabled="loading"><app-icon name="refresh" size="sm" />刷新</button></div>
    </div>
    <div v-if="error" class="card__body"><state-card error icon="alert" title="任务列表加载失败" :text="error" /></div>
    <div v-else-if="loading && !tasks.length" class="card__body"><state-card loading /></div>
    <div v-else-if="!tasks.length" class="card__body"><state-card icon="knowledge" title="还没有接入任务" text="上传第一批文档后，这里会显示排队、抽取、审核到完成的每个阶段。" /></div>
    <div v-else class="table-wrap"><table class="data-table">
      <thead><tr><th>任务</th><th>文档</th><th>进度</th><th>状态</th><th>说明</th><th>更新时间</th></tr></thead>
      <tbody><tr v-for="t in tasks" :key="t.id">
        <td class="mono">{{ (t.id || '').slice(0, 8) }}</td>
        <td><div v-for="d in (t.docs || []).slice(0, 3)" :key="d.doc_id" class="break">{{ d.name || d.doc_id }}</div><span v-if="(t.docs || []).length > 3" class="muted">等 {{ t.docs.length }} 个</span></td>
        <td><div class="pipeline" :aria-label="'阶段：' + t.status"><span v-for="s in STAGES" :key="s" class="pipeline__step" :class="stageClass(t, s)"></span></div></td>
        <td><span class="badge" :class="(STATUS[t.status] || {}).cls">{{ (STATUS[t.status] || {}).text || t.status }}</span></td>
        <td class="secondary" style="max-width:280px">{{ t.message || '—' }}</td>
        <td class="mono muted">{{ fmtTime(t.updated_at || t.created_at) }}</td>
      </tr></tbody>
    </table></div>
    <data-pager v-if="tasks.length" :page="page" :total="total" :limit="PAGE" unit="个任务" @go="go" />
  </section>
</div>`,
};
