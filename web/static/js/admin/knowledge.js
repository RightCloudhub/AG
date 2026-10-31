// @ts-check
/**
 * Knowledge view — four-step import wizard (select → preview → confirm → progress)
 * plus the ingest task table. Extraction preview is gated by FeatureFlag.EXTRACTION_PREVIEW.
 */
import { ref, computed, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { uploadDocs, fetchIngestTasks, fetchIngestTask } from '../api.js';
import { parseTask } from '../responses.js';
import { FeatureFlag, IngestStatus, IngestStep, KeyboardKey, UiPolicy, UploadPolicy, View } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { openNotebook } from '../store/shell.js';
import { formatBytes, isActiveStatus, stageFiles, statusBadge, statusHint } from '../knowledge/ingest_wizard.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../knowledge/ingest_wizard.js').StagedFile} StagedFile */
/** @typedef {import('../responses.js').Task} Task */

export default {
  name: 'KnowledgeView',
  setup() {
    /** @type {Ref<number>} */
    const step = ref(IngestStep.SELECT);
    /** @type {Ref<StagedFile[]>} */
    const staged = ref([]);
    const dragActive = ref(false);
    const uploading = ref(false);
    const uploadError = ref('');
    /** @type {Ref<Task | null>} */
    const task = ref(null);
    /** @type {Ref<HTMLInputElement | null>} */
    const fileInput = ref(null);
    /** @type {Ref<Task[]>} */
    const tasks = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const page = ref(1);
    const limit = UiPolicy.TABLE_PAGE_SIZE;
    /** @type {ReturnType<typeof setInterval> | undefined} */
    let pollTimer;
    const validCount = computed(() => staged.value.filter(f => f.ok).length);
    const canConfirm = computed(() => validCount.value > 0);
    const validFiles = computed(() => staged.value.filter(f => f.ok));

    /** @param {FileList | File[] | null | undefined} list */
    async function addFiles(list) {
      if (!list?.length) return;
      staged.value = await stageFiles(Array.from(list), staged.value);
    }
    /** @param {DragEvent} e */
    function onDrop(e) { e.preventDefault(); dragActive.value = false; addFiles(e.dataTransfer?.files); }
    /** @param {DragEvent} e */
    function onDragOver(e) { e.preventDefault(); dragActive.value = true; }
    function onDragLeave() { dragActive.value = false; }
    function triggerPick() { fileInput.value?.click(); }
    /** @param {FileList | null} files */
    function onPick(files) { addFiles(files); if (fileInput.value) fileInput.value.value = ''; }
    /** @param {number} index */
    function removeFile(index) { staged.value = staged.value.filter((_, i) => i !== index); }
    function next() {
      if (step.value === IngestStep.SELECT && !canConfirm.value) return;
      if (step.value === IngestStep.CONFIRM) { confirmUpload(); return; }
      if (step.value < IngestStep.CONFIRM) step.value += 1;
    }
    function back() { if (step.value > IngestStep.SELECT && step.value < IngestStep.PROGRESS) step.value -= 1; }
    function skip() { if (step.value === IngestStep.PREVIEW) step.value = IngestStep.CONFIRM; }
    /** @param {KeyboardEvent} e */
    function onKeydown(e) {
      if (e.key === KeyboardKey.ESCAPE) { skip(); return; }
      const typing = e.target instanceof HTMLButtonElement || e.target instanceof HTMLInputElement;
      if (e.key === KeyboardKey.ENTER && !typing) next();
    }
    function reset() { stopPolling(); staged.value = []; task.value = null; uploadError.value = ''; step.value = IngestStep.SELECT; }
    async function confirmUpload() {
      uploading.value = true;
      uploadError.value = '';
      try {
        const { data } = await uploadDocs(validFiles.value.map(f => f.file));
        task.value = parseTask(data);
        step.value = IngestStep.PROGRESS;
        startPolling();
        loadTasks();
      } catch (e) {
        uploadError.value = fmt(T.knowledge.uploadFailed, { msg: e instanceof Error ? e.message : String(e) });
      } finally { uploading.value = false; }
    }
    function startPolling() { stopPolling(); pollTimer = setInterval(refreshTask, UiPolicy.POLL_INTERVAL_MS); }
    function stopPolling() { clearInterval(pollTimer); pollTimer = undefined; }
    async function refreshTask() {
      const current = task.value;
      if (!current || document.visibilityState !== 'visible') return;
      if (!isActiveStatus(current.status)) { stopPolling(); return; }
      try { task.value = parseTask((await fetchIngestTask(current.id || current.task_id)).data); }
      catch { /* keep the last known state; next tick retries */ }
    }
    function gotoReview() { window.location.hash = View.REVIEW; }
    async function loadTasks() {
      loading.value = true;
      try {
        const { data, meta } = await fetchIngestTasks({ limit, offset: (page.value - 1) * limit });
        tasks.value = data;
        total.value = meta.total;
      } catch { tasks.value = []; }
      finally { loading.value = false; }
    }
    function totalPages() { return Math.max(1, Math.ceil(total.value / limit)); }
    function prevPage() { if (page.value > 1) { page.value -= 1; loadTasks(); } }
    function nextPage() { if (page.value < totalPages()) { page.value += 1; loadTasks(); } }
    /** @param {number} epochSeconds */
    function fmtCreated(epochSeconds) {
      return epochSeconds ? new Date(epochSeconds * UiPolicy.MILLISECONDS_PER_SECOND).toLocaleString('zh-CN') : '—';
    }

    onMounted(loadTasks);
    onUnmounted(stopPolling);
    return {
      step, staged, dragActive, uploading, uploadError, task, fileInput, tasks, total, loading, page, validCount,
      canConfirm, onDrop, onDragOver, onDragLeave, triggerPick, onPick, removeFile, next, back, skip, onKeydown,
      reset, confirmUpload, gotoReview, loadTasks, totalPages, prevPage, nextPage, fmtCreated, formatBytes, statusBadge,
      statusHint, isActiveStatus, openNotebook, T, fmt, IngestStep, IngestStatus, FeatureFlag, UploadPolicy, UiPolicy,
    };
  },

  template: `
<div class="view">
  <div class="view-header">
    <button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="T.notebook.open"><ag-icon name="menu"></ag-icon></button>
    <h1 class="view-header__title">{{ T.knowledge.title }}</h1>
    <div class="view-header__actions">
      <button type="button" class="btn btn--secondary btn--sm" @click="loadTasks" :disabled="loading"><ag-icon name="refresh" :size="14"></ag-icon>{{ T.knowledge.refresh }}</button>
    </div>
  </div>
  <div class="view-body">
    <section class="import-wizard" :aria-label="T.knowledge.wizardTitle" @keydown="onKeydown">
      <div class="import-wizard__head"><h2 class="dialog__title">{{ T.knowledge.wizardTitle }}</h2></div>
      <ol class="wizard__steps">
        <li v-for="(label, i) in T.knowledge.steps" :key="label" class="wizard__step"
            :class="{ 'wizard__step--active': step === i, 'wizard__step--done': step > i }"
            :aria-current="step === i ? 'step' : null"><ag-icon v-if="step > i" name="check" :size="14"></ag-icon>{{ label }}</li>
      </ol>
      <div class="import-wizard__body">
        <div v-if="step === IngestStep.SELECT">
          <div class="upload-zone" :class="{ 'upload-zone--active': dragActive }" @drop="onDrop" @dragover="onDragOver" @dragleave="onDragLeave">
            <span class="upload-zone__icon"><ag-icon name="upload" :size="20"></ag-icon></span>
            <div class="upload-zone__text">{{ T.knowledge.dropHint }}<br><span class="hint">{{ T.knowledge.dropRule }}</span></div>
            <button type="button" class="btn btn--secondary btn--sm" @click="triggerPick">{{ T.knowledge.browse }}</button>
            <input ref="fileInput" class="sr-only" type="file" multiple :accept="UploadPolicy.ACCEPT" @change="onPick($event.target.files)" tabindex="-1" aria-hidden="true">
          </div>
          <ul v-if="staged.length" class="staged" :aria-label="fmt(T.knowledge.selected, { n: staged.length })">
            <li v-for="(f, i) in staged" :key="f.name + f.bytes" class="staged__item" :class="{ 'staged__item--bad': !f.ok }">
              <span class="staged__name">{{ f.name }}</span><span class="staged__meta">{{ formatBytes(f.bytes) }}</span>
              <span v-if="!f.ok" class="tag tag--err">{{ f.reason }}</span>
              <button type="button" class="btn btn--icon btn--ghost btn--sm" @click="removeFile(i)" :aria-label="T.common.remove"><ag-icon name="close" :size="14"></ag-icon></button>
            </li>
          </ul>
        </div>
        <div v-else-if="step === IngestStep.PREVIEW" class="field-stack">
          <p class="notice" role="status" v-if="!FeatureFlag.EXTRACTION_PREVIEW"><ag-icon name="info" :size="14"></ag-icon><span>{{ T.knowledge.extractionUnavailable }}</span></p>
          <p class="field__label">{{ T.knowledge.previewTitle }}</p>
          <div><article v-for="f in staged.filter(x => x.ok)" :key="f.name" class="preview-doc">
            <div class="preview-doc__name">{{ f.name }} <span class="hint">{{ formatBytes(f.bytes) }}</span></div>
            <pre class="preview-doc__text">{{ f.preview }}</pre>
          </article></div>
        </div>
        <div v-else-if="step === IngestStep.CONFIRM" class="field-stack">
          <p class="field__label">{{ T.knowledge.confirmTitle }}</p>
          <p>{{ fmt(T.knowledge.selected, { n: validCount }) }} · {{ T.knowledge.confirmHint }}</p>
          <p v-if="uploadError" class="row-error" role="alert"><ag-icon name="warning" :size="14"></ag-icon><span>{{ uploadError }}</span></p>
        </div>
        <div v-else-if="task" class="field-stack task-progress" aria-live="polite">
          <p class="field__label">{{ T.knowledge.progressTitle }}</p>
          <div class="task-progress__row"><span>{{ T.knowledge.taskId }}</span><code>{{ task.id || task.task_id }}</code></div>
          <div class="task-progress__row"><span>{{ T.knowledge.status }}</span>
            <span class="tag" :class="statusBadge(task.status)">{{ task.status }}</span>
            <span class="hint">{{ statusHint(task.status) }}</span><span v-if="isActiveStatus(task.status)" class="spinner"></span></div>
          <div v-if="task.message" class="task-progress__row"><span>{{ T.knowledge.message }}</span><span>{{ task.message }}</span></div>
          <ul class="staged">
            <li v-for="d in task.docs" :key="d.doc_id" class="staged__item" :class="{ 'staged__item--bad': d.error }">
              <span class="staged__name">{{ d.name || d.doc_id }}</span><span class="staged__meta">{{ d.bytes }} B</span>
              <span v-if="d.error" class="tag tag--err">{{ d.error }}</span></li>
          </ul>
          <div class="wizard__actions">
            <button v-if="task.status === IngestStatus.REVIEW" type="button" class="btn btn--primary" @click="gotoReview">{{ T.knowledge.gotoReview }}</button>
            <button type="button" class="btn btn--secondary" @click="reset">{{ T.knowledge.newImport }}</button>
          </div>
        </div>
      </div>
      <footer v-if="step < IngestStep.PROGRESS" class="dialog__foot">
        <span class="hint">{{ T.wizard.keyboard }}</span>
        <div class="wizard__actions">
          <button type="button" class="btn btn--secondary" @click="back" :disabled="step === IngestStep.SELECT">{{ T.common.back }}</button>
          <button v-if="step === IngestStep.PREVIEW" type="button" class="btn btn--ghost" @click="skip">{{ T.common.skip }}</button>
          <button type="button" class="btn btn--primary" @click="next" :disabled="!canConfirm || uploading">
            {{ step === IngestStep.CONFIRM ? (uploading ? T.knowledge.uploading : T.common.confirm) : T.common.next }}</button>
        </div>
      </footer>
    </section>

    <h2 class="section-title">{{ T.knowledge.tasksTitle }}</h2>
    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>
    <div v-else-if="tasks.length === 0" class="empty-state empty-state--slot"><div class="empty-state__text">{{ T.knowledge.noTasks }}</div></div>
    <div v-else class="data-table-wrap">
      <table class="data-table">
        <thead><tr><th>{{ T.knowledge.taskId }}</th><th>{{ T.knowledge.status }}</th><th class="num">{{ T.knowledge.docs }}</th><th>{{ T.knowledge.created }}</th></tr></thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.task_id || t.id">
            <td class="mono">{{ (t.task_id || t.id).slice(0, UiPolicy.ID_PREVIEW) }}</td>
            <td><span class="tag" :class="statusBadge(t.status)">{{ t.status }}</span></td>
            <td class="num">{{ t.docs.length }}</td>
            <td>{{ fmtCreated(t.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="table-footer"><span>{{ fmt(T.knowledge.total, { n: total }) }}</span>
        <div class="pagination"><button type="button" class="btn btn--xs btn--secondary" :disabled="page <= 1" @click="prevPage">{{ T.knowledge.prev }}</button>
          <span class="pagination__info">{{ fmt(T.knowledge.page, { page, pages: totalPages() }) }}</span>
          <button type="button" class="btn btn--xs btn--secondary" :disabled="page >= totalPages()" @click="nextPage">{{ T.knowledge.next }}</button></div></div>
    </div>
  </div>
</div>`,
};
