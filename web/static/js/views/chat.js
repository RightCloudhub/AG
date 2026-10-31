// @ts-check
/**
 * The notebook view (§4.4): question pages (title → HopRail → conclusion → meta → follow-ups), the docked
 * composer with its wizard, and the inspector at the right. Streaming fills the rail hop by hop.
 */
import { ref, computed, nextTick, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { streamQuery, postFeedback, fetchEntities } from '../api.js';
import { record, text, parseAnswer } from '../responses.js';
import {
  ClarifyKind, FeatureFlag, FeedbackReason, KeyboardKey, MessageRole, QueryMode, QueryRoute, QueryStatus, SlashCommand,
  StreamEvent, UiPolicy, View, WizardStep,
} from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { activeConversationId, appendMessage, createConversation, findConversation, updateMessage } from '../store/conversations.js';
import { setPreferences } from '../store/preferences.js';
import { useComposer } from '../chat/use_composer.js';
import { useInspector, inspSyncTo } from '../shell/use_inspector.js';
import * as progress from '../chat/progress.js';
import { RELATION_CATALOG } from '../chat/wizard.js';
import { detectBroadQuestion, detectEntityAmbiguity, missingInfoCard } from '../chat/clarify.js';
import { useClarify } from '../chat/use_clarify.js';
import { suggestFollowups } from '../chat/followups.js';
import { buildPages } from '../chat/pages.js';
import { hopClass, hopOfEvidence, linkPending, railFromAnswer, railFromProgress } from '../chat/hoprail.js';
import {
  citationsFor, citeSnippet, confidenceClass, confidenceTag, copyText, logFrame, metaLine, railAria, railLabel, railMeta, routeLabel,
} from '../chat/presenters.js';
import { COMPOSER_TEMPLATE, WIZARD_TEMPLATE } from '../chat/tpl_composer.js';
import { PAGE_TEMPLATE, WELCOME_GROUPS, WELCOME_TEMPLATE } from '../chat/tpl_page.js';
import { THREAD_ROWS_TEMPLATE } from '../chat/tpl_thread.js';
import { INSPECTOR_TEMPLATE } from '../shell/tpl_inspector.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../chat/types.js').ChatMessage} ChatMessage */
/** @typedef {import('../chat/types.js').ProgressModel} ProgressModel */
/** @typedef {import('../chat/types.js').PageModel} PageModel */
/** @typedef {import('../chat/types.js').LogFrame} LogFrame */
/** @typedef {import('../chat/hoprail.js').RailItem} RailItem */
/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {PageModel & {rail: RailItem[]}} Page */

const REASON = /原因[:：]\s*([^。]+)/;

export default {
  name: 'ChatView',
  setup() {
    const composer = useComposer();
    const inspector = useInspector();
    const streaming = ref(false);
    const sending = ref(false);
    /** @type {Ref<ProgressModel | null>} */
    const live = ref(null);
    /** @type {Ref<AbortController | null>} */
    const abortCtrl = ref(null);
    /** @type {Ref<string | null>} */
    const streamConversationId = ref(null);
    /** @type {Ref<HTMLElement | null>} */
    const scrollEl = ref(null);
    /** @type {Ref<HTMLTextAreaElement | null>} */
    const editEl = ref(null);
    const copiedId = ref(''), copyMenuId = ref(''), hopMenuId = ref(''), editingKey = ref(''), editText = ref('');
    /** @type {Ref<{key: string, question: string, lines: string[]} | null>} */
    const pendingPlan = ref(null);
    /** @type {LogFrame[]} */
    let logFrames = [];
    let logStart = 0;
    /** @type {Map<string, Map<string, number>>} */
    const hopMaps = new Map();

    const conversation = computed(() => findConversation(activeConversationId.value));
    const messages = computed(() => conversation.value?.messages ?? []);
    const streamingHere = computed(() => streaming.value && streamConversationId.value === activeConversationId.value);
    const pages = computed(() => buildPages(messages.value, streamingHere.value).map(p => ({ ...p, rail: railFor(p) })));
    const planLines = computed(() => (live.value ? progress.planLines(live.value) : []));
    const canSend = computed(() => composer.question.value.trim().length > 0 && !streaming.value && !sending.value);
    const feedbackReasons = Object.values(FeedbackReason).map(id => ({ id, label: T.feedback.reasons[id] }));

    /** @param {PageModel} p @returns {RailItem[]} */
    function railFor(p) {
      if (p.live && live.value) return railFromProgress(live.value);
      const answer = p.answer?.msg.data;
      return answer ? railFromAnswer(answer, p.answer?.msg.progress ?? null) : [];
    }
    function scrollBottom() { nextTick(() => { if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight; }); }
    /** @param {ChatMessage} msg @param {string | null} [conversationId] */
    function push(msg, conversationId = null) {
      const target = findConversation(conversationId) ?? conversation.value ?? createConversation(msg.text ?? '');
      const index = appendMessage(target.id, msg);
      scrollBottom();
      return { target: target.id, key: `${msg.ts}-${index}` };
    }
    /** @param {number} index @param {Partial<ChatMessage>} patch */
    function patch(index, patch) { if (conversation.value) updateMessage(conversation.value.id, index, patch); }
    /** @param {string} q */
    async function clarifyFor(q) {
      const broad = detectBroadQuestion(q);
      if (broad) return broad;
      try { return await detectEntityAmbiguity(q, async query => (await fetchEntities({ query, limit: UiPolicy.AUTOCOMPLETE_LIMIT })).data); }
      catch { return null; }
    }
    /** Open a page for the question; in 先确认计划 mode wait for 执行 (§6). @param {string} q @param {boolean} [skipClarification] */
    async function sendQuestion(skipClarification = false, q = composer.question.value.trim()) {
      if (!q || streaming.value || sending.value) return;
      sending.value = true;
      const card = skipClarification ? null : await clarifyFor(q).finally(() => { sending.value = false; });
      sending.value = false;
      composer.question.value = '';
      nextTick(composer.autosize);
      const page = push({ role: MessageRole.USER, text: q, ts: Date.now() });
      if (card) { push({ role: MessageRole.CLARIFY, clarify: card, ts: Date.now() }, page.target); return; }
      if (composer.mode.value === QueryMode.PLAN_CONFIRM) {
        const draft = composer.wizardQuestion.value === q ? composer.wizardDraftLabels.value : [];
        pendingPlan.value = { key: page.key, question: q, lines: draft.length ? draft : [q] };
        return;
      }
      runStream(q, page.target);
    }
    function confirmPlan() { const plan = pendingPlan.value; pendingPlan.value = null; if (plan) runStream(plan.question, activeConversationId.value); }
    function cancelPlan() { pendingPlan.value = null; push({ role: MessageRole.NOTICE, text: T.plan.cancelled, ts: Date.now() }); }
    /** @param {(model: ProgressModel) => ProgressModel} fn */
    function update(fn) { if (live.value) { live.value = fn(live.value); scrollBottom(); } }
    function settle() { streaming.value = false; live.value = null; abortCtrl.value = null; }
    /** @param {string} name @param {unknown} payload */
    function log(name, payload) { logFrames = [...logFrames, logFrame(name, payload, logStart)]; }
    /** @param {string} q @param {string | null} target */
    function runStream(q, target) {
      streamConversationId.value = target;
      streaming.value = true;
      logStart = Date.now();
      logFrames = [];
      live.value = progress.emptyProgress(logStart);
      if (composer.planUnavailable.value) push({ role: MessageRole.NOTICE, text: T.composer.planUnavailable, ts: Date.now() }, target);
      abortCtrl.value = streamQuery(composer.requestFor(q), {
        [StreamEvent.TRIAGE](p) { log(StreamEvent.TRIAGE, p); update(m => progress.applyTriage(m, p)); },
        [StreamEvent.THINKING](p) { log(StreamEvent.THINKING, p); update(m => progress.applyThinking(m, p)); },
        [StreamEvent.SUB_QUESTION](p) { log(StreamEvent.SUB_QUESTION, p); update(m => progress.applySubQuestion(m, p, Date.now())); },
        [StreamEvent.HOP_DONE](p) { log(StreamEvent.HOP_DONE, p); update(m => progress.applyHopDone(m, p, Date.now())); },
        [StreamEvent.CACHE_HIT](p) { log(StreamEvent.CACHE_HIT, p); update(progress.applyCacheHit); },
        [StreamEvent.ANSWER](p) { log(StreamEvent.ANSWER, p); finish(parseAnswer(p), q, target); },
        [StreamEvent.ERROR](p) { log(StreamEvent.ERROR, p); fail(text(record(p).message, T.common.unknownError), target); },
        done() { if (streaming.value) settle(); },
      });
    }
    /** @param {Answer} answer @param {string} q @param {string | null} target */
    function finish(answer, q, target) {
      const done = live.value ? progress.finishProgress(live.value, answer) : null;
      push({ role: MessageRole.ASSISTANT, data: answer, progress: done, log: logFrames, followups: suggestFollowups(answer, q), ts: Date.now() }, target);
      const missing = missingInfoCard(answer, q);
      if (missing) push({ role: MessageRole.CLARIFY, clarify: missing, ts: Date.now() }, target);
      settle();
      if (target === activeConversationId.value) inspector.inspShow(answer, q, logFrames);
    }
    /** @param {string} message @param {string | null} target */
    function fail(message, target) { push({ role: MessageRole.ERROR, text: message, ts: Date.now() }, target); settle(); }
    function cancelStream() {
      if (!abortCtrl.value) return;
      abortCtrl.value.abort();
      const stopped = live.value ? progress.stopProgress(live.value) : null;
      push({ role: MessageRole.NOTICE, text: fmt(T.states.stoppedAt, { n: stopped?.hops.length ?? 0 }), progress: stopped, ts: Date.now() }, streamConversationId.value);
      settle();
    }
    /** @param {import('../contracts.js').SlashCommandName} cmd */
    function runSlash(cmd) {
      if (cmd === SlashCommand.WIZARD) composer.openWizard();
      else if (cmd === SlashCommand.PLAN) setPreferences({ mode: QueryMode.PLAN_CONFIRM });
      else if (cmd === SlashCommand.IMPORT) window.location.hash = View.KNOWLEDGE;
    }
    /** @param {{id: import('../contracts.js').SlashCommandName, label: string, icon: string}} item */
    function onSlash(item) { runSlash(composer.pickSlash(item)); }
    /** @param {KeyboardEvent} e */
    function handleKeydown(e) {
      const handled = composer.menuKeydown(e);
      if (handled === true) { e.preventDefault(); return; }
      if (typeof handled === 'string') { e.preventDefault(); runSlash(handled); return; }
      if (e.key === KeyboardKey.ENTER && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
    }
    /** Esc stops a run (§8). @param {KeyboardEvent} e */
    function onWindowKeydown(e) { if (e.key === KeyboardKey.ESCAPE && streaming.value) cancelStream(); }
    /** @param {ChatMessage} msg @param {number} index @param {boolean} accurate @param {string} [reason] */
    async function submitFeedback(msg, index, accurate, reason = '') {
      if (!msg.data?.query_id || msg.feedbackSent) return;
      try { await postFeedback({ query_id: msg.data.query_id, accurate, reason }); patch(index, { feedbackSent: true, feedbackValue: accurate, feedbackReason: reason }); }
      catch { patch(index, { feedbackValue: undefined }); }
    }
    /** @param {ChatMessage} msg @param {number} index */
    function startNegative(msg, index) { if (!msg.feedbackSent) patch(index, { feedbackValue: false }); }
    /** @param {string} q */
    function useHint(q) { composer.setQuestion(q); }
    /** @param {Page} p */
    function ensureInspected(p) {
      const answer = p.answer?.msg.data;
      if (answer && inspector.inspAnswer.value?.query_id !== answer.query_id) inspector.inspShow(answer, p.question, p.answer?.msg.log ?? []);
    }
    /** @param {Page} p @param {RailItem} it */
    function selectHop(p, it) { if (it.kind !== 'hop' || !p.answer) return; ensureInspected(p); inspector.inspSelectHop(it.hop); closeHopMenu(); }
    /** @param {Page} p @param {RailItem} it */
    function isSelected(p, it) {
      return it.kind === 'hop' && !!p.answer && inspector.inspAnswer.value?.query_id === p.answer.msg.data?.query_id && inspector.inspHop.value === it.hop;
    }
    /** @param {Page} p @param {string} id */
    function openEvidence(p, id) { ensureInspected(p); inspector.inspFocusEvidence(id); }
    /** @param {Answer} answer @param {string} id */
    function hopOf(answer, id) {
      let map = hopMaps.get(answer.query_id);
      if (!map) { map = hopOfEvidence(answer); hopMaps.set(answer.query_id, map); }
      return map.get(id) ?? 0;
    }
    /** Degraded re-run: the real endpoint (FeatureFlag.RERUN_FROM_HOP) is wired here once it ships. @param {Page} p @param {RailItem} it */
    function rerunFromHop(p, it) {
      closeHopMenu();
      if (FeatureFlag.RERUN_FROM_HOP) return;
      push({ role: MessageRole.NOTICE, text: T.composer.rerunUnavailable, ts: Date.now() });
      composer.setQuestion(it.subQuestion);
    }
    /** @param {Page} p */
    function askAgain(p) { sendQuestion(true, p.question); }
    /** @param {Page} p */
    function startEdit(p) { editingKey.value = p.key; editText.value = p.question; nextTick(() => editEl.value?.focus()); }
    /** @param {Page} p */
    function saveEdit(p) { const q = editText.value.trim(); editingKey.value = ''; if (q && q !== p.question) sendQuestion(false, q); }
    function cancelEdit() { editingKey.value = ''; }
    /** @param {Page} p */
    function supplyKnowledge(p) { window.location.hash = View.KNOWLEDGE; }
    /** The reason inside the honest-fallback answer, or the plain sentence. @param {Answer} answer */
    function noAnswerReason(answer) { return REASON.exec(answer.answer)?.[1] ?? T.states.notFoundReason; }
    /** @param {string} id */
    function toggleCopyMenu(id) { copyMenuId.value = copyMenuId.value === id ? '' : id; }
    function closeCopyMenu() { copyMenuId.value = ''; }
    /** @param {string} id */
    function toggleHopMenu(id) { hopMenuId.value = hopMenuId.value === id ? '' : id; }
    function closeHopMenu() { hopMenuId.value = ''; }
    /** @param {string} value @param {string} key */
    async function copyValue(value, key) {
      closeCopyMenu();
      copiedId.value = (await copyText(value)) ? key : '';
      setTimeout(() => { copiedId.value = ''; }, UiPolicy.COPY_FLASH_MS);
    }
    function applyWizardAndSend() { composer.applyWizard(); sendQuestion(true); }
    const clarify = useClarify({ patch, startStream: q => { const page = push({ role: MessageRole.USER, text: q, ts: Date.now() }); runStream(q, page.target); }, setQuestion: composer.setQuestion, openWizard: composer.openWizard });

    onMounted(() => { inspSyncTo(conversation.value); composer.focusInput(); window.addEventListener('keydown', onWindowKeydown); });
    onUnmounted(() => window.removeEventListener('keydown', onWindowKeydown));
    return {
      ...composer, ...inspector, ...clarify, streaming, live, scrollEl, editEl, copiedId, copyMenuId, hopMenuId, editingKey, editText, pendingPlan,
      pages, planLines, canSend, feedbackReasons, sendQuestion, confirmPlan, cancelPlan, cancelStream, handleKeydown, onSlash, submitFeedback,
      startNegative, useHint, selectHop, isSelected, openEvidence, hopOf, rerunFromHop, askAgain, startEdit, saveEdit, cancelEdit, supplyKnowledge,
      noAnswerReason, toggleCopyMenu, closeCopyMenu, toggleHopMenu, closeHopMenu, copyValue, applyWizardAndSend, linkPending, hopClass,
      citationsFor, citeSnippet, confidenceClass, confidenceTag, metaLine, railAria, railLabel, railMeta, routeLabel,
      T, fmt, UiPolicy, MessageRole, ClarifyKind, QueryStatus, QueryRoute, WizardStep, RELATION_CATALOG, WELCOME_GROUPS,
    };
  },

  template: `
<div class="page">
  <div class="page__scroll" ref="scrollEl"><div class="page__col" :class="{ 'page__col--center': !pages.length }">
    ${WELCOME_TEMPLATE}
    ${PAGE_TEMPLATE.replace('${THREAD_ROWS}', THREAD_ROWS_TEMPLATE)}
  </div></div>
  ${COMPOSER_TEMPLATE}
</div>
${INSPECTOR_TEMPLATE}
${WIZARD_TEMPLATE}`,
};
