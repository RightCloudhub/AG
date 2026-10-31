// @ts-check
/**
 * Clarification cards. Ambiguity is detected against the real entity index
 * (GET /v1/graph/entities); broad questions and partial answers use the
 * chain's own `missing_info`. Nothing here invents backend fields.
 */
import { ClarifyKind, UiPolicy, QueryStatus } from '../contracts.js';

/** @typedef {import('./types.js').ClarifyCard} ClarifyCard */
/** @typedef {import('./types.js').ClarifyOption} ClarifyOption */
/** @typedef {import('../responses.js').Entity} Entity */
/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {(query: string) => Promise<Entity[]>} EntityLookup */

const QUESTION_WORDS = new Set([
  'Who', 'Which', 'What', 'Where', 'When', 'Why', 'How', 'Did', 'Does', 'Do', 'Is', 'Are', 'Was', 'Were',
  'Among', 'Find', 'List', 'Show', 'Tell', 'Give', 'Name', 'Between', 'And', 'Or', 'The', 'Of', 'In', 'At',
]);
const CAPITALISED_RUN = /\b([A-Z][\w-]*(?:\s+[A-Z][\w-]*)*)\b/g;
const MENTION = /@([^\s@?,.;!？，。]+(?:\s+[A-Z][\w-]*)*)/g;
const QUESTION_MARKER = /[?？]|\b(who|which|what|where|when|how)\b|谁|哪|什么|吗/i;

/** Candidate entity mentions in question order, de-duplicated. @param {string} question */
export function extractMentions(question) {
  /** @type {string[]} */
  const found = [];
  for (const match of question.matchAll(MENTION)) found.push(match[1].trim());
  for (const match of question.matchAll(CAPITALISED_RUN)) {
    const words = match[1].split(/\s+/).filter(w => !QUESTION_WORDS.has(w));
    if (words.length) found.push(words.join(' '));
  }
  return [...new Set(found)].slice(0, UiPolicy.CLARIFY_MAX_ENTITIES);
}

/** @param {Entity} entity @returns {ClarifyOption} */
function optionFor(entity) {
  return { id: entity.id, name: entity.name, type: entity.type, label: entity.type ? `${entity.name} · ${entity.type}` : entity.name };
}

/**
 * Entity disambiguation: a mention matching several distinct graph entities.
 * @param {string} question @param {EntityLookup} lookup @returns {Promise<ClarifyCard | null>}
 */
export async function detectEntityAmbiguity(question, lookup) {
  for (const mention of extractMentions(question)) {
    const needle = mention.toLowerCase();
    const matches = (await lookup(mention)).filter(e =>
      e.name.toLowerCase() === needle || e.aliases.some(a => a.toLowerCase() === needle));
    const distinct = [...new Map(matches.map(e => [e.id, e])).values()];
    if (distinct.length > 1) {
      return { kind: ClarifyKind.ENTITY, mention, options: distinct.map(optionFor), multi: false, question };
    }
  }
  return null;
}

/** Broad question: short, clearly a question, and naming no entity (greetings pass through). @param {string} question */
export function detectBroadQuestion(question) {
  const trimmed = question.trim();
  if (trimmed.length >= UiPolicy.CLARIFY_MIN_QUESTION_CHARS || !QUESTION_MARKER.test(trimmed)) return null;
  if (extractMentions(trimmed).length) return null;
  return { kind: ClarifyKind.BROAD, mention: '', options: [], multi: false, question: trimmed };
}

/** Missing-information card for partial / no-answer chains. @param {Answer} answer @param {string} question */
export function missingInfoCard(answer, question) {
  const partial = answer.status === QueryStatus.PARTIAL || answer.status === QueryStatus.NO_ANSWER;
  if (!partial || !answer.missing_info.length) return null;
  /** @type {ClarifyOption[]} */
  const options = answer.missing_info.map((item, i) => ({ id: `m${i}`, name: item, type: '', label: item }));
  return { kind: ClarifyKind.MISSING, mention: '', options, multi: true, question };
}

/** Rewrite the question with the chosen entity (name + type for the planner). @param {ClarifyCard} card @param {ClarifyOption} chosen */
export function applyEntityChoice(card, chosen) {
  const replacement = chosen.type ? `${chosen.name} (${chosen.type})` : chosen.name;
  return card.question.split(card.mention).join(replacement).replace(/@/g, '');
}

/** Append the selected missing items as explicit asks. @param {ClarifyCard} card @param {ClarifyOption[]} chosen */
export function applyMissingChoice(card, chosen) {
  if (!chosen.length) return card.question;
  return `${card.question.replace(/\?\s*$/, '')}? Please also cover: ${chosen.map(c => c.name).join('; ')}.`;
}
