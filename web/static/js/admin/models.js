// @ts-check
/** Role assignments are the main object; drafts, test receipts and publication stay distinct. */
import { computed, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { modelState } from './model_state.js';
import { modelActions } from './model_actions.js';
import { forRole, modelRoles } from './model_types.js';
import { modelText as T, text } from '../i18n.js';

export default {
  name: 'ModelsPanel',
  setup() {
    const state = modelState();
    const actions = modelActions(state);
    const canSave = computed(() => state.form.value.id && state.form.value.name && modelRoles.every(
      role => role === 'rerank' || state.form.value.roles[role],
    ));
    const canTest = computed(() => state.form.value.revision > 0 && !state.dirty.value);
    const canPublish = computed(() => canTest.value && state.form.value.test?.passed &&
      state.form.value.active_revision !== state.form.value.revision);
    /** @param {import('./model_types.js').ModelRole} role */
    const options = role => forRole(state.catalog.value, role);
    onMounted(actions.load);
    return { ...state, ...actions, canSave, canTest, canPublish, modelRoles, options, T, text };
  },
  template: `
<section class="models-panel" :aria-busy="Boolean(busy)">
  <div class="models-heading"><div><h2>{{ T.title }}</h2><p>{{ T.description }}</p></div>
    <button type="button" class="btn btn--ghost" @click="load" :disabled="Boolean(busy)">{{ text.common.refresh }}</button>
  </div>
  <p v-if="error" class="models-message models-message--error" role="alert">{{ error }}</p>
  <p v-if="notice" class="models-message" role="status">{{ notice }}</p>
  <p v-if="busy === 'load'" class="models-message" role="status">{{ text.common.loading }}</p>
  <p v-if="!catalog.length && !busy" class="models-empty">{{ T.empty }}</p>
  <template v-if="catalog.length">
    <div class="models-toolbar">
      <label for="model-profile-select">{{ T.profiles }}</label>
      <select id="model-profile-select" class="select" :value="selected" @change="choose($event.target.value)" :disabled="Boolean(busy)">
        <option value="">{{ T.newProfile }}</option>
        <option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
      </select>
      <span v-if="form.revision" class="models-revision">{{ T.draft }} {{ form.revision }} · {{ T.active }} {{ form.active_revision || T.unpublished }}</span>
    </div>
    <p v-if="!profiles.length" class="models-hint">{{ T.noProfiles }}</p>
    <form @submit.prevent="save" class="models-form">
      <fieldset :disabled="Boolean(busy)">
        <div class="models-identity">
          <label>{{ T.name }}<input class="input" v-model="form.name" maxlength="120" required :placeholder="T.namePlaceholder" @input="markDirty"></label>
          <label>{{ T.id }}<input class="input" v-model="form.id" maxlength="80" pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" required :readonly="form.revision > 0" :placeholder="T.idPlaceholder" @input="markDirty"></label>
        </div>
        <div class="models-role-head"><span>{{ T.role }}</span><span>{{ T.model }}</span></div>
        <div v-for="role in modelRoles" :key="role" class="models-role">
          <label :for="'model-role-' + role">{{ T.roles[role] }}</label>
          <select :id="'model-role-' + role" class="select" v-model="form.roles[role]" @change="markDirty" :required="role !== 'rerank'">
            <option :value="null">{{ role === 'rerank' ? T.disabled : T.chooseModel }}</option>
            <option v-for="model in options(role)" :key="model.id" :value="model.id">{{ model.provider }} / {{ model.model }}</option>
          </select>
        </div>
      </fieldset>
      <div class="models-actions">
        <span class="models-hint">{{ dirty ? T.dirty : T.testFirst }}</span>
        <button type="submit" class="btn btn--ghost" :disabled="Boolean(busy) || !canSave">{{ busy === 'save' ? T.saving : T.save }}</button>
        <button type="button" class="btn btn--ghost" :disabled="Boolean(busy) || !canTest" @click="test">{{ busy === 'test' ? T.testing : T.test }}</button>
        <button type="button" class="btn btn--primary" :disabled="Boolean(busy) || !canPublish" @click="publish('publish')">{{ busy === 'publish' ? T.publishing : T.publish }}</button>
      </div>
    </form>
    <div v-if="form.test" class="models-results">
      <h3>{{ T.results }}</h3>
      <div class="data-table-wrap"><table class="data-table">
        <thead><tr><th>{{ T.role }}</th><th>{{ T.capability }}</th><th>{{ T.result }}</th><th class="models-number">{{ T.latency }}</th><th class="models-number">{{ T.tokens }}</th></tr></thead>
        <tbody><tr v-for="(row, index) in form.test.checks" :key="index">
          <td>{{ T.roles[row.role] }}</td><td>{{ T.capabilities[row.capability] || row.capability }}</td>
          <td><span :class="row.status === 'failed' ? 'cell-danger' : ''">{{ T.errors[row.error_code] || T.statuses[row.status] }}</span></td>
          <td class="models-number">{{ row.latency_ms }} ms</td><td class="models-number">{{ row.tokens }} tok</td>
        </tr></tbody>
      </table></div>
    </div>
    <div v-if="form.active_revision > 1" class="models-rollback">
      <label for="model-rollback">{{ T.rollbackRevision }}</label>
      <input id="model-rollback" class="input" type="number" v-model.number="targetRevision" :min="1" :max="form.active_revision - 1">
      <button type="button" class="btn btn--ghost" :disabled="Boolean(busy) || dirty" @click="publish('rollback')">{{ T.rollback }}</button>
    </div>
  </template>
</section>
`,
};
