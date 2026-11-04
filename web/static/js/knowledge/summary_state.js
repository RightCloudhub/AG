// @ts-check
import { ref, computed, watch, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { activeKnowledge, knowledgeRole } from '../store/knowledge.js';
import { requestGeneration, isCurrentRequest } from '../request_scope.js';
import { ApiError } from '../http.js';
import { knowledgeText as K } from '../i18n.js';
import { loadSummary, loadFailedJobs, buildSummary, retrySummary } from './summary_api.js';

const POLL_MS = 5000;
class SummaryRequests {
  state = {
    status: ref(/** @type {import('./summary_api.js').SummaryStatus|null} */ (null)),
    jobs: ref(/** @type {import('./summary_api.js').SummaryJob[]} */ ([])),
    loading: ref(false), busy: ref(false), error: ref(''),
    canManage: computed(() => ['admin', 'operator'].includes(knowledgeRole.value)),
  };
  sequence = 0;
  context = 0;
  active = true;
  /** @type {ReturnType<typeof setTimeout>|undefined} */
  timer;

  reset() {
    this.sequence += 1; this.context += 1; clearTimeout(this.timer);
    const state = this.state;
    state.status.value = null; state.jobs.value = []; state.error.value = '';
    state.busy.value = false; state.loading.value = false;
  }
  async reload() {
    const id = activeKnowledge.value?.kb_id, state = this.state;
    if (!id || !this.active) return;
    const ticket = ++this.sequence, generation = requestGeneration();
    clearTimeout(this.timer); state.loading.value = true;
    try {
      const next = await loadSummary(id);
      const failed = state.canManage.value && next.failed ? await loadFailedJobs(id) : [];
      if (ticket !== this.sequence || !isCurrentRequest(generation)) return;
      state.status.value = next; state.jobs.value = failed; state.error.value = '';
      if (next.pending || next.running) this.timer = setTimeout(() => void this.reload(), POLL_MS);
    } catch (failure) {
      if (ticket === this.sequence && isCurrentRequest(generation)) this.showError(failure);
    } finally { if (ticket === this.sequence) state.loading.value = false; }
  }
  /** @param {unknown} failure */
  showError(failure) {
    if (failure instanceof Error && failure.name === 'AbortError') return;
    this.state.error.value = failure instanceof ApiError && failure.code === 'CAPABILITY_UNAVAILABLE'
      ? K.summaryNeedModels : failure instanceof Error ? failure.message : String(failure);
  }
  /** @param {string} job */
  async run(job) {
    const id = activeKnowledge.value?.kb_id, state = this.state;
    if (!id || !this.active || state.busy.value || !state.canManage.value) return;
    const context = this.context, generation = requestGeneration();
    const current = () => this.active && context === this.context && isCurrentRequest(generation);
    state.busy.value = true; state.error.value = '';
    try {
      if (job) await retrySummary(id, job); else await buildSummary(id);
      if (current()) await this.reload();
    } catch (failure) { if (current()) this.showError(failure); }
    finally { if (current()) state.busy.value = false; }
  }
}

export function summaryState() {
  const requests = new SummaryRequests();
  watch(() => `${activeKnowledge.value?.kb_id}:${activeKnowledge.value?.config_revision}:${knowledgeRole.value}`, () => {
    requests.reset(); void requests.reload();
  }, { immediate: true });
  onUnmounted(() => { requests.active = false; requests.reset(); });
  return { ...requests.state, reload: () => requests.reload(), run: (job = '') => requests.run(job) };
}
