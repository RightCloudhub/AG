// @ts-check
/** Template fragments for the welcome state (§6 空白页) and one question page (§4.4: title → HopRail → conclusion → meta → follow-ups). */

/** Example questions from evals/datasets/poc_cases.jsonl, one card per reasoning shape; the dot takes hop colour 1–4. */
export const WELCOME_GROUPS = Object.freeze([
  { id: 'twoHop', hop: 1, question: 'Who is the CEO of the parent company of NovaTech Industries?' },
  { id: 'threeHop', hop: 2, question: 'Which companies did the CEO of the parent company of NovaTech Industries previously work at?' },
  { id: 'compare', hop: 3, question: 'Did Elena Varga and Marcus Chen both work at Orion Systems?' },
  { id: 'intersect', hop: 4, question: 'Among suppliers of QuantumEdge Server, which also supply Helix Compute?' },
]);

export const WELCOME_TEMPLATE = `
<section v-if="!pages.length" class="welcome" :aria-label="T.welcome.title">
  <h1 class="welcome__title">{{ T.welcome.title }}</h1>
  <div class="welcome__grid">
    <button v-for="g in WELCOME_GROUPS" :key="g.id" type="button" class="hint-card" :class="hopClass(g.hop)" @click="useHint(g.question)">
      <span class="hint-card__kind"><span class="hint-card__dot" aria-hidden="true"></span>{{ T.welcome.groups[g.id] }}</span>
      <span class="hint-card__q">{{ g.question }}</span>
    </button>
  </div>
</section>`;

export const RAIL_TEMPLATE = `
<nav class="hoprail" :class="{ 'hoprail--scroll': p.rail.length > UiPolicy.HOP_RAIL_SCROLL_AFTER + 1 }" :aria-label="T.hoprail.label">
  <ol class="hoprail__track" role="list">
    <li v-for="(it, i) in p.rail" :key="it.key" role="listitem" class="hop" :aria-label="railAria(it)"
        :class="[hopClass(it.hop), 'hop--' + it.state, 'hop--' + it.kind, { 'hop--selected': isSelected(p, it), 'hop--new': p.live }]">
      <span class="hop__link" :class="{ 'hop__link--pending': linkPending(p.rail, i) }" aria-hidden="true"></span>
      <button type="button" class="hop__btn" :disabled="it.kind !== 'hop' || !p.answer" @click="selectHop(p, it)">
        <span class="hop__node" :class="{ 'hop__node--diamond': it.kind === 'answer' }">
          <span v-if="it.kind === 'hop'">{{ it.hop }}</span>
          <span v-else-if="it.kind === 'answer' && it.state === 'done'"><ag-icon name="check" :size="12"></ag-icon></span>
          <span v-else-if="it.state === 'failed'"><ag-icon name="close" :size="12"></ag-icon></span>
        </span>
        <span class="hop__label">{{ railLabel(it) }}</span>
        <span v-if="railMeta(it)" class="hop__meta">{{ railMeta(it) }}</span>
      </button>
      <span v-if="it.kind === 'hop' && it.subQuestion" class="popover"><strong>{{ it.subQuestion }}</strong><template v-if="it.conclusion"><br>{{ it.conclusion }}</template></span>
      <template v-if="it.kind === 'hop' && p.answer">
        <button type="button" class="btn btn--icon btn--xs btn--ghost hop__more" aria-haspopup="menu" :aria-expanded="hopMenuId === p.key + it.key"
                :aria-label="T.common.more" @click.stop="toggleHopMenu(p.key + it.key)"><ag-icon name="kebab" :size="14"></ag-icon></button>
        <div v-if="hopMenuId === p.key + it.key" class="menu menu--down" role="menu" @keydown.esc="closeHopMenu">
          <button type="button" class="menu__item" role="menuitem" @click="rerunFromHop(p, it)"><ag-icon name="refresh" :size="14"></ag-icon>{{ T.hoprail.retry }}</button>
          <button type="button" class="menu__item" role="menuitem" @click="selectHop(p, it); closeHopMenu()"><ag-icon name="filter" :size="14"></ag-icon>{{ fmt(T.hoprail.focus, { n: it.hop }) }}</button>
        </div>
      </template>
    </li>
  </ol>
  <ol v-if="p.live && planLines.length" class="hoprail__plan">
    <li v-for="l in planLines" :key="l">{{ l }}</li>
  </ol>
  <p v-if="p.live && live?.cacheHit" class="hoprail__note"><ag-icon name="flash" :size="14"></ag-icon>{{ T.hoprail.cache }}</p>
</nav>`;

export const PAGE_TEMPLATE = `
<article v-for="(p, pi) in pages" :key="p.key" class="qpage" :aria-label="T.page.yourQuestion">
  <div v-if="editingKey === p.key" class="qpage__editor">
    <label class="sr-only" :for="'edit-' + p.key">{{ T.page.edit }}</label>
    <textarea :id="'edit-' + p.key" ref="editEl" class="input" rows="2" v-model="editText" @keydown.esc="cancelEdit" @keydown.enter.exact.prevent="saveEdit(p)"></textarea>
    <div class="qpage__editor-actions">
      <button type="button" class="btn btn--primary btn--sm" @click="saveEdit(p)">{{ T.page.save }}</button>
      <button type="button" class="btn btn--ghost btn--sm" @click="cancelEdit">{{ T.page.cancel }}</button>
      <span class="hint">{{ T.page.editHint }}</span>
    </div>
  </div>
  <div v-else class="qpage__head">
    <component :is="pi === 0 ? 'h1' : 'h2'" class="qpage__title">{{ p.question }}</component>
    <button type="button" class="btn btn--icon btn--sm btn--ghost qpage__edit" @click="startEdit(p)" :aria-label="T.page.edit" :title="T.page.edit"><ag-icon name="edit" :size="14"></ag-icon></button>
  </div>
  <div v-if="pendingPlan && pendingPlan.key === p.key" class="plan-edit" role="group" :aria-label="T.commands.plan">
    <div v-for="(line, li) in pendingPlan.lines" :key="li" class="plan-edit__row"><ag-icon name="grip" :size="14"></ag-icon><span class="plan-item__idx">{{ li + 1 }}.</span><span class="plan-item__text">{{ line }}</span></div>
    <p class="notice" role="status"><ag-icon name="info" :size="14"></ag-icon><span>{{ T.composer.planUnavailable }}</span></p>
    <div class="plan-edit__foot">
      <button type="button" class="btn btn--primary btn--sm" @click="confirmPlan">{{ T.plan.run }}</button>
      <button type="button" class="btn btn--ghost btn--sm" @click="cancelPlan">{{ T.common.cancel }}</button>
    </div>
  </div>
  <template v-else-if="p.rail.length">${RAIL_TEMPLATE}</template>
  <template v-for="r in p.rows" :key="r.index">${'${THREAD_ROWS}'}</template>
  <div v-if="p.live && !p.answer" class="answer" aria-busy="true">
    <div class="answer__skel" aria-hidden="true"><span class="skeleton skeleton--w85"></span><span class="skeleton"></span><span class="skeleton skeleton--w60"></span></div>
  </div>
  <section v-else-if="p.answer" class="answer" :class="{ 'answer--none': p.answer.msg.data.status === QueryStatus.NO_ANSWER }" aria-live="polite">
    <template v-if="p.answer.msg.data.status === QueryStatus.NO_ANSWER">
      <h3 class="answer__none-title">{{ T.answer.notFoundTitle }}</h3>
      <p class="answer__reason">{{ noAnswerReason(p.answer.msg.data) }}</p>
      <ul v-if="p.answer.msg.data.missing_info.length" class="answer__missing"><li v-for="m in p.answer.msg.data.missing_info" :key="m">{{ m }}</li></ul>
      <button type="button" class="btn btn--secondary btn--sm" @click="supplyKnowledge(p)"><ag-icon name="knowledge" :size="14"></ag-icon>{{ T.answer.supply }}</button>
    </template>
    <template v-else>
      <span v-if="p.answer.msg.data.status === QueryStatus.PARTIAL" class="tag tag--warn answer__flag">{{ T.answer.partial }}</span>
      <p class="answer__body">{{ p.answer.msg.data.answer }}<template v-if="citationsFor(p.answer.msg.data).length"><span class="sr-only">{{ T.answer.cites }}</span><span v-for="c in citationsFor(p.answer.msg.data)" :key="c.ev.id" class="cite-anchor" :class="hopClass(hopOf(p.answer.msg.data, c.ev.id))">
        <button type="button" class="cite" :class="{ 'cite--active': inspHighlight === c.ev.id }" :aria-label="fmt(T.answer.openEvidence, { n: c.index })" @click="openEvidence(p, c.ev.id)">[{{ c.index }}]</button>
        <span class="popover"><strong>{{ T.answer.snippet }} · {{ c.ev.score.toFixed(UiPolicy.SCORE_PRECISION) }}</strong><br>{{ citeSnippet(c.ev) }}</span></span></template></p>
      <ul v-if="p.answer.msg.data.missing_info.length" class="answer__missing"><li v-for="m in p.answer.msg.data.missing_info" :key="m">{{ m }}</li></ul>
    </template>
  </section>
  <div v-if="p.answer" class="qmeta">
    <span class="qmeta__line">{{ metaLine(p.answer.msg.data) }}</span>
    <span v-if="confidenceTag(p.answer.msg.data)" class="tag qmeta__status" :class="confidenceClass(p.answer.msg.data)">{{ confidenceTag(p.answer.msg.data) }}</span>
    <span v-if="p.answer.msg.data.route !== QueryRoute.AGENTIC" class="tag qmeta__status">{{ routeLabel(p.answer.msg.data.route) }}</span>
    <div class="qmeta__actions">
      <button type="button" class="btn btn--icon btn--sm btn--ghost" @click="askAgain(p)" :aria-label="T.answer.askAgain" :title="T.answer.askAgain"><ag-icon name="refresh" :size="14"></ag-icon></button>
      <span class="menu-anchor">
        <button type="button" class="btn btn--icon btn--sm btn--ghost" aria-haspopup="menu" :aria-expanded="copyMenuId === p.key" @click="toggleCopyMenu(p.key)" :aria-label="T.answer.copy" :title="T.answer.copy"><ag-icon name="copy" :size="14"></ag-icon></button>
        <span v-if="copiedId === p.key" class="hint" role="status">{{ T.answer.copied }}</span>
        <div v-if="copyMenuId === p.key" class="menu menu--down" role="menu" @keydown.esc="closeCopyMenu">
          <button type="button" class="menu__item" role="menuitem" @click="copyValue(p.answer.msg.data.answer, p.key)">{{ T.answer.copyAnswer }}</button>
          <button type="button" class="menu__item" role="menuitem" :title="p.answer.msg.data.query_id" @click="copyValue(p.answer.msg.data.query_id, p.key)">{{ T.answer.copyId }}</button>
        </div></span>
      <button type="button" class="btn btn--icon btn--sm btn--ghost" :disabled="p.answer.msg.feedbackSent" :aria-label="T.feedback.up" :title="T.feedback.up"
              :aria-pressed="p.answer.msg.feedbackValue === true" @click="submitFeedback(p.answer.msg, p.answer.index, true)"><ag-icon name="thumbUp" :size="14"></ag-icon></button>
      <button type="button" class="btn btn--icon btn--sm btn--ghost" :disabled="p.answer.msg.feedbackSent" :aria-label="T.feedback.down" :title="T.feedback.down"
              :aria-pressed="p.answer.msg.feedbackValue === false" @click="startNegative(p.answer.msg, p.answer.index)"><ag-icon name="thumbDown" :size="14"></ag-icon></button>
      <span v-if="p.answer.msg.feedbackSent" class="hint" role="status">{{ T.feedback.sent }}</span>
    </div>
    <div v-if="p.answer.msg.feedbackValue === false && !p.answer.msg.feedbackSent" class="qmeta__reasons" role="group" :aria-label="T.feedback.reasonTitle">
      <span class="hint">{{ T.feedback.reasonTitle }}</span>
      <button v-for="r in feedbackReasons" :key="r.id" type="button" class="chip chip--sm" @click="submitFeedback(p.answer.msg, p.answer.index, false, r.id)">{{ r.label }}</button>
    </div>
  </div>
  <div v-if="p.answer && p.answer.msg.followups?.length" class="followups" :aria-label="T.answer.followups">
    <button v-for="q in p.answer.msg.followups" :key="q" type="button" class="chip" @click="useHint(q)">{{ q }}</button>
  </div>
</article>`;
