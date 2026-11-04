// @ts-check
/** The selected knowledge base is shared by every business request. */
import { ref } from '../../vendor/vue.esm-browser.prod.js';

export const DEFAULT_KNOWLEDGE_BASE = 'default';
export const activeKnowledgeId = ref(DEFAULT_KNOWLEDGE_BASE);
export const knowledgeRevision = ref(0);

/** Keep omitted scope compatible with the existing default-only API contract. @param {string} path */
export function knowledgePath(path) {
  if (activeKnowledgeId.value === DEFAULT_KNOWLEDGE_BASE) return path;
  const [base, query = ''] = path.split('?');
  const params = new URLSearchParams(query);
  params.set('kb_id', activeKnowledgeId.value);
  return `${base}?${params}`;
}

/** @param {import('../contracts.js').QueryRequest} body */
export function knowledgeQuery(body) {
  return body.kb_id || activeKnowledgeId.value === DEFAULT_KNOWLEDGE_BASE
    ? body : { ...body, kb_id: activeKnowledgeId.value };
}
