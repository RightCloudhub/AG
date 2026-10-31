// @ts-check
/**
 * Guided-question wizard: pure state helpers plus the relation catalog used to
 * compose an English natural-language question (the demo corpus is English).
 * The catalog mirrors configs/schema/domain_v0.yaml; when FeatureFlag.RELATION_CATALOG
 * ships it should be replaced by GET /v1/graph/relations.
 */
import { ConstraintKind, UiPolicy, WizardStep } from '../contracts.js';

/** @typedef {import('./types.js').RelationOption} RelationOption */
/** @typedef {import('./types.js').WizardState} WizardState */

export const EntityTypeLabel = Object.freeze({
  Company: '公司', Person: '人物', Product: '产品', Event: '事件', Location: '地点',
});
const ALL_TYPES = Object.keys(EntityTypeLabel);

/**
 * @param {string} relation @param {boolean} inverse @param {string} label @param {string[]} from
 * @param {string} to @param {boolean} many @param {(x: string) => string} phrase
 * @param {string} verb reads as "<subject> <verb> <entity>" with the subject on the `from` side
 * @returns {RelationOption}
 */
function option(relation, inverse, label, from, to, many, phrase, verb) {
  return { id: `${relation}${inverse ? '~inv' : ''}`, relation, inverse, label, from, to, many, phrase, verb };
}

/** @type {readonly RelationOption[]} */
export const RELATION_CATALOG = Object.freeze([
  option('CEO_OF', true, 'CEO', ['Company'], 'Person', false, x => `the CEO of ${x}`, 'are led by'),
  option('CEO_OF', false, '担任 CEO 的公司', ['Person'], 'Company', false, x => `the company led by ${x}`, 'lead'),
  option('SUBSIDIARY_OF', false, '母公司', ['Company'], 'Company', false, x => `the parent company of ${x}`, 'are subsidiaries of'),
  option('PARENT_OF', false, '子公司', ['Company'], 'Company', true, x => `the subsidiaries of ${x}`, 'own'),
  option('WORKED_AT', false, '曾任职公司', ['Person'], 'Company', true, x => `the companies where ${x} previously worked`, 'previously worked at'),
  option('WORKED_AT', true, '前员工', ['Company'], 'Person', true, x => `the people who previously worked at ${x}`, 'previously employed'),
  option('WORKS_AT', false, '现任职公司', ['Person'], 'Company', false, x => `the company where ${x} currently works`, 'currently work at'),
  option('FOUNDED', false, '创立的公司', ['Person'], 'Company', true, x => `the companies founded by ${x}`, 'founded'),
  option('FOUNDED', true, '创始人', ['Company'], 'Person', false, x => `the founder of ${x}`, 'were founded by'),
  option('PRODUCES', false, '产品', ['Company'], 'Product', true, x => `the products of ${x}`, 'produce'),
  option('PRODUCES', true, '生产商', ['Product'], 'Company', false, x => `the producer of ${x}`, 'are produced by'),
  option('COMPETES_WITH', false, '竞争对手', ['Company'], 'Company', true, x => `the competitors of ${x}`, 'compete with'),
  option('SUPPLIES', true, '供应商', ['Company'], 'Company', true, x => `the suppliers of ${x}`, 'are supplied by'),
  option('SUPPLIES', false, '供应的客户', ['Company'], 'Company', true, x => `the companies supplied by ${x}`, 'supply'),
  option('SUPPLIES_FOR', true, '零部件供应商', ['Product'], 'Company', true, x => `the suppliers of ${x}`, 'are supplied by'),
  option('LOCATED_IN', false, '所在地', ['Company', 'Person', 'Event'], 'Location', false, x => `the location of ${x}`, 'are located in'),
  option('LOCATED_IN', true, '位于该地的公司', ['Location'], 'Company', true, x => `the companies headquartered in ${x}`, 'are home to'),
  option('PARTICIPATED_IN', false, '参与的事件', ['Company', 'Person'], 'Event', true, x => `the events ${x} participated in`, 'participated in'),
  option('PARTICIPATED_IN', true, '参与方', ['Event'], 'Company', true, x => `the participants of ${x}`, 'involved'),
  option('ACQUIRED', false, '收购的公司', ['Company'], 'Company', true, x => `the companies acquired by ${x}`, 'acquired'),
  option('ACQUIRED', true, '收购方', ['Company'], 'Company', false, x => `the acquirer of ${x}`, 'were acquired by'),
  option('PARTNER_OF', false, '合作伙伴', ['Company'], 'Company', true, x => `the partners of ${x}`, 'partner with'),
]);

/** @returns {WizardState} */
export function initialWizard() {
  return {
    step: WizardStep.ENTITY, entity: null, entityQuery: '', chain: [], time: '', count: false,
    intersect: null, override: '',
  };
}

/** @param {string} id */
export function relationById(id) {
  return RELATION_CATALOG.find(r => r.id === id) ?? null;
}

/** Options that can follow the current chain end (or the start entity type). @param {WizardState} state */
export function relationsFor(state) {
  const endType = chainEndType(state);
  if (!endType || !ALL_TYPES.includes(endType)) return RELATION_CATALOG;
  return RELATION_CATALOG.filter(r => r.from.includes(endType));
}

/** @param {WizardState} state */
export function chainEndType(state) {
  const last = state.chain.length ? relationById(state.chain[state.chain.length - 1]) : null;
  return last ? last.to : state.entity?.type ?? '';
}

/** @param {WizardState} state @param {string} relationId @returns {WizardState} */
export function pushRelation(state, relationId) {
  if (state.chain.length >= UiPolicy.WIZARD_MAX_RELATIONS || !relationById(relationId)) return state;
  return { ...state, chain: [...state.chain, relationId], override: '' };
}

/** @param {WizardState} state @param {number} index @returns {WizardState} */
export function removeRelation(state, index) {
  return { ...state, chain: state.chain.filter((_, i) => i !== index), override: '' };
}

/** @param {WizardState} state @param {number} index @param {number} delta @returns {WizardState} */
export function moveRelation(state, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= state.chain.length) return state;
  const chain = [...state.chain];
  [chain[index], chain[target]] = [chain[target], chain[index]];
  return { ...state, chain, override: '' };
}

/** @param {WizardState} state @param {number} step @returns {WizardState} */
export function gotoStep(state, step) {
  const bounded = Math.max(WizardStep.ENTITY, Math.min(WizardStep.PREVIEW, step));
  return { ...state, step: bounded };
}

/** @param {WizardState} state */
export function canAdvance(state) {
  if (state.step === WizardStep.ENTITY) return Boolean(state.entity?.name || state.entityQuery.trim());
  if (state.step === WizardStep.RELATIONS) return state.chain.length > 0;
  return true;
}

/** Resolve the start entity, falling back to the typed name. @param {WizardState} state */
export function startEntity(state) {
  return state.entity ?? { name: state.entityQuery.trim(), type: '' };
}

/** Draft sub-questions, one per relation, in execution order. @param {WizardState} state */
export function draftSubQuestions(state) {
  const start = startEntity(state).name;
  return state.chain.map((id, i) => {
    const rel = relationById(id);
    if (!rel) return '';
    const subject = i === 0 ? start : `{${i}}`;
    return `${questionWord(rel)} ${rel.phrase(subject)}?`;
  });
}

/** @param {RelationOption} rel */
function questionWord(rel) {
  if (rel.to === 'Person') return rel.many ? 'Who are' : 'Who is';
  return rel.many ? 'What are' : 'What is';
}

/** Compose the natural-language question for the current state. @param {WizardState} state */
export function composeQuestion(state) {
  if (state.override.trim()) return state.override.trim();
  const start = startEntity(state).name;
  if (!start) return '';
  const relations = state.chain.map(relationById).filter(
    /** @returns {r is RelationOption} */ r => r !== null,
  );
  if (!relations.length) return `What do we know about ${start}?`;
  const phrase = relations.reduce((acc, rel) => rel.phrase(acc), start);
  const last = relations[relations.length - 1];
  let question = `${questionWord(last)} ${phrase}`;
  if (state.count) question = `How many ${phrase.replace(/^the\s+/, '')} are there`;
  if (state.intersect?.relation && state.intersect.entity.trim()) {
    const extra = relationById(state.intersect.relation);
    if (extra) question = `Among ${phrase}, which also ${extra.verb} ${state.intersect.entity.trim()}`;
  }
  if (state.time.trim()) question = `${question} as of ${state.time.trim()}`;
  return `${question}?`;
}

/** Constraint kinds shown in step 3 (order matters for the UI). */
export const CONSTRAINT_ORDER = Object.freeze([ConstraintKind.TIME, ConstraintKind.COUNT, ConstraintKind.INTERSECT]);
