// @ts-check
/** Shell state shared by the frame and the views: the phone notebook sheet, the inspector drawer, the palette. */
import { ref } from '../../vendor/vue.esm-browser.prod.js';

/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<boolean>} */
export const notebookOpen = ref(false);
/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<boolean>} */
export const inspectorOpen = ref(false);
/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<boolean>} */
export const paletteOpen = ref(false);

export function openNotebook() { notebookOpen.value = true; }
export function closeNotebook() { notebookOpen.value = false; }
export function openInspector() { inspectorOpen.value = true; }
export function closeInspector() { inspectorOpen.value = false; }
export function openPalette() { paletteOpen.value = true; }
export function closePalette() { paletteOpen.value = false; }

/** Escape and navigation close everything that floats. */
export function closeOverlays() { notebookOpen.value = false; inspectorOpen.value = false; paletteOpen.value = false; }
