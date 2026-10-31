// @ts-check
/**
 * The one icon set. The brief fixes it (Prompt.md §3.3: Lucide, 16px, 1.5 stroke, currentColor), so
 * every glyph the workspace draws is real Lucide geometry: the vendored subset in
 * ../vendor/lucide-icons.js, written by scripts/build_lucide_icons.mjs. Icons render through the
 * global <ag-icon name size> component; no icon font, no runtime library, no emoji.
 */
import { computed } from '../vendor/vue.esm-browser.prod.js';
import { ICON_IDS, LUCIDE } from '../vendor/lucide-icons.js';

/** One icon's nodes, each its own [tag, attributes] on the 24-unit grid. */
/** @typedef {ReadonlyArray<readonly [string, Readonly<Record<string, string>>]>} IconNodes */
/** @typedef {Record<string, IconNodes>} IconSet */

/**
 * The product's own mark: three hops joined by two links. The one glyph that is not Lucide, drawn
 * here the way the reference demo hand-draws its own brand mark beside the library icons.
 * @type {IconSet}
 */
const BRAND = {
  mark: [
    ['circle', { cx: '5', cy: '17', r: '2' }],
    ['circle', { cx: '12', cy: '11', r: '2' }],
    ['circle', { cx: '19', cy: '5', r: '2' }],
    ['path', { d: 'M6.6 15.6l3.8-3.2' }],
    ['path', { d: 'M13.6 9.6l3.8-3.2' }],
  ],
};

/** The vendored Lucide geometry, indexed by Lucide id (typed by its generator). */
const GEOMETRY = LUCIDE;

/** Every icon the workspace can draw: the name it is asked for, then the nodes to draw. @type {IconSet} */
export const ICONS = Object.freeze({
  ...BRAND,
  ...Object.fromEntries(Object.entries(ICON_IDS).map(([name, id]) => [name, GEOMETRY[id] ?? []])),
});

export const AgIcon = {
  name: 'AgIcon',
  props: { name: { type: String, required: true }, size: { type: Number, default: 16 } },
  /** @param {{name: string, size: number}} props */
  setup(props) {
    const nodes = computed(() => ICONS[props.name] ?? []);
    return { nodes };
  },
  template: `<svg class="icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><component
  v-for="(n, i) in nodes" :key="i" :is="n[0]" v-bind="n[1]"></component></svg>`,
};
