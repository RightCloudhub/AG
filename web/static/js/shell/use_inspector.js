// @ts-check
/**
 * Inspector state (§4.5). Module-level so the shell's shortcuts (E, [ ]) and the chat view share it:
 * the selected answer, the hop filter (the rail's selection), the sort, the highlighted evidence, the
 * SSE log, and the panel's fold and width.
 */
import { ref, computed, nextTick, shallowRef } from '../../vendor/vue.esm-browser.prod.js';
import { EvidenceSort, InspectorTab, KeyboardKey, MessageRole, UiPolicy } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { evidenceOrigin, hopClass, hopCounts, hopOfEvidence, hopTitle, stepHop } from '../chat/hoprail.js';
import { buildSubgraph, edgesCitedBy, isGraphEvidence } from '../chat/subgraph.js';
import { routeLabel } from '../chat/presenters.js';
import { setGraphFocus } from '../store/graph_focus.js';
import { preferences, setPreferences } from '../store/preferences.js';
import { closeInspector, inspectorOpen, openInspector } from '../store/shell.js';

/** @template T @typedef {import('../../vendor/vue.esm-browser.prod.js').Ref<T>} Ref */
/** @typedef {import('../responses.js').Answer} Answer */
/** @typedef {import('../responses.js').Evidence} Evidence */
/** @typedef {import('../chat/types.js').LogFrame} LogFrame */

/** @type {Ref<Answer | null>} */
export const inspAnswer = ref(null);
export const inspQuestion = ref('');
/** @type {Ref<import('../contracts.js').InspectorTabName>} */
export const inspTab = ref(InspectorTab.EVIDENCE);
/** 0 = every hop (§5.1: clicking a node filters the inspector to that hop). */
export const inspHop = ref(0);
/** @type {Ref<import('../contracts.js').EvidenceSortName>} */
export const inspSort = ref(EvidenceSort.SCORE);
export const inspHighlight = ref('');
/** @type {Ref<LogFrame[]>} */
export const inspLog = ref([]);

/** DOM id of the evidence card at catalog position `index`. @param {number} index */
export function evidenceDomId(index) { return `insp-ev-${index}`; }
const overlayQuery = () => window.matchMedia(`(max-width: ${UiPolicy.WIDE_PX - 1}px)`).matches;

/** E: fold the panel on a wide screen, open or close the drawer below 1440px. */
export function toggleInspector() {
  if (overlayQuery()) { if (inspectorOpen.value) closeInspector(); else openInspector(); return; }
  setPreferences({ inspectorCollapsed: !preferences.value.inspectorCollapsed });
}
/** [ and ]: move the selected hop. @param {number} delta */
export function stepInspectorHop(delta) {
  const hops = inspAnswer.value ? [...new Set(inspAnswer.value.steps.map(s => s.hop))].sort((a, b) => a - b) : [];
  inspHop.value = stepHop(hops, inspHop.value, delta);
}
/** @param {number} px */
export function applyInspectorWidth(px) { document.documentElement.style.setProperty('--insp-w', `${px}px`); }

/** Show the last answer of a notebook (when it is opened from the column or the palette), or nothing. @param {import('../chat/types.js').Conversation | null} conversation */
export function inspSyncTo(conversation) {
  const messages = conversation?.messages ?? [];
  const at = messages.map((m, i) => (m.role === MessageRole.ASSISTANT && m.data ? i : -1)).filter(i => i >= 0).pop() ?? -1;
  const last = at >= 0 ? messages[at] : null;
  inspHighlight.value = '';
  inspHop.value = 0;
  if (!last?.data) { inspAnswer.value = null; inspQuestion.value = ''; inspLog.value = []; return; }
  const asked = messages.slice(0, at).reverse().find(m => m.role === MessageRole.USER);
  inspAnswer.value = last.data;
  inspQuestion.value = asked?.text ?? last.data.question;
  inspLog.value = last.log ?? [];
}

export function useInspector() {
  /** @type {Ref<Record<string, boolean>>} */
  const inspExpanded = ref({});
  const inspResizing = ref(false);
  const inspLogCopied = ref(false);
  /** @type {Ref<import('../../vendor/vue.esm-browser.prod.js').Component | null>} */
  const inspGraphView = shallowRef(null);
  const inspGraphKey = ref(0);
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let flashTimer;

  const inspHopById = computed(() => (inspAnswer.value ? hopOfEvidence(inspAnswer.value) : new Map()));
  const inspCounts = computed(() => (inspAnswer.value ? hopCounts(inspAnswer.value) : new Map()));
  const inspHops = computed(() => [...inspCounts.value.keys()].sort((a, b) => a - b));
  const inspSubgraph = computed(() => (inspAnswer.value ? buildSubgraph(inspAnswer.value.evidence) : { nodes: [], edges: [] }));
  const inspHotEdges = computed(() => edgesCitedBy(inspSubgraph.value, inspHighlight.value ? [inspHighlight.value] : []));
  const inspHotNodes = computed(() => new Set(inspSubgraph.value.edges.filter(e => inspHotEdges.value.has(e.id)).flatMap(e => [e.head, e.tail])));
  const inspTabs = computed(() => Object.values(InspectorTab)
    .filter(id => id !== InspectorTab.SUBGRAPH || inspSubgraph.value.edges.length > 0)
    .map(id => ({ id, label: T.inspector.tabs[id] })));
  const inspRouteLabel = computed(() => (inspAnswer.value ? routeLabel(inspAnswer.value.route) : ''));
  /** Catalog rows with their hop, filtered by the selected hop and sorted (§4.5). */
  const inspRows = computed(() => {
    const answer = inspAnswer.value;
    if (!answer) return [];
    const labels = new Map(answer.steps.map(s => [s.hop, hopTitle(s)]));
    const rows = answer.evidence.map((ev, i) => {
      const hop = inspHopById.value.get(ev.id) ?? 0;
      return { index: i + 1, ev, hop, hopClass: hopClass(hop), hopLabel: labels.get(hop) ?? '', graph: isGraphEvidence(ev), source: sourceText(ev) };
    }).filter(r => inspHop.value === 0 || r.hop === inspHop.value);
    return inspSort.value === EvidenceSort.HOP
      ? rows.sort((a, b) => (a.hop || Number.MAX_SAFE_INTEGER) - (b.hop || Number.MAX_SAFE_INTEGER) || b.ev.score - a.ev.score)
      : rows.sort((a, b) => b.ev.score - a.ev.score);
  });

  /** The source line, read from the id (§10: hidden when the id says nothing). @param {Evidence} ev */
  function sourceText(ev) {
    const origin = evidenceOrigin(ev.id);
    if (!origin) return '';
    if (origin.kind === 'doc') return fmt(T.inspector.sourceDoc, { doc: origin.doc, n: origin.chunk });
    return origin.relation ? fmt(T.inspector.sourceGraph, { relation: origin.relation }) : T.inspector.sourceGraphPlain;
  }
  /** @param {Answer} answer @param {string} question @param {LogFrame[]} [log] */
  function inspShow(answer, question, log) {
    inspAnswer.value = answer;
    inspQuestion.value = question;
    inspHighlight.value = '';
    inspHop.value = 0;
    inspExpanded.value = {};
    if (log) inspLog.value = log;
    if (inspTab.value === InspectorTab.SUBGRAPH && !inspSubgraph.value.edges.length) inspTab.value = InspectorTab.EVIDENCE;
  }
  /** @param {number} hop */
  function inspSelectHop(hop) { inspHop.value = inspHop.value === hop ? 0 : hop; inspTab.value = InspectorTab.EVIDENCE; }
  /** @param {string} id */
  function inspCitationIndex(id) { return (inspAnswer.value?.evidence.findIndex(e => e.id === id) ?? -1) + 1; }
  /** Open the evidence tab, scroll to the card and flash it for 1.2 s (§5.2). @param {string} id */
  function inspFocusEvidence(id) {
    inspTab.value = InspectorTab.EVIDENCE;
    inspHop.value = 0;
    inspHighlight.value = id;
    if (overlayQuery()) openInspector();
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { if (inspHighlight.value === id) inspHighlight.value = ''; }, UiPolicy.HIGHLIGHT_MS);
    const index = inspCitationIndex(id) - 1;
    nextTick(() => {
      const el = index >= 0 ? document.getElementById(evidenceDomId(index)) : null;
      if (el) { el.scrollIntoView({ block: 'nearest' }); el.focus(); }
    });
  }
  /** @param {string} id */
  function inspHighlightGraph(id) { inspTab.value = InspectorTab.SUBGRAPH; inspHighlight.value = id; }
  /** @param {string} id */
  function inspToggleExpanded(id) { inspExpanded.value = { ...inspExpanded.value, [id]: !inspExpanded.value[id] }; }
  /** The hop colour of a subgraph node: the hop that retrieved its first evidence row. @param {import('../chat/types.js').SubgraphNode} node */
  function inspNodeHopClass(node) {
    const hop = node.evidenceIds.map(id => inspHopById.value.get(id)).find(h => h !== undefined);
    return hopClass(hop ?? 0);
  }
  /** Reuse the graph view inside the subgraph tab, focused on `name`. @param {string} name */
  async function inspOpenGraph(name) {
    setGraphFocus(name);
    inspGraphKey.value += 1;
    if (!inspGraphView.value) inspGraphView.value = (await import('../admin/graph.js')).default;
  }
  function inspToggle() { toggleInspector(); }
  async function inspCopyLog() {
    const lines = inspLog.value.map(f => `${f.at} ${f.name} ${f.summary}`).join('\n');
    try { await navigator.clipboard.writeText(lines); inspLogCopied.value = true; } catch { inspLogCopied.value = false; }
    setTimeout(() => { inspLogCopied.value = false; }, UiPolicy.COPY_FLASH_MS);
  }
  /** Roving tabindex across the tabs. @param {KeyboardEvent} e */
  function inspTabKeydown(e) {
    const ids = inspTabs.value.map(t => t.id);
    const i = ids.indexOf(inspTab.value);
    const delta = e.key === KeyboardKey.ARROW_RIGHT ? 1 : e.key === KeyboardKey.ARROW_LEFT ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = ids[(i + delta + ids.length) % ids.length];
    inspTab.value = next;
    nextTick(() => document.getElementById(`insp-tab-${next}`)?.focus());
  }
  /** Drag the panel's left edge between 320 and 520px (§4.5). @param {PointerEvent} e */
  function inspResizeStart(e) {
    e.preventDefault();
    inspResizing.value = true;
    const startX = e.clientX, startW = preferences.value.inspectorWidth;
    /** @param {PointerEvent} ev */
    const move = ev => {
      const px = Math.min(UiPolicy.INSPECTOR_MAX_PX, Math.max(UiPolicy.INSPECTOR_MIN_PX, Math.round(startW + (startX - ev.clientX))));
      applyInspectorWidth(px);
      setPreferences({ inspectorWidth: px });
    };
    const up = () => { inspResizing.value = false; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  return {
    inspAnswer, inspQuestion, inspTab, inspTabs, inspHop, inspSort, inspHighlight, inspLog, inspLogCopied, inspExpanded, inspResizing,
    inspGraphView, inspGraphKey, inspHopById, inspCounts, inspHops, inspRows, inspSubgraph, inspHotEdges, inspHotNodes, inspRouteLabel,
    inspectorOpen, inspShow, inspSelectHop, inspCitationIndex, inspFocusEvidence, inspHighlightGraph, inspToggleExpanded, inspNodeHopClass,
    inspOpenGraph, inspToggle, inspCopyLog, inspTabKeydown, inspResizeStart, closeInspector, evidenceDomId, hopClass, InspectorTab, EvidenceSort,
  };
}
