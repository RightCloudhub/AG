import { friendlyError, listIngestTasks, uploadDocuments } from "../api.js";

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_BATCH_FILES = 20;
const POLL_INTERVAL_MS = 5000;
const ACTIVE_TASKS = new Set(["queued", "extracting", "review"]);

export const KnowledgeView = {
  name: "KnowledgeView",
  template: `
    <section class="view-stack knowledge-view">
      <div class="metric-strip">
        <article class="metric-tile"><span>当前任务</span><strong>{{ totalTasks }}</strong><small>最近任务记录</small></article>
        <article class="metric-tile"><span>等待处理</span><strong>{{ activeCount }}</strong><small>队列中的接入任务</small></article>
        <article class="metric-tile"><span>本页文档</span><strong>{{ visibleDocumentCount }}</strong><small>最近 50 个任务</small></article>
      </div>
      <section class="panel upload-panel">
        <div class="panel-heading"><div><p class="eyebrow">KNOWLEDGE INTAKE</p><h2>接入文档</h2><p>上传 Markdown 或纯文本，任务会进入知识处理队列。</p></div>
          <span class="panel-index">01</span></div>
        <label class="upload-drop" :class="{ 'drop-active': dragActive }" for="documentFiles"
          @dragover.prevent="dragActive = true" @dragleave.prevent="dragActive = false" @drop.prevent="onDrop">
          <span class="upload-symbol" aria-hidden="true">＋</span><strong>拖放文件到此处，或浏览文件</strong>
          <small>支持 .md、.txt · 单文件不超过 5 MB · 每批最多 20 个</small>
          <input id="documentFiles" type="file" multiple accept=".md,.txt,text/plain,text/markdown" @change="selectFiles" />
        </label>
        <div v-if="selectedFiles.length" class="selected-files">
          <div class="selected-heading"><strong>已选择 {{ selectedFiles.length }} 个文件</strong>
            <button class="button button-quiet" type="button" @click="clearSelection">清除</button></div>
          <div v-for="file in selectedFiles" :key="file.name + file.lastModified" class="file-row">
            <span class="file-icon">TXT</span><span class="file-name">{{ file.name }}<small>{{ formatBytes(file.size) }}</small></span>
            <span v-if="fileIssue(file)" class="status-pill status-failed">{{ fileIssue(file) }}</span>
            <span v-else class="status-pill status-ready">待上传</span>
          </div>
          <button class="button button-primary upload-submit" type="button" :disabled="uploading || validFiles.length === 0" @click="uploadSelected">
            {{ uploading ? '正在上传…' : '上传 ' + Math.min(validFiles.length, 20) + ' 个文件' }}
          </button>
        </div>
        <div v-if="uploadMessage" class="inline-alert" :class="uploadError ? 'alert-error' : 'alert-success'" role="status">{{ uploadMessage }}</div>
      </section>

      <section class="panel task-panel">
        <div class="panel-heading"><div><p class="eyebrow">INGEST ACTIVITY</p><h2>接入任务</h2><p>任务状态来自服务端；正在处理的任务会自动刷新。</p></div>
          <button class="button button-secondary" type="button" :disabled="loading" @click="loadTasks()">{{ loading ? '刷新中…' : '刷新列表' }}</button></div>
        <div v-if="loadError" class="state-card state-error"><strong>无法读取任务</strong><p>{{ loadError }}</p><button class="button button-secondary" @click="loadTasks()">重试</button></div>
        <div v-else-if="loading && !tasks.length" class="state-card" role="status">正在读取接入任务…</div>
        <div v-else-if="!tasks.length && !loading" class="state-card"><span class="state-mark" aria-hidden="true">文</span><strong>还没有接入任务</strong><p>上传一份 Markdown 或纯文本，任务会显示在这里。</p></div>
        <div v-else class="task-list" aria-live="polite">
          <article v-for="task in tasks" :key="task.id" class="task-card">
            <div class="task-main"><span class="task-status-mark" :class="'task-' + task.status" aria-hidden="true"></span>
              <div class="task-copy"><div class="task-title-row"><strong>{{ task.docs?.length || 0 }} 个文档</strong><span class="status-pill" :class="taskStatusClass(task.status)">{{ taskStatusLabel(task.status) }}</span></div>
                <p>{{ task.message || task.id }}</p><small>{{ formatDate(task.created_at) }} · {{ task.id }}</small></div>
              <button class="icon-button" type="button" :aria-label="'查看任务 ' + task.id" @click="toggleTask(task.id)">{{ expandedTask === task.id ? '−' : '+' }}</button>
            </div>
            <div v-if="expandedTask === task.id" class="task-details">
              <div v-for="doc in task.docs || []" :key="doc.doc_id || doc.name" class="file-row">
                <span class="file-icon">TXT</span><span class="file-name">{{ doc.name || doc.doc_id }}<small>{{ formatBytes(Number(doc.bytes || 0)) }}</small></span>
                <span v-if="doc.error" class="status-pill status-failed">处理失败</span><span v-else class="status-pill status-ready">已接收</span>
              </div>
            </div>
          </article>
        </div>
        <button v-if="tasks.length < totalTasks" class="button button-quiet load-more" type="button" :disabled="loading" @click="loadMore">加载更多任务</button>
      </section>
      <p class="view-footnote">任务由独立 ingest worker 处理；默认离线索引在 worker 进程内运行。上传仅表示资料已接收，不会自动抽取或写入知识图谱。需跨进程共享索引时，请按部署配置启用共享存储并启动 worker。</p>
    </section>
  `,
  data() {
    return {
      tasks: [],
      totalTasks: 0,
      selectedFiles: [],
      uploadMessage: "",
      uploadError: false,
      loading: false,
      uploading: false,
      loadError: "",
      dragActive: false,
      expandedTask: "",
      _timer: null,
      _visibilityHandler: null,
    };
  },
  computed: {
    validFiles() {
      return this.selectedFiles.filter((file) => !this.fileIssue(file));
    },
    activeCount() {
      return this.tasks.filter((task) => ACTIVE_TASKS.has(task.status)).length;
    },
    visibleDocumentCount() {
      return this.tasks.reduce((sum, task) => sum + (task.docs || []).length, 0);
    },
  },
  activated() { this.startPolling(); },
  deactivated() { this.stopPolling(); },
  beforeUnmount() { this.stopPolling(); },
  methods: {
    startPolling() {
      if (this._timer) return;
      this.loadTasks();
      this._timer = window.setInterval(() => {
        if (!document.hidden && this.tasks.some((task) => ACTIVE_TASKS.has(task.status))) this.loadTasks(true);
      }, POLL_INTERVAL_MS);
      this._visibilityHandler = () => {
        if (!document.hidden) this.loadTasks(true);
      };
      document.addEventListener("visibilitychange", this._visibilityHandler);
    },
    stopPolling() {
      if (this._timer) window.clearInterval(this._timer);
      this._timer = null;
      if (this._visibilityHandler) document.removeEventListener("visibilitychange", this._visibilityHandler);
      this._visibilityHandler = null;
    },
    async loadTasks(silent = false, offset = 0) {
      if (!silent) this.loading = true;
      this.loadError = "";
      try {
        const result = await listIngestTasks({ limit: 50, offset });
        this.totalTasks = Number(result.meta.total || 0);
        this.tasks = offset ? [...this.tasks, ...result.data] : result.data;
      } catch (error) {
        this.loadError = friendlyError(error);
      } finally {
        if (!silent) this.loading = false;
      }
    },
    loadMore() { this.loadTasks(false, this.tasks.length); },
    selectFiles(event) { this.selectedFiles = Array.from(event.target.files || []); this.uploadMessage = ""; },
    onDrop(event) {
      this.dragActive = false;
      this.selectedFiles = Array.from(event.dataTransfer.files || []);
      this.uploadMessage = "";
    },
    fileIssue(file) {
      const extension = String(file.name || "").split(".").pop().toLowerCase();
      if (!["md", "txt"].includes(extension)) return extension === "pdf" ? "暂不支持 PDF" : "格式不支持";
      if (file.size > MAX_FILE_BYTES) return "超过 5 MB";
      return "";
    },
    clearSelection() {
      this.selectedFiles = [];
      const input = document.getElementById("documentFiles");
      if (input) input.value = "";
      this.uploadMessage = "";
    },
    async uploadSelected() {
      if (this.uploading || !this.validFiles.length) return;
      const overflow = Math.max(0, this.validFiles.length - MAX_BATCH_FILES);
      const files = this.validFiles.slice(0, MAX_BATCH_FILES);
      this.uploading = true;
      this.uploadMessage = "";
      try {
        const result = await uploadDocuments(files);
        const accepted = (result.data.docs || []).filter((doc) => !doc.error).length;
        this.uploadMessage = `${accepted}/${files.length} 个文件已接收，任务 ${result.data.id}。` +
          (overflow ? ` 另有 ${overflow} 个文件超出单批上限，请分批上传。` : "");
        this.uploadError = accepted !== files.length;
        await this.loadTasks(true);
        if (overflow) {
          const remaining = this.validFiles.slice(MAX_BATCH_FILES);
          const rejected = this.selectedFiles.filter((file) => this.fileIssue(file));
          this.selectedFiles = [...remaining, ...rejected];
          const input = document.getElementById("documentFiles");
          if (input) input.value = "";
        } else {
          this.clearSelection();
        }
        this.$emit("notify", this.uploadMessage, this.uploadError ? "warning" : "success");
      } catch (error) {
        this.uploadError = true;
        this.uploadMessage = `上传失败：${friendlyError(error)}`;
      } finally {
        this.uploading = false;
      }
    },
    toggleTask(id) { this.expandedTask = this.expandedTask === id ? "" : id; },
    taskStatusLabel(status) {
      return ({ queued: "等待处理", extracting: "正在处理", review: "等待审核", done: "已完成", failed: "处理失败", empty: "无文件" })[status] || status || "未知";
    },
    taskStatusClass(status) { return ACTIVE_TASKS.has(status) ? "status-pending" : `status-${status || "unknown"}`; },
    formatDate(value) { return value ? new Date(Number(value) * 1000).toLocaleString() : "时间未知"; },
    formatBytes(value) {
      const size = Number(value) || 0;
      return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
    },
  },
};
