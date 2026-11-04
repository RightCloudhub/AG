// @ts-check
import { ref, computed } from '../../vendor/vue.esm-browser.prod.js';
import { loadProfiles } from '../admin/model_api.js';
import { activeKnowledge, knowledgeRole, acceptKnowledgeBase, selectKnowledgeBase } from '../store/knowledge.js';
import { requestGeneration, isCurrentRequest } from '../request_scope.js';
import { knowledgeText as K } from '../i18n.js';
import { newKnowledgeBase } from './config_types.js';
import { saveKnowledgeBase } from './config_api.js';

export function knowledgeConfigState() {
  const open = ref(false), creating = ref(false), busy = ref(false), error = ref(''), notice = ref('');
  const form = ref(newKnowledgeBase());
  const profiles = ref(/** @type {import('../admin/model_types.js').Profile[]} */ ([]));
  const canEdit = computed(() => knowledgeRole.value === 'admin');
  const canCreate = computed(() => ['admin', 'operator'].includes(knowledgeRole.value));
  const profileAvailable = computed(() => profiles.value.some(row => row.id === form.value.model_profile_id));

  /** @param {boolean} isNew */
  function edit(isNew) {
    if (isNew ? !canCreate.value : !canEdit.value) return;
    creating.value = isNew;
    form.value = isNew || !activeKnowledge.value ? newKnowledgeBase() : { ...activeKnowledge.value };
    error.value = ''; notice.value = ''; open.value = true;
    if (canEdit.value) void loadAvailableProfiles();
  }
  async function loadAvailableProfiles() {
    try { profiles.value = (await loadProfiles()).filter(row => row.active_revision > 0); }
    catch (failure) {
      if (!(failure instanceof Error && failure.name === 'AbortError')) error.value = failure instanceof Error ? failure.message : String(failure);
    }
  }
  function close() { if (!busy.value) open.value = false; }
  function changeMode() {
    if (form.value.build_mode === 'open') form.value.review_policy = 'confidence';
  }
  function changeProfile() { form.value.model_profile_revision = null; }
  async function save() {
    if (busy.value || (creating.value ? !canCreate.value : !canEdit.value)) return;
    const generation = requestGeneration();
    busy.value = true; error.value = ''; notice.value = '';
    try {
      const saved = await saveKnowledgeBase(form.value, creating.value);
      if (!isCurrentRequest(generation)) return;
      acceptKnowledgeBase(saved); form.value = saved; notice.value = K.saved; open.value = false;
      if (creating.value) selectKnowledgeBase(saved.kb_id);
    } catch (failure) {
      if (isCurrentRequest(generation) && !(failure instanceof Error && failure.name === 'AbortError')) error.value = failure instanceof Error ? failure.message : String(failure);
    } finally { busy.value = false; }
  }
  return { activeKnowledge, canEdit, canCreate, open, creating, busy, error, notice, form, profiles, profileAvailable, edit, close, save, changeMode, changeProfile };
}
