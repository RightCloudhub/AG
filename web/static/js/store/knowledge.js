// @ts-check
/** Selection resets business view state before another scope can issue requests. */
import { ref, computed } from '../../vendor/vue.esm-browser.prod.js';
import { cancelRequests } from '../request_scope.js';
import { listKnowledgeBases } from '../knowledge/config_api.js';
import { activeKnowledgeId, DEFAULT_KNOWLEDGE_BASE, knowledgeRevision } from '../knowledge/scope.js';
import { selectHistoryKnowledge } from './conversations.js';
import { closeOverlays } from './shell.js';
import { setGraphFocus } from './graph_focus.js';
import { inspSyncTo } from '../shell/use_inspector.js';

export { activeKnowledgeId, knowledgeRevision } from '../knowledge/scope.js';
export const knowledgeBases = ref(/** @type {import('../knowledge/config_types.js').KnowledgeBase[]} */ ([]));
export const knowledgeLoading = ref(false);
export const knowledgeError = ref('');
export const knowledgeRole = ref('reader');
export const activeKnowledge = computed(() => knowledgeBases.value.find(kb => kb.kb_id === activeKnowledgeId.value) ?? null);
let loadSequence = 0;

/** @param {string} id */
function changeScope(id) {
  cancelRequests();
  activeKnowledgeId.value = id;
  selectHistoryKnowledge(id);
  inspSyncTo(null);
  setGraphFocus('');
  closeOverlays();
  knowledgeRevision.value += 1;
}

/** Accept only a base returned for the authenticated principal. @param {string} id */
export function selectKnowledgeBase(id) {
  if (id === activeKnowledgeId.value || !knowledgeBases.value.some(kb => kb.kb_id === id)) return;
  changeScope(id);
}

/** Called before an identity change, including replacing a core API key. */
export function resetKnowledgeScope() {
  loadSequence += 1;
  knowledgeBases.value = []; knowledgeError.value = ''; knowledgeLoading.value = false;
  knowledgeRole.value = 'reader';
  changeScope(DEFAULT_KNOWLEDGE_BASE);
}

export async function refreshKnowledgeBases() {
  const sequence = ++loadSequence;
  knowledgeLoading.value = true; knowledgeError.value = '';
  try {
    const found = await listKnowledgeBases();
    if (sequence !== loadSequence) return;
    knowledgeBases.value = found;
    if (!found.some(kb => kb.kb_id === activeKnowledgeId.value)) changeScope(DEFAULT_KNOWLEDGE_BASE);
  } catch (error) {
    if (sequence === loadSequence && !(error instanceof Error && error.name === 'AbortError')) {
      knowledgeError.value = error instanceof Error ? error.message : String(error);
    }
  } finally { if (sequence === loadSequence) knowledgeLoading.value = false; }
}

/** Apply only the server's saved revision. @param {import('../knowledge/config_types.js').KnowledgeBase} saved */
export function acceptKnowledgeBase(saved) {
  knowledgeBases.value = [...knowledgeBases.value.filter(kb => kb.kb_id !== saved.kb_id), saved]
    .sort((a, b) => a.kb_id.localeCompare(b.kb_id));
}
