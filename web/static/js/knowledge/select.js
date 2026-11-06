// @ts-check
import { knowledgeText as K, text as T } from '../i18n.js';
import { activeKnowledgeId, knowledgeBases, knowledgeLoading, knowledgeError,
  selectKnowledgeBase, refreshKnowledgeBases } from '../store/knowledge.js';

export default {
  name: 'KnowledgeSelect',
  setup() { return { K, T, activeKnowledgeId, knowledgeBases, knowledgeLoading, knowledgeError,
    selectKnowledgeBase, refreshKnowledgeBases }; },
  template: `<div class="knowledge-picker">
    <label class="sr-only" for="active-knowledge">{{ K.scope }}</label>
    <select id="active-knowledge" class="select select--quiet" :aria-label="K.scope" :value="activeKnowledgeId"
        :disabled="knowledgeLoading || !knowledgeBases.length" @change="selectKnowledgeBase($event.target.value)">
      <option v-if="!knowledgeBases.length" value="default">{{ knowledgeLoading ? T.common.loading : K.defaultName }}</option>
      <option v-for="kb in knowledgeBases" :key="kb.kb_id" :value="kb.kb_id">{{ kb.name }}</option>
    </select>
    <button v-if="knowledgeError" class="btn btn--icon btn--ghost" type="button" @click="refreshKnowledgeBases"
      :title="knowledgeError" :aria-label="K.loadFailed + ' · ' + T.states.retry"><ag-icon name="refresh" :size="14"></ag-icon></button>
    <span v-if="knowledgeError" class="sr-only" role="alert">{{ K.loadFailed }}</span>
  </div>`,
};
