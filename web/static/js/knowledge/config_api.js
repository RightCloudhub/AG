// @ts-check
import { request } from '../transport.js';
import { rows } from '../responses.js';
import { parseKnowledgeBase } from './config_types.js';

const endpoint = '/v1/knowledge-bases';

export async function listKnowledgeBases() {
  /** @type {import('./config_types.js').KnowledgeBase[]} */
  const found = [];
  let page = 1;
  while (true) {
    const result = await request(`${endpoint}?page=${page}&limit=100`);
    const items = rows(result.data, parseKnowledgeBase);
    found.push(...items);
    if (!items.length || found.length >= result.meta.total) return found;
    page += 1;
  }
}

/** @param {import('./config_types.js').KnowledgeBase} form @param {boolean} creating */
export async function saveKnowledgeBase(form, creating) {
  const options = {
    name: form.name.trim(), model_profile_id: form.model_profile_id || null,
    model_profile_revision: form.model_profile_id ? form.model_profile_revision : null,
    global_summary_enabled: form.global_summary_enabled, review_policy: form.review_policy,
  };
  const body = creating ? { ...options, kb_id: form.kb_id.trim(), build_mode: form.build_mode,
    schema_id: form.build_mode === 'schema' ? form.schema_id || null : null,
    schema_version: form.build_mode === 'schema' ? form.schema_version || null : null,
  } : { ...options, expected_revision: form.config_revision };
  const path = creating ? endpoint : `${endpoint}/${encodeURIComponent(form.kb_id)}`;
  return parseKnowledgeBase((await request(path, {
    method: creating ? 'POST' : 'PATCH', body: JSON.stringify(body),
  })).data);
}
