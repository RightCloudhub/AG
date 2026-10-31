// @ts-check
/**
 * Follow-up suggestions derived from entities that actually appear in the
 * answer's graph evidence — templated by the entity type the graph reports.
 */
import { UiPolicy } from '../contracts.js';
import { buildSubgraph } from './subgraph.js';

/** @typedef {import('../responses.js').Answer} Answer */

/** @type {Record<string, ((name: string) => string)[]>} */
const TEMPLATES = {
  Person: [
    name => `Which companies did ${name} previously work at?`,
    name => `Which company does ${name} lead?`,
  ],
  Company: [
    name => `Who is the CEO of ${name}?`,
    name => `What products does ${name} produce?`,
    name => `Which company is the parent of ${name}?`,
  ],
  Product: [
    name => `Which company produces ${name}?`,
    name => `Which suppliers provide components for ${name}?`,
  ],
  Event: [name => `Which companies participated in ${name}?`],
  Location: [name => `Which companies are headquartered in ${name}?`],
};

/** @param {string} value */
function normalise(value) {
  return value.toLowerCase().replace(/[^\w\s一-鿿]/g, '').replace(/\s+/g, ' ').trim();
}

/** @param {Answer} answer @param {string} question @returns {string[]} */
export function suggestFollowups(answer, question) {
  const graph = buildSubgraph(answer.evidence);
  const asked = normalise(question);
  const answerText = answer.answer.toLowerCase();
  const ranked = [...graph.nodes]
    .filter(node => node.type && TEMPLATES[node.type])
    .sort((a, b) => Number(answerText.includes(b.name.toLowerCase())) - Number(answerText.includes(a.name.toLowerCase()))
      || b.evidenceIds.length - a.evidenceIds.length);
  /** @type {string[]} */
  const out = [];
  for (const node of ranked) {
    for (const template of TEMPLATES[node.type]) {
      const candidate = template(node.name);
      if (normalise(candidate) === asked || asked.includes(normalise(node.name)) && !answerText.includes(node.name.toLowerCase())) continue;
      if (!out.includes(candidate)) out.push(candidate);
      if (out.length >= UiPolicy.FOLLOWUP_COUNT) return out;
    }
  }
  return out;
}
