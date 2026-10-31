// @ts-check
/** Entity the graph view should focus on when opened from a chat citation. */
import { ref } from '../../vendor/vue.esm-browser.prod.js';

/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<string>} */
export const graphFocus = ref('');

/** @param {string} name */
export function setGraphFocus(name) {
  graphFocus.value = name.trim();
}
