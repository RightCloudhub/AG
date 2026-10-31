// @ts-check
/** Clarification-card interactions (entity pick / broad question / missing info). */
import { ref } from '../../vendor/vue.esm-browser.prod.js';
import { ClarifyKind } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { applyEntityChoice, applyMissingChoice } from './clarify.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('./types.js').ChatMessage} ChatMessage */
/** @typedef {import('./types.js').ClarifyOption} ClarifyOption */
/**
 * @typedef {object} ClarifyDeps
 * @property {(index: number, patch: Partial<ChatMessage>) => void} patch
 * @property {(question: string) => void} startStream
 * @property {(question: string) => void} setQuestion
 * @property {() => void} openWizard
 */

/** @param {ClarifyDeps} deps */
export function useClarify(deps) {
  /** @type {Ref<Record<number, string[]>>} */
  const picks = ref({});

  /** @param {ChatMessage} msg */
  function clarifyTitle(msg) {
    const card = msg.clarify;
    if (!card) return '';
    if (card.kind === ClarifyKind.ENTITY) return fmt(T.clarify.entityTitle, { mention: card.mention });
    return card.kind === ClarifyKind.BROAD ? T.clarify.broadTitle : T.clarify.missingTitle;
  }
  /** @param {number} index @param {string} id */
  function isPicked(index, id) { return (picks.value[index] ?? []).includes(id); }
  /** @param {ChatMessage} msg @param {number} index @param {ClarifyOption} option */
  function pickClarify(msg, index, option) {
    const card = msg.clarify;
    if (!card || msg.resolved) return;
    if (card.multi) {
      const current = picks.value[index] ?? [];
      const next = current.includes(option.id) ? current.filter(id => id !== option.id) : [...current, option.id];
      picks.value = { ...picks.value, [index]: next };
      return;
    }
    deps.patch(index, { resolved: true });
    deps.startStream(applyEntityChoice(card, option));
  }
  /** @param {ChatMessage} msg @param {number} index */
  function confirmClarify(msg, index) {
    const card = msg.clarify;
    if (!card || msg.resolved) return;
    deps.patch(index, { resolved: true });
    deps.startStream(applyMissingChoice(card, card.options.filter(o => isPicked(index, o.id))));
  }
  /** @param {number} index */
  function skipClarify(index) { deps.patch(index, { resolved: true }); }
  /** @param {ChatMessage} msg @param {number} index @param {'wizard' | 'send'} action */
  function resolveBroad(msg, index, action) {
    const card = msg.clarify;
    if (!card || msg.resolved) return;
    deps.patch(index, { resolved: true });
    if (action === 'send') { deps.startStream(card.question); return; }
    deps.setQuestion(card.question);
    deps.openWizard();
  }

  return { clarifyTitle, isPicked, pickClarify, confirmClarify, skipClarify, resolveBroad };
}
