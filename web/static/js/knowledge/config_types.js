// @ts-check
import { record, text, number } from '../responses.js';

/** @typedef {'schema' | 'open'} BuildMode */
/** @typedef {'confidence' | 'required'} ReviewPolicy */
/** @typedef {{kb_id: string, name: string, build_mode: BuildMode, schema_id: string, schema_version: string, model_profile_id: string, model_profile_revision: number | null, config_revision: number, active_generation: string, global_summary_enabled: boolean, review_policy: ReviewPolicy}} KnowledgeBase */

/** @param {unknown} value @returns {KnowledgeBase} */
export function parseKnowledgeBase(value) {
  const row = record(value), revision = number(row.config_revision);
  if (!text(row.kb_id) || !text(row.name) || !Number.isInteger(revision) || revision < 1
      || (row.build_mode !== 'schema' && row.build_mode !== 'open')
      || (row.review_policy !== 'confidence' && row.review_policy !== 'required')) {
    throw new TypeError('Invalid knowledge-base response');
  }
  return {
    kb_id: text(row.kb_id), name: text(row.name), build_mode: row.build_mode,
    schema_id: text(row.schema_id), schema_version: text(row.schema_version),
    model_profile_id: text(row.model_profile_id), config_revision: revision,
    model_profile_revision: row.model_profile_revision == null ? null : number(row.model_profile_revision),
    active_generation: text(row.active_generation), review_policy: row.review_policy,
    global_summary_enabled: row.global_summary_enabled === true,
  };
}

/** @returns {KnowledgeBase} */
export function newKnowledgeBase() {
  return { kb_id: '', name: '', build_mode: 'schema', schema_id: '', schema_version: '',
    model_profile_id: '', model_profile_revision: null, config_revision: 0, active_generation: '',
    global_summary_enabled: false, review_policy: 'confidence' };
}
