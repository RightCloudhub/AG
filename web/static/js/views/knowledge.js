/* Knowledge-ops view (P5-UI-02 U-07, operator+): document upload with
 * client-side prechecks (5MB / 20 files / md·txt only; PDF explicitly
 * rejected) and the ingest-task list with ≥2s polling that stops while the
 * tab is hidden or the view is unmounted.
 */
import { fetchIngestTasks, uploadDocs } from "../api-console.js";
import { pushToast } from "../components/toast.js";
import { errorState, formatTimestamp, shortId } from "../components/console-widgets.js";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_FILES = 20;
const ALLOWED_EXT = /\.(md|txt)$/i;
const POLL_MS = 3000;
const PAGE_SIZE = 10;

export const KnowledgeView = {
  name: "KnowledgeView",
  data() {
    return {
      fileChecks: [],
      uploading: false,
      uploadMessage: "",
      uploadOk: false,
      tasks: [],
      total: 0,
      offset: 0,
      loading: false,
      error: null,
      taskColumns: [
        { key: "id", label: "任务", render: (row) => shortId(row.id) },
        { key: "status", label: "状态", badge: true },
        {
          key: "docs",
          label: "文档数",
          render: (row) => String((row.docs || []).length),
        },
        { key: "message", label: "消息", render: (row) => row.message || "" },
        { key: "updated_at", label: "更新时间", render: (row) => formatTimestamp(row.updated_at) },
      ],
    };
  },
  computed: {
    validFiles() {
      return this.fileChecks.filter((c) => c.ok).map((c) => c.file);
    },
    page() {
      return Math.floor(this.offset / PAGE_SIZE) + 1;
    },
    pageCount() {
      return Math.max(1, Math.ceil(this.total / PAGE_SIZE));
    },
  },
  mounted() {
    this.loadTasks();
    this._timer = window.setInterval(() => {
      if (!document.hidden) this.loadTasks(true);
    }, POLL_MS);
  },
  beforeUnmount() {
    if (this._timer) window.clearInterval(this._timer);
    this._timer = null;
  },
  methods: {
    onPickFiles(event) {
      this.uploadMessage = "";
      const picked = Array.from(event.target.files || []);
      this.fileChecks = picked.map((file, index) => this.checkFile(file, index));
    },
    checkFile(file, index) {
      if (index >= MAX_FILES) return { file, name: file.name, ok: false, note: `超过 ${MAX_FILES} 个文件上限` };
      if (/\.pdf$/i.test(file.name)) return { file, name: file.name, ok: false, note: "PDF 暂不支持（仅 md/txt）" };
      if (!ALLOWED_EXT.test(file.name)) return { file, name: file.name, ok: false, note: "不支持的类型（仅 md/txt）" };
      if (file.size > MAX_FILE_BYTES) return { file, name: file.name, ok: false, note: "超过 5MB 大小限制" };
      return { file, name: file.name, ok: true, note: "可上传" };
    },
    async upload() {
      if (!this.validFiles.length || this.uploading) return;
      this.uploading = true;
      this.uploadMessage = "";
      try {
        await uploadDocs(this.validFiles);
        this.uploadOk = true;
        this.uploadMessage = "上传成功，已创建抽取任务（见下方列表）";
        pushToast("上传成功，已创建抽取任务", "good");
        this.fileChecks = [];
        this.offset = 0;
        await this.loadTasks();
      } catch (err) {
        this.uploadOk = false;
        this.uploadMessage = `上传失败: ${(err && err.message) || "未知错误"}`;
        pushToast(this.uploadMessage, "bad");
      } finally {
        this.uploading = false;
      }
    },
    async loadTasks(silent = false) {
      if (!silent) {
        this.error = null;
        this.loading = true;
      }
      try {
        const env = await fetchIngestTasks({ limit: PAGE_SIZE, offset: this.offset });
        this.tasks = env.data || [];
        this.total = (env.meta && env.meta.total) || this.tasks.length;
      } catch (err) {
        if (!silent) this.error = errorState(err);
      } finally {
        if (!silent) this.loading = false;
      }
    },
    turnPage(delta) {
      const next = (this.page + delta - 1) * PAGE_SIZE;
      if (next < 0 || (delta > 0 && this.page >= this.pageCount)) return;
      this.offset = next;
      this.loadTasks();
    },
  },
  template: `
    <section class="view console-view">
      <state-card v-if="error" :kind="error.kind" :title="error.title" :detail="error.detail"></state-card>
      <template v-else>
        <div class="panel">
          <div class="panel-head">
            <h2>文档上传</h2>
            <p class="panel-sub">单文件 ≤5MB · 一次 ≤20 个 · 仅 md/txt（PDF 暂不支持）</p>
          </div>
          <div class="upload-row">
            <input ref="fileBox" type="file" multiple @change="onPickFiles" />
            <button type="button" class="send-btn" :disabled="!validFiles.length || uploading" @click="upload">
              <span>{{ uploading ? "上传中…" : "上传" }}</span>
            </button>
          </div>
          <ul v-if="fileChecks.length" class="file-checks">
            <li v-for="c in fileChecks" :key="c.name" :class="{ bad: !c.ok }">{{ c.name }} — {{ c.note }}</li>
          </ul>
          <p v-if="uploadMessage" class="feedback-note" :class="{ bad: !uploadOk }">{{ uploadMessage }}</p>
        </div>
        <div class="panel">
          <div class="panel-head">
            <h2>抽取任务</h2>
            <button type="button" class="mini-btn" @click="loadTasks">刷新</button>
          </div>
          <data-table :columns="taskColumns" :rows="tasks" empty-text="暂无任务" :loading="loading"></data-table>
          <div class="pager">
            <span class="muted">第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }} 条（自动轮询中，页面隐藏时暂停）</span>
            <span class="pager-btns">
              <button type="button" class="mini-btn" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
              <button type="button" class="mini-btn" :disabled="page >= pageCount" @click="turnPage(1)">下一页</button>
            </span>
          </div>
        </div>
      </template>
    </section>
  `,
};
