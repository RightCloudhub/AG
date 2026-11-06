// @ts-check
import { ref, watch, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { request } from '../transport.js';
import { record, text, number, rows } from '../responses.js';
import { requestGeneration, isCurrentRequest } from '../request_scope.js';
import { text as T, fmt } from '../i18n.js';

const PAGE_CHARS = 4096;
/** @param {unknown} value */
export function parseOriginal(value) {
  const row = record(value);
  return { content: text(row.content), status: text(row.status), offset: number(row.offset), total: number(row.total_chars),
    sources: rows(row.source_refs, raw => {
      const source = record(raw);
      return { doc: text(source.doc_id), version: number(source.doc_version), chunk: text(source.chunk_id) };
    }) };
}
/** @param {string} evidence @param {string} kb @param {number} [offset] */
export async function loadOriginal(evidence, kb, offset = 0) {
  const params = new URLSearchParams({ kb_id: kb, offset: String(offset), limit: String(PAGE_CHARS) });
  return parseOriginal((await request(`/v1/evidence/${encodeURIComponent(evidence)}?${params}`)).data);
}

export default {
  name: 'EvidenceSource',
  props: { evidenceId: { type: String, required: true }, kbId: { type: String, default: 'default' } },
  /** @param {{evidenceId: string, kbId: string}} props */
  setup(props) {
    const original = ref(/** @type {ReturnType<typeof parseOriginal>|null} */ (null));
    const busy = ref(false), error = ref('');
    let sequence = 0;
    /** @param {number} [offset] */
    async function load(offset = 0) {
      const ticket = ++sequence, generation = requestGeneration();
      busy.value = true; error.value = '';
      try {
        const value = await loadOriginal(props.evidenceId, props.kbId, offset);
        if (ticket === sequence && isCurrentRequest(generation)) original.value = value;
      } catch (failure) {
        if (ticket === sequence && isCurrentRequest(generation) && !(failure instanceof Error && failure.name === 'AbortError')) error.value = failure instanceof Error ? failure.message : String(failure);
      } finally { if (ticket === sequence) busy.value = false; }
    }
    function nextOffset() { return original.value ? original.value.offset + Array.from(original.value.content).length : 0; }
    watch(() => `${props.kbId}:${props.evidenceId}`, () => { original.value = null; void load(); }, { immediate: true });
    onUnmounted(() => { sequence += 1; });
    return { original, busy, error, load, nextOffset, PAGE_CHARS, T, fmt };
  },
  template: `<section class="evidence-original" :aria-label="T.inspector.original" :aria-busy="busy">
    <h4>{{ T.inspector.original }}</h4>
    <p v-if="busy" role="status">{{ T.inspector.originalLoading }}</p>
    <template v-if="original">
      <p v-if="original.status !== 'available'" role="status">{{ original.status === 'deleted' ? T.inspector.originalDeleted : T.inspector.originalUnavailable }}</p>
      <template v-else>
        <p class="evidence-original__text">{{ original.content }}</p>
        <p v-for="source in original.sources" :key="source.doc + source.chunk" class="evidence-original__source">{{ fmt(T.inspector.originalVersion, source) }}</p>
        <div v-if="original.total > PAGE_CHARS" class="evidence-original__pages">
          <button class="link-btn" type="button" :disabled="busy || !original.offset" @click="load(Math.max(0, original.offset - PAGE_CHARS))">{{ T.inspector.originalPrevious }}</button>
          <span>{{ original.offset + 1 }}–{{ nextOffset() }} / {{ original.total }}</span>
          <button class="link-btn" type="button" :disabled="busy || nextOffset() >= original.total" @click="load(nextOffset())">{{ T.inspector.originalNext }}</button>
        </div>
      </template>
    </template>
    <p v-if="error" class="row-error" role="alert">{{ error }} <button class="link-btn" type="button" @click="load()" :disabled="busy">{{ T.inspector.originalRetry }}</button></p>
  </section>`,
};
