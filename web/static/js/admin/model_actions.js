// @ts-check
import * as api from './model_api.js';
import { newProfile } from './model_state.js';
import { modelText as T } from '../i18n.js';

/** @param {import('./model_state.js').ModelState} state */
export function modelActions(state) {
  /** @param {string} name @param {() => Promise<void>} work */
  async function run(name, work) {
    if (state.busy.value) return;
    state.busy.value = name; state.error.value = ''; state.notice.value = '';
    try { await work(); }
    catch (error) { state.error.value = error instanceof Error ? error.message : String(error); }
    finally { state.busy.value = ''; }
  }

  async function refresh() {
    const [catalog, profiles] = await Promise.all([api.loadModels(), api.loadProfiles()]);
    state.catalog.value = catalog; state.profiles.value = profiles;
  }
  /** @param {string} id */
  function choose(id) {
    const found = state.profiles.value.find(profile => profile.id === id);
    state.form.value = found ? { ...found, roles: { ...found.roles } } : newProfile();
    state.selected.value = id; state.dirty.value = false; state.notice.value = ''; state.error.value = '';
    state.targetRevision.value = Math.max(1, state.form.value.active_revision - 1);
  }
  function save() { return run('save', async () => {
    const saved = await api.saveProfile(state.form.value);
    await refresh(); choose(saved.id); state.notice.value = T.saved;
  }); }
  function test() { return run('test', async () => {
    const result = await api.testProfile(state.form.value);
    state.form.value.test = result;
    state.notice.value = result?.passed ? T.testPassed : T.testFailed;
  }); }
  /** @param {'publish'|'rollback'} action */
  function publish(action) { return run(action, async () => {
    const form = state.form.value;
    await api.publishProfile(form, action === 'rollback' ? state.targetRevision.value : form.revision, action);
    await refresh(); choose(form.id); state.notice.value = action === 'rollback' ? T.rolledBack : T.published;
  }); }
  function markDirty() { state.dirty.value = true; state.notice.value = ''; }
  return { load: () => run('load', refresh), save, test, publish, choose, markDirty };
}
