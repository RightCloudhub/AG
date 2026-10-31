// @ts-check
/** Composer state: the question, @ entity autocomplete, / commands, the guided-question wizard. */
import { ref, computed, nextTick, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchEntities } from '../api.js';
import { FeatureFlag, KeyboardKey, MentionTrigger, QueryMode, SlashCommand, SlashTrigger, UiPolicy, WizardStep } from '../contracts.js';
import { text as T } from '../i18n.js';
import { preferences } from '../store/preferences.js';
import {
  EntityTypeLabel, canAdvance, composeQuestion, draftSubQuestions, gotoStep, initialWizard,
  moveRelation, pushRelation, relationById, relationsFor, removeRelation, startEntity,
} from './wizard.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../responses.js').Entity} Entity */
/** @typedef {import('./types.js').WizardState} WizardState */
/** @typedef {{open: boolean, query: string, start: number, items: Entity[], active: number}} MentionState */
/** @typedef {{id: import('../contracts.js').SlashCommandName, label: string, icon: string}} SlashItem */
/** @typedef {{open: boolean, query: string, start: number, items: SlashItem[], active: number}} SlashState */

const LINE_HEIGHT_PX = 22.5;
/** @type {SlashItem[]} */
const SLASH_ITEMS = [
  { id: SlashCommand.WIZARD, label: T.commands.wizard, icon: 'guide' },
  { id: SlashCommand.PLAN, label: T.commands.plan, icon: 'plan' },
  { id: SlashCommand.IMPORT, label: T.commands.import, icon: 'upload' },
];
/** @returns {MentionState} */
const closedMention = () => ({ open: false, query: '', start: -1, items: [], active: 0 });
/** @returns {SlashState} */
const closedSlash = () => ({ open: false, query: '', start: -1, items: [], active: 0 });

/** @param {string} query */
async function lookup(query) {
  if (query.trim().length < UiPolicy.AUTOCOMPLETE_MIN_CHARS) return [];
  try { return (await fetchEntities({ query: query.trim(), limit: UiPolicy.AUTOCOMPLETE_LIMIT })).data; }
  catch { return []; }
}
/** The trigger character before the caret, at a word boundary, on the same line. @param {string} before @param {string} trigger */
function triggerAt(before, trigger) {
  const at = before.lastIndexOf(trigger);
  if (at === -1 || before.slice(at).includes('\n')) return -1;
  return at === 0 || /\s/.test(before[at - 1]) ? at : -1;
}

export function useComposer() {
  const question = ref('');
  /** @type {Ref<HTMLTextAreaElement | null>} */
  const inputEl = ref(null);
  /** @type {Ref<MentionState>} */
  const mention = ref(closedMention());
  /** @type {Ref<SlashState>} */
  const slash = ref(closedSlash());
  const wizardOpen = ref(false);
  /** @type {Ref<WizardState>} */
  const wizard = ref(initialWizard());
  /** @type {Ref<Entity[]>} */
  const wizardSuggestions = ref([]);
  /** @type {Ref<Entity[]>} */
  const intersectSuggestions = ref([]);
  /** @type {Ref<HTMLInputElement | null>} */
  const wizardFirstEl = ref(null);
  /** @type {HTMLElement | null} */
  let lastFocus = null;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let timer;
  onUnmounted(() => clearTimeout(timer));

  const mode = computed(() => preferences.value.mode);
  const planUnavailable = computed(() => mode.value === QueryMode.PLAN_CONFIRM && !FeatureFlag.PLAN_PREVIEW);
  const wizardQuestion = computed(() => composeQuestion(wizard.value));
  const wizardRelations = computed(() => relationsFor(wizard.value));
  const wizardCanAdvance = computed(() => canAdvance(wizard.value) || wizardSuggestions.value.length > 0);
  const chainLabels = computed(() => wizard.value.chain.map(id => relationById(id)?.label ?? id));
  const startName = computed(() => startEntity(wizard.value).name);
  const wizardDraftLabels = computed(() => draftSubQuestions(wizard.value).map(q => q.replace(/\{(\d+)\}/g, (_, n) => `[${T.wizard.prevResult} ${n}]`)));

  /** The request for the current mode and hop budget (§4.2). @param {string} q @returns {import('../contracts.js').QueryRequest} */
  function requestFor(q) {
    const body = { question: q, max_hops: preferences.value.maxHops };
    return mode.value === QueryMode.AUTO ? body : { ...body, force_agentic: true };
  }
  function focusInput() { nextTick(() => inputEl.value?.focus()); }
  function autosize() {
    const el = inputEl.value;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, LINE_HEIGHT_PX * UiPolicy.COMPOSER_MAX_LINES)}px`;
  }
  /** @param {string} value */
  function setQuestion(value) { question.value = value; focusInput(); nextTick(autosize); }
  /** @param {string} type */
  function typeLabel(type) { return EntityTypeLabel[/** @type {keyof typeof EntityTypeLabel} */ (type)] ?? type; }

  /* ── @ entities and / commands ──────────────────────────────────── */
  function onInput() { autosize(); detectMenus(); }
  function closeMenus() {
    if (mention.value.open) mention.value = closedMention();
    if (slash.value.open) slash.value = closedSlash();
  }
  function detectMenus() {
    const caret = inputEl.value?.selectionStart ?? question.value.length;
    const before = question.value.slice(0, caret);
    const at = triggerAt(before, MentionTrigger), sl = triggerAt(before, SlashTrigger);
    if (sl !== -1 && sl >= at) {
      const query = before.slice(sl + 1).toLowerCase();
      const items = SLASH_ITEMS.filter(c => !query || c.id.startsWith(query) || c.label.includes(query));
      mention.value = closedMention();
      slash.value = { open: true, query, start: sl, items, active: 0 };
      return;
    }
    if (at === -1) { closeMenus(); return; }
    const query = before.slice(at + 1);
    slash.value = closedSlash();
    mention.value = { ...mention.value, open: true, query, start: at, active: 0 };
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const items = await lookup(query);
      if (mention.value.open && mention.value.query === query) mention.value = { ...mention.value, items };
    }, UiPolicy.SEARCH_DEBOUNCE_MS);
  }
  /** @param {Entity} entity */
  function pickMention(entity) {
    const caret = inputEl.value?.selectionStart ?? question.value.length;
    question.value = `${question.value.slice(0, mention.value.start)}${entity.name} ${question.value.slice(caret)}`;
    closeMenus();
    focusInput();
  }
  /** Remove the typed command and return its id for the view to act on. @param {SlashItem} item */
  function pickSlash(item) {
    const caret = inputEl.value?.selectionStart ?? question.value.length;
    question.value = `${question.value.slice(0, slash.value.start)}${question.value.slice(caret)}`.trimStart();
    closeMenus();
    nextTick(autosize);
    return item.id;
  }
  /**
   * Keyboard for whichever menu is open; returns the picked slash command, `true` when handled, else `false`.
   * @param {KeyboardEvent} e @returns {boolean | import('../contracts.js').SlashCommandName}
   */
  function menuKeydown(e) {
    const m = mention.value.open ? mention.value : slash.value.open ? slash.value : null;
    if (!m) return false;
    if (e.key === KeyboardKey.ESCAPE) { closeMenus(); return true; }
    if (!m.items.length) return false;
    const set = /** @param {number} active */ active => { if (mention.value.open) mention.value = { ...mention.value, active }; else slash.value = { ...slash.value, active }; };
    if (e.key === KeyboardKey.ARROW_DOWN) { set((m.active + 1) % m.items.length); return true; }
    if (e.key === KeyboardKey.ARROW_UP) { set((m.active - 1 + m.items.length) % m.items.length); return true; }
    if (e.key === KeyboardKey.ENTER || e.key === KeyboardKey.TAB) {
      if (mention.value.open) { pickMention(mention.value.items[mention.value.active]); return true; }
      return pickSlash(slash.value.items[slash.value.active]);
    }
    return false;
  }

  /* ── wizard ─────────────────────────────────────────────────────── */
  function openWizard() {
    lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    wizard.value = initialWizard();
    wizardSuggestions.value = [];
    wizardOpen.value = true;
    nextTick(() => wizardFirstEl.value?.focus());
  }
  function closeWizard() { wizardOpen.value = false; (lastFocus ?? inputEl.value)?.focus(); }
  function focusWizard() {
    nextTick(() => {
      const el = document.querySelector('.dialog__body input, .dialog__body select, .dialog__body textarea, .dialog__body button');
      if (el instanceof HTMLElement) el.focus();
    });
  }
  function wizardNext() {
    const state = wizard.value;
    if (state.step === WizardStep.ENTITY && !state.entity && wizardSuggestions.value.length) chooseWizardEntity(wizardSuggestions.value[0]);
    if (!canAdvance(wizard.value)) return;
    if (wizard.value.step === WizardStep.PREVIEW) { applyWizard(); return; }
    wizard.value = gotoStep(wizard.value, wizard.value.step + 1);
    focusWizard();
  }
  function wizardBack() { wizard.value = gotoStep(wizard.value, wizard.value.step - 1); }
  function wizardSkip() {
    if (wizard.value.step === WizardStep.ENTITY || wizard.value.step === WizardStep.PREVIEW) { closeWizard(); return; }
    wizard.value = gotoStep(wizard.value, wizard.value.step + 1);
  }
  /** @param {KeyboardEvent} e */
  function wizardKeydown(e) {
    if (e.key === KeyboardKey.ESCAPE) { e.preventDefault(); wizardSkip(); return; }
    if (e.key === KeyboardKey.ENTER && !e.shiftKey && !(e.target instanceof HTMLTextAreaElement) && !(e.target instanceof HTMLButtonElement)) { e.preventDefault(); wizardNext(); }
  }
  /** @param {string} value */
  function setWizardQuery(value) {
    wizard.value = { ...wizard.value, entityQuery: value, entity: null };
    clearTimeout(timer);
    timer = setTimeout(async () => { wizardSuggestions.value = await lookup(value); }, UiPolicy.SEARCH_DEBOUNCE_MS);
  }
  /** @param {Entity} entity */
  function chooseWizardEntity(entity) {
    wizard.value = { ...wizard.value, entity: { name: entity.name, type: entity.type }, entityQuery: entity.name };
    wizardSuggestions.value = [];
  }
  function useTypedEntity() {
    wizard.value = { ...wizard.value, entity: { name: wizard.value.entityQuery.trim(), type: '' } };
    wizardSuggestions.value = [];
  }
  /** @param {string} id */
  function addRelation(id) { wizard.value = pushRelation(wizard.value, id); }
  /** @param {number} i */
  function dropRelation(i) { wizard.value = removeRelation(wizard.value, i); }
  /** @param {number} i @param {number} delta */
  function shiftRelation(i, delta) { wizard.value = moveRelation(wizard.value, i, delta); }
  /** @param {Partial<WizardState>} patch */
  function setConstraint(patch) { wizard.value = { ...wizard.value, ...patch, override: '' }; }
  /** @param {string} relation */
  function setIntersectRelation(relation) { setConstraint({ intersect: relation ? { relation, entity: wizard.value.intersect?.entity ?? '' } : null }); }
  /** @param {string} entity */
  function setIntersectEntity(entity) {
    setConstraint({ intersect: { relation: wizard.value.intersect?.relation ?? '', entity } });
    clearTimeout(timer);
    timer = setTimeout(async () => { intersectSuggestions.value = await lookup(entity); }, UiPolicy.SEARCH_DEBOUNCE_MS);
  }
  /** @param {string} value */
  function setOverride(value) { wizard.value = { ...wizard.value, override: value }; }
  function applyWizard() { setQuestion(wizardQuestion.value); closeWizard(); }

  return {
    question, inputEl, mention, slash, wizardOpen, wizard, wizardSuggestions, intersectSuggestions, wizardFirstEl, mode,
    planUnavailable, wizardQuestion, wizardRelations, wizardCanAdvance, chainLabels, startName, wizardDraftLabels,
    requestFor, focusInput, autosize, setQuestion, typeLabel, onInput, closeMenus, pickMention, pickSlash, menuKeydown,
    openWizard, closeWizard, wizardNext, wizardBack, wizardSkip, wizardKeydown, setWizardQuery, chooseWizardEntity,
    useTypedEntity, addRelation, dropRelation, shiftRelation, setConstraint, setIntersectRelation, setIntersectEntity,
    setOverride, applyWizard,
  };
}
