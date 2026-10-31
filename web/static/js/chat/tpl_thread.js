// @ts-check
/** Rows under a question that are not its answer: an error, a notice, a clarification card. Rendered inside a page for row `r`. */

export const THREAD_ROWS_TEMPLATE = `
<p v-if="r.msg.role === MessageRole.ERROR" class="row-error" role="alert"><ag-icon name="warning" :size="14"></ag-icon><span>{{ r.msg.text }}</span></p>
<p v-else-if="r.msg.role === MessageRole.NOTICE" class="notice row-notice" role="status"><ag-icon name="info" :size="14"></ag-icon><span>{{ r.msg.text }}</span></p>
<div v-else-if="r.msg.role === MessageRole.CLARIFY && r.msg.clarify" class="clarify" :class="{ 'clarify--resolved': r.msg.resolved }" role="group" :aria-label="clarifyTitle(r.msg)">
  <div class="clarify__title">{{ clarifyTitle(r.msg) }}</div>
  <div v-if="r.msg.clarify.kind === ClarifyKind.BROAD" class="clarify__actions">
    <button type="button" class="btn btn--sm btn--primary" :disabled="r.msg.resolved" @click="resolveBroad(r.msg, r.index, 'wizard')">{{ T.clarify.openWizard }}</button>
    <button type="button" class="btn btn--sm btn--secondary" :disabled="r.msg.resolved" @click="resolveBroad(r.msg, r.index, 'send')">{{ T.clarify.sendAnyway }}</button>
    <button type="button" class="btn btn--sm btn--ghost" :disabled="r.msg.resolved" @click="skipClarify(r.index)">{{ T.clarify.skip }}</button>
  </div>
  <template v-else>
    <p v-if="r.msg.clarify.multi" class="hint">{{ T.clarify.multiHint }}</p>
    <div class="clarify__options">
      <button v-for="o in r.msg.clarify.options" :key="o.id" type="button" class="chip" :disabled="r.msg.resolved"
              :aria-pressed="r.msg.clarify.multi ? isPicked(r.index, o.id) : null" @click="pickClarify(r.msg, r.index, o)">{{ o.label }}</button>
    </div>
    <div v-if="r.msg.clarify.multi" class="clarify__actions">
      <button type="button" class="btn btn--sm btn--primary" :disabled="r.msg.resolved" @click="confirmClarify(r.msg, r.index)">{{ T.clarify.continueWith }}</button>
      <button type="button" class="btn btn--sm btn--ghost" :disabled="r.msg.resolved" @click="skipClarify(r.index)">{{ T.clarify.skip }}</button>
    </div>
  </template>
</div>`;
