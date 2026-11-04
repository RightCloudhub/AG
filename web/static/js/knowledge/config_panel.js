// @ts-check
import { knowledgeConfigState } from './config_state.js';
import { knowledgeText as K, text as T } from '../i18n.js';
import SummaryStatus from './summary_panel.js';

export default {
  name: 'KnowledgeConfig',
  components: { SummaryStatus },
  setup() { return { ...knowledgeConfigState(), K, T }; },
  template: `<section class="knowledge-config" :aria-label="K.configuration">
    <header class="knowledge-config__heading">
      <div><h2>{{ activeKnowledge?.name || K.defaultName }}</h2>
        <p v-if="activeKnowledge">{{ K.modes[activeKnowledge.build_mode] }} · {{ activeKnowledge.global_summary_enabled ? K.summaryOn : K.summaryOff }}</p>
      </div>
      <div class="knowledge-config__actions">
        <button v-if="canEdit && activeKnowledge" type="button" class="btn btn--ghost" @click="edit(false)" :disabled="busy">{{ K.configure }}</button>
        <button v-if="canCreate" type="button" class="btn btn--secondary" @click="edit(true)" :disabled="busy">{{ K.create }}</button>
      </div>
    </header>
    <p v-if="notice" class="knowledge-config__notice" role="status">{{ notice }}</p>
    <p v-if="error" class="row-error" role="alert">{{ error }}</p>
    <form v-if="open" class="knowledge-config__form" @submit.prevent="save">
      <h3>{{ creating ? K.createTitle : K.configuration }}</h3>
      <fieldset :disabled="busy">
        <div class="knowledge-config__grid">
          <label class="field"><span class="field__label">{{ K.name }}</span><input id="kb-name" class="input" v-model="form.name" required maxlength="200"></label>
          <label class="field"><span class="field__label">{{ K.id }}</span><input id="kb-id" class="input" v-model="form.kb_id" required maxlength="128" pattern="[A-Za-z0-9][A-Za-z0-9_.-]*" :readonly="!creating" aria-describedby="kb-id-hint"><span id="kb-id-hint" class="hint">{{ K.idHint }}</span></label>
          <div class="field knowledge-config__wide"><label class="field__label" for="kb-mode">{{ K.mode }}</label>
            <select v-if="creating" id="kb-mode" class="select" v-model="form.build_mode" @change="changeMode" aria-describedby="kb-mode-hint"><option value="schema">{{ K.modes.schema }}</option><option value="open">{{ K.modes.open }}</option></select>
            <p v-else id="kb-mode">{{ K.modes[form.build_mode] }}</p>
            <p id="kb-mode-hint" class="hint">{{ creating ? K.modeHints[form.build_mode] : K.migration }}</p>
          </div>
          <template v-if="creating && form.build_mode === 'schema'">
            <label class="field"><span class="field__label">{{ K.schemaId }}</span><input class="input" v-model="form.schema_id" :placeholder="K.schemaDefault"></label>
            <label class="field"><span class="field__label">{{ K.schemaVersion }}</span><input class="input" v-model="form.schema_version"></label>
          </template>
          <label class="field"><span class="field__label">{{ K.profile }}</span>
            <select id="kb-profile" class="select" v-model="form.model_profile_id" @change="changeProfile" :disabled="!canEdit" aria-describedby="kb-profile-hint"><option value="">{{ K.inheritedProfile }}</option>
              <option v-if="form.model_profile_id && !profileAvailable" :value="form.model_profile_id">{{ form.model_profile_id }}</option>
              <option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
            </select><span id="kb-profile-hint" class="hint">{{ K.profileHint }}</span>
          </label>
          <label class="field"><span class="field__label">{{ K.review }}</span><select id="kb-review" class="select" v-model="form.review_policy" :aria-describedby="form.build_mode === 'open' ? 'kb-review-hint' : null"><option value="confidence">{{ K.reviewOptions.confidence }}</option><option value="required" :disabled="form.build_mode === 'open'">{{ K.reviewOptions.required }}</option></select><span v-if="form.build_mode === 'open'" id="kb-review-hint" class="hint">{{ K.requiredSchemaOnly }}</span></label>
          <div class="field knowledge-config__wide"><span class="field__label">{{ K.summary }}</span>
            <label class="knowledge-config__check"><input id="kb-summary" type="checkbox" v-model="form.global_summary_enabled" aria-describedby="kb-summary-hint">{{ K.summaryToggle }}</label><span id="kb-summary-hint" class="hint">{{ K.summaryHint }}</span>
          </div>
        </div>
      </fieldset>
      <footer class="knowledge-config__footer"><span class="hint" v-if="!creating">{{ K.revision }} {{ form.config_revision }} · {{ K.generation }} {{ form.active_generation }}</span>
        <button type="button" class="btn btn--ghost" @click="close" :disabled="busy">{{ T.common.cancel }}</button>
        <button type="submit" class="btn btn--primary" :disabled="busy">{{ busy ? K.saving : creating ? K.create : K.save }}</button>
      </footer>
    </form>
    <summary-status v-if="activeKnowledge" :key="activeKnowledge.kb_id"></summary-status>
  </section>`,
};
