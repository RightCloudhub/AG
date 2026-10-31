// @ts-check
/** Template fragments for the composer (§5.4: @ entities, / commands) and the guided-question dialog. */

export const COMPOSER_TEMPLATE = `
<div class="composer-dock"><div class="composer-dock__inner"><div class="composer">
  <div class="composer__box">
    <textarea ref="inputEl" class="composer__input" rows="1" :placeholder="T.composer.placeholder"
              v-model="question" @input="onInput" @keydown="handleKeydown" @blur="closeMenus"
              :aria-label="T.composer.ariaInput" aria-autocomplete="list" :aria-expanded="mention.open || slash.open" aria-controls="composer-menu"></textarea>
    <button v-if="streaming" type="button" class="composer__send composer__send--stop" @click="cancelStream" :aria-label="T.composer.stopAria" :title="T.composer.stop"><ag-icon name="stop"></ag-icon></button>
    <button v-else type="button" class="composer__send" :disabled="!canSend" @click="sendQuestion()" :aria-label="T.composer.sendAria" :title="T.composer.send"><ag-icon name="arrowUp"></ag-icon></button>
    <ul v-if="mention.open" id="composer-menu" class="menu menu--up" role="listbox" :aria-label="T.composer.mention">
      <li v-for="(e, i) in mention.items" :key="e.id" class="menu__item" role="option" :class="{ 'menu__item--active': i === mention.active }" :aria-selected="i === mention.active" @mousedown.prevent="pickMention(e)">
        <span class="mention__name">{{ e.name }}</span><span class="tag">{{ typeLabel(e.type) }}</span></li>
      <li v-if="!mention.items.length" class="menu__empty hint">{{ T.composer.mentionEmpty }}</li>
    </ul>
    <ul v-else-if="slash.open" id="composer-menu" class="menu menu--up" role="listbox" :aria-label="T.composer.slashHint">
      <li v-for="(c, i) in slash.items" :key="c.id" class="menu__item" role="option" :class="{ 'menu__item--active': i === slash.active }" :aria-selected="i === slash.active" @mousedown.prevent="onSlash(c)">
        <ag-icon :name="c.icon" :size="14"></ag-icon><span>{{ c.label }}</span><span class="hint">/{{ c.id }}</span></li>
      <li v-if="!slash.items.length" class="menu__empty hint">{{ T.composer.slashEmpty }}</li>
    </ul>
  </div>
  <div class="composer__hint"><span>{{ T.composer.hint }}</span>
    <span v-if="planUnavailable" class="notice" role="status"><ag-icon name="info" :size="12"></ag-icon><span>{{ T.composer.planUnavailable }}</span></span></div>
</div></div></div>`;

export const WIZARD_TEMPLATE = `
<div v-if="wizardOpen" class="modal-backdrop" @click.self="closeWizard">
  <div class="dialog dialog--wide" role="dialog" aria-modal="true" aria-labelledby="wizard-title" @keydown="wizardKeydown">
    <header class="dialog__head">
      <div><h2 id="wizard-title" class="dialog__title">{{ T.wizard.title }}</h2><p class="wizard__subtitle">{{ T.wizard.subtitle }}</p></div>
      <button type="button" class="btn btn--icon btn--ghost" @click="closeWizard" :aria-label="T.common.close"><ag-icon name="close"></ag-icon></button>
    </header>
    <ol class="wizard__steps">
      <li v-for="(label, i) in T.wizard.steps" :key="label" class="wizard__step" :class="{ 'wizard__step--active': wizard.step === i, 'wizard__step--done': wizard.step > i }"
          :aria-current="wizard.step === i ? 'step' : null"><ag-icon v-if="wizard.step > i" name="check" :size="14"></ag-icon>{{ label }}</li>
    </ol>
    <section class="dialog__body">
      <div v-if="wizard.step === WizardStep.ENTITY" class="field-stack">
        <label class="field__label" for="wizard-entity">{{ T.wizard.entityLabel }}</label>
        <input id="wizard-entity" ref="wizardFirstEl" class="input" :value="wizard.entityQuery" @input="setWizardQuery($event.target.value)"
               :placeholder="T.wizard.entityPlaceholder" autocomplete="off" role="combobox" aria-autocomplete="list"
               :aria-expanded="wizardSuggestions.length > 0" aria-controls="wizard-entity-list">
        <ul id="wizard-entity-list" class="choice-list" role="listbox">
          <li v-for="e in wizardSuggestions" :key="e.id" role="option" :aria-selected="wizard.entity?.name === e.name">
            <button type="button" class="choice__btn" @click="chooseWizardEntity(e)">{{ e.name }}<span class="tag">{{ typeLabel(e.type) }}</span></button></li>
        </ul>
        <p v-if="wizard.entity" class="wizard__chosen"><ag-icon name="check" :size="14"></ag-icon>{{ T.wizard.chosen }} {{ wizard.entity.name }}<span v-if="wizard.entity.type" class="hint">· {{ typeLabel(wizard.entity.type) }}</span></p>
        <p v-else-if="wizard.entityQuery && !wizardSuggestions.length" class="hint">{{ T.wizard.entityEmpty }}
          <button type="button" class="link-btn" @click="useTypedEntity">{{ fmt(T.wizard.useTyped, { name: wizard.entityQuery }) }}</button></p>
      </div>
      <div v-else-if="wizard.step === WizardStep.RELATIONS" class="field-stack">
        <p class="field__label">{{ fmt(T.wizard.relationLabel, { max: UiPolicy.WIZARD_MAX_RELATIONS }) }}</p>
        <div class="chain" aria-live="polite">
          <span class="chain__node">{{ startName }}</span>
          <template v-for="(label, i) in chainLabels" :key="i"><span class="chain__arrow" aria-hidden="true"><ag-icon name="arrowRight" :size="14"></ag-icon></span>
            <span class="chain__rel">{{ label }}<button type="button" class="chain__x" @click="dropRelation(i)" :aria-label="T.common.remove"><ag-icon name="close" :size="12"></ag-icon></button></span></template>
          <span v-if="!chainLabels.length" class="hint">{{ T.wizard.chainEmpty }}</span>
        </div>
        <div class="choice-grid">
          <button v-for="r in wizardRelations" :key="r.id" type="button" class="chip chip--sm" @click="addRelation(r.id)"
                  :disabled="wizard.chain.length >= UiPolicy.WIZARD_MAX_RELATIONS">{{ r.label }} <span class="chip__sub">{{ typeLabel(r.to) }}</span></button>
        </div>
        <p class="hint">{{ T.wizard.relationCatalogNote }}</p>
      </div>
      <div v-else-if="wizard.step === WizardStep.CONSTRAINTS" class="field-stack">
        <p class="field__label">{{ T.wizard.constraintLabel }} <span class="hint">({{ T.common.optional }})</span></p>
        <label class="field"><span>{{ T.wizard.time }}</span><input class="input" :value="wizard.time" @input="setConstraint({ time: $event.target.value })"></label>
        <label class="field field--row"><input type="checkbox" :checked="wizard.count" @change="setConstraint({ count: $event.target.checked })"><span>{{ T.wizard.count }}</span></label>
        <div class="field"><span>{{ T.wizard.intersect }}</span>
          <div class="field__row">
            <select class="select" :value="wizard.intersect?.relation ?? ''" @change="setIntersectRelation($event.target.value)" :aria-label="T.wizard.intersect">
              <option value="">—</option><option v-for="r in RELATION_CATALOG" :key="r.id" :value="r.id">{{ r.label }}</option></select>
            <input class="input" list="intersect-list" :value="wizard.intersect?.entity ?? ''" :placeholder="T.wizard.intersectEntity" :aria-label="T.wizard.intersectEntity" @input="setIntersectEntity($event.target.value)">
            <datalist id="intersect-list"><option v-for="e in intersectSuggestions" :key="e.id" :value="e.name"></option></datalist>
          </div>
        </div>
      </div>
      <div v-else class="field-stack">
        <p class="field__label">{{ T.wizard.planLabel }}</p><p class="hint">{{ T.wizard.planHint }}</p>
        <ol class="plan-list">
          <li v-for="(q, i) in wizardDraftLabels" :key="i" class="plan-item">
            <span class="plan-item__idx">{{ i + 1 }}.</span><span class="plan-item__text">{{ q }}</span>
            <span class="plan-item__ops">
              <button type="button" class="btn btn--icon btn--ghost btn--xs" @click="shiftRelation(i, -1)" :disabled="i === 0" :aria-label="T.common.moveUp"><ag-icon name="arrowUp" :size="14"></ag-icon></button>
              <button type="button" class="btn btn--icon btn--ghost btn--xs" @click="shiftRelation(i, 1)" :disabled="i === wizardDraftLabels.length - 1" :aria-label="T.common.moveDown"><ag-icon name="arrowDown" :size="14"></ag-icon></button>
              <button type="button" class="btn btn--icon btn--ghost btn--xs" @click="dropRelation(i)" :aria-label="T.common.remove"><ag-icon name="close" :size="14"></ag-icon></button>
            </span>
          </li>
        </ol>
        <label class="field"><span>{{ T.wizard.previewLabel }}</span><textarea class="input" rows="3" :value="wizardQuestion" @input="setOverride($event.target.value)"></textarea></label>
      </div>
    </section>
    <footer class="dialog__foot">
      <span class="hint">{{ T.wizard.keyboard }}</span>
      <div class="wizard__actions">
        <button type="button" class="btn btn--ghost" @click="wizardSkip">{{ T.common.skip }}</button>
        <button type="button" class="btn btn--secondary" @click="wizardBack" :disabled="wizard.step === 0">{{ T.common.back }}</button>
        <button v-if="wizard.step < WizardStep.PREVIEW" type="button" class="btn btn--primary" @click="wizardNext" :disabled="!wizardCanAdvance">{{ T.common.next }}</button>
        <template v-else>
          <button type="button" class="btn btn--secondary" @click="applyWizard">{{ T.wizard.generate }}</button>
          <button type="button" class="btn btn--primary" @click="applyWizardAndSend">{{ T.wizard.generateAndSend }}</button>
        </template>
      </div>
    </footer>
  </div>
</div>`;
