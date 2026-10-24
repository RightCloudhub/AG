// @ts-check
/**
 * Knowledge view — document upload + ingest task list.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { uploadDocs, fetchIngestTasks } from '../api.js';
import { IngestStatus, UiPolicy, UploadPolicy } from '../contracts.js';

function useKnowledgeState() {
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').Task[]>} */
  const tasks = ref([]);
  const total = ref(0);
  const loading = ref(false);
  const uploading = ref(false);
  const dragActive = ref(false);
  const page = ref(1);
  const limit = UiPolicy.TABLE_PAGE_SIZE;
  function onDragLeave() { dragActive.value = false; }

  return { tasks, total, loading, uploading, dragActive, page, limit, onDragLeave };
}

/** @param {import('../contracts.js').IngestStatusName} status */
function statusBadge(status) {
  const map = {
    [IngestStatus.QUEUED]: 'badge--warning',
    [IngestStatus.EXTRACTING]: 'badge--info',
    [IngestStatus.REVIEW]: 'badge--warning',
    [IngestStatus.DONE]: 'badge--success',
    [IngestStatus.FAILED]: 'badge--danger',
    [IngestStatus.EMPTY]: 'badge--muted',
  };
  return map[status] || 'badge--muted';
}

export default {
  name: 'KnowledgeView',
  setup() {
    const { tasks, total, loading, uploading, dragActive, page, limit, onDragLeave } = useKnowledgeState();

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

    /** @param {FileList | null} fileList */
    async function handleFiles(fileList) {
      if (!fileList?.length) return;
      uploading.value = true;
      try {
        await uploadDocs(Array.from(fileList));
        await loadTasks();
      } catch (e) {
        alert('上传失败：' + (e instanceof Error ? e.message : String(e)));
      } finally {
        uploading.value = false;
      }
    }

    /** @param {DragEvent} e */
    function onDrop(e) {
      e.preventDefault();
      dragActive.value = false;
      handleFiles(e.dataTransfer?.files ?? null);
    }

    /** @param {DragEvent} e */
    function onDragOver(e) { e.preventDefault(); dragActive.value = true; }

    function triggerUpload() {
      const input = document.createElement('input');
      input.type = 'file';
      input.multiple = true;
      input.accept = UploadPolicy.ACCEPT;
      input.onchange = () => handleFiles(input.files);
      input.click();
    }

    function totalPages() { return Math.max(1, Math.ceil(total.value / limit)); }
    function prevPage() { if (page.value > 1) { page.value--; loadTasks(); } }
    function nextPage() { if (page.value < totalPages()) { page.value++; loadTasks(); } }

    onMounted(loadTasks);

    return { tasks, total, loading, uploading, dragActive, page, limit,
             onDrop, onDragOver, onDragLeave, triggerUpload, statusBadge,
             totalPages, prevPage, nextPage, loadTasks, UiPolicy };
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
              {{ (t.task_id || t.id || '').slice(0, UiPolicy.ID_PREVIEW) }}
            </td>
            <td><span class="badge" :class="statusBadge(t.status)">{{ t.status || 'unknown' }}</span></td>
            <td>{{ t.docs.length ?? '—' }}</td>
            <td style="font-size:var(--text-xs);color:var(--color-text-muted);">
              {{ t.created_at ? new Date(t.created_at * UiPolicy.MILLISECONDS_PER_SECOND).toLocaleString() : '—' }}
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
