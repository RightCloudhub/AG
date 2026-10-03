/**
 * Knowledge view — document upload + ingest task list.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { uploadDocs, fetchIngestTasks } from '../api.js';

export default {
  name: 'KnowledgeView',
  setup() {
    const tasks = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const uploading = ref(false);
    const dragActive = ref(false);
    const page = ref(1);
    const limit = 20;

    async function loadTasks() {
      loading.value = true;
      try {
        const { data, meta } = await fetchIngestTasks({
          limit, offset: (page.value - 1) * limit,
        });
        tasks.value = data || [];
        total.value = meta?.total ?? 0;
      } catch { tasks.value = []; }
      finally { loading.value = false; }
    }

    async function handleFiles(fileList) {
      if (!fileList?.length) return;
      uploading.value = true;
      try {
        await uploadDocs(Array.from(fileList));
        await loadTasks();
      } catch (e) {
        alert('Upload failed: ' + e.message);
      } finally {
        uploading.value = false;
      }
    }

    function onDrop(e) {
      e.preventDefault();
      dragActive.value = false;
      handleFiles(e.dataTransfer.files);
    }

    function onDragOver(e) { e.preventDefault(); dragActive.value = true; }
    function onDragLeave() { dragActive.value = false; }

    function triggerUpload() {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = '.md,.txt';
      input.onchange = () => handleFiles(input.files);
      input.click();
    }

    function statusBadge(status) {
      const map = {
        pending: 'badge--warning', running: 'badge--info',
        completed: 'badge--success', failed: 'badge--danger',
      };
      return map[status] || 'badge--muted';
    }

    function totalPages() { return Math.max(1, Math.ceil(total.value / limit)); }
    function prevPage() { if (page.value > 1) { page.value--; loadTasks(); } }
    function nextPage() { if (page.value < totalPages()) { page.value++; loadTasks(); } }

    onMounted(loadTasks);

    return { tasks, total, loading, uploading, dragActive, page, limit,
             onDrop, onDragOver, onDragLeave, triggerUpload, statusBadge,
             totalPages, prevPage, nextPage, loadTasks };
  },

  template: `
<div>
  <div class="view-header">
    <div class="view-header__title">📚 知识库构建</div>
    <div class="view-header__actions">
      <button class="btn btn--ghost btn--sm" @click="loadTasks" :disabled="loading">↻ 刷新</button>
    </div>
  </div>
  <div class="view-body">
    <!-- Upload zone -->
    <div class="upload-zone" :class="{ 'upload-zone--active': dragActive }"
         @drop="onDrop" @dragover="onDragOver" @dragleave="onDragLeave" @click="triggerUpload">
      <div class="upload-zone__icon">{{ uploading ? '⏳' : '📄' }}</div>
      <div class="upload-zone__text" v-if="!uploading">
        拖拽 Markdown 或文本文档到此处，或点击浏览文件<br>
        <span style="color:var(--color-text-muted);font-size:var(--text-xs);">UTF-8 · .md / .txt · 每文件最大 5 MiB · 单次最多 20 个文件</span>
      </div>
      <div class="upload-zone__text" v-else>上传中…</div>
    </div>

    <!-- Tasks table -->
    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>

    <div v-else-if="tasks.length === 0" class="empty-state">
      <div class="empty-state__icon">📋</div>
      <div class="empty-state__text">暂无处理任务</div>
    </div>

    <div v-else class="data-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>任务 ID</th>
            <th>状态</th>
            <th>文档数量</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.task_id || t.id">
            <td style="font-family:var(--font-mono);font-size:var(--text-xs);">
              {{ (t.task_id || t.id || '').slice(0, 12) }}
            </td>
            <td><span class="badge" :class="statusBadge(t.status)">{{ t.status || 'unknown' }}</span></td>
            <td>{{ t.doc_count ?? t.documents?.length ?? '—' }}</td>
            <td style="font-size:var(--text-xs);color:var(--color-text-muted);">
              {{ t.created_at ? new Date(t.created_at).toLocaleString() : '—' }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="table-footer">
        <span>共 {{ total }} 个任务</span>
        <div class="pagination">
          <button class="pagination__btn" :disabled="page <= 1" @click="prevPage">←</button>
          <span class="pagination__info">{{ page }} / {{ totalPages() }}</span>
          <button class="pagination__btn" :disabled="page >= totalPages()" @click="nextPage">→</button>
        </div>
      </div>
    </div>
  </div>
</div>
`
};
