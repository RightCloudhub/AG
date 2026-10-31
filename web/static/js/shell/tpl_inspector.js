// @ts-check
/** The inspector (§4.5): 证据 · 子图 · 日志 for the selected answer; evidence filtered by hop and sorted. */

export const INSPECTOR_TEMPLATE = `
<aside class="inspector" :class="{ 'inspector--open': inspectorOpen, 'inspector--resizing': inspResizing }" :aria-label="T.inspector.title">
  <div class="inspector__resize" @pointerdown="inspResizeStart" aria-hidden="true"></div>
  <div class="sheet-handle" aria-hidden="true"></div>
  <header class="inspector__head">
    <div class="inspector__tabs" role="tablist" :aria-label="T.inspector.title" @keydown="inspTabKeydown">
      <button v-for="t in inspTabs" :key="t.id" type="button" role="tab" :id="'insp-tab-' + t.id" class="tab"
              :aria-selected="inspTab === t.id" :aria-controls="'insp-panel-' + t.id" :tabindex="inspTab === t.id ? 0 : -1"
              @click="inspTab = t.id">{{ t.label }}</button>
    </div>
    <button type="button" class="btn btn--icon btn--ghost" @click="inspToggle" :aria-label="T.inspector.toggle" :title="T.inspector.toggle + ' (E)'">
      <ag-icon name="panelRightClose"></ag-icon></button>
  </header>
  <div class="inspector__body">
    <p v-if="!inspAnswer" class="empty">{{ T.inspector.empty }}</p>
    <section v-else-if="inspTab === InspectorTab.EVIDENCE" role="tabpanel" id="insp-panel-evidence" aria-labelledby="insp-tab-evidence">
      <div v-if="inspHops.length" class="insp-toolbar" role="group" :aria-label="T.inspector.filterAll">
        <button type="button" class="chip chip--sm" :aria-pressed="inspHop === 0" @click="inspSelectHop(0)">{{ T.inspector.filterAll }}</button>
        <button v-for="h in inspHops" :key="h" type="button" class="chip chip--sm" :class="hopClass(h)" :aria-pressed="inspHop === h"
                :aria-label="fmt(T.hoprail.focus, { n: h })" @click="inspSelectHop(h)"><span class="chip__dot" aria-hidden="true"></span>{{ fmt(T.inspector.filterHop, { n: h }) }}</button>
        <label class="sr-only" for="insp-sort">{{ T.inspector.sortLabel }}</label>
        <select id="insp-sort" class="select" :value="inspSort" @change="inspSort = $event.target.value">
          <option :value="EvidenceSort.SCORE">{{ T.inspector.sortScore }}</option>
          <option :value="EvidenceSort.HOP">{{ T.inspector.sortHop }}</option>
        </select>
      </div>
      <p v-if="!inspAnswer.evidence.length" class="empty">{{ T.inspector.evidenceEmpty }}</p>
      <template v-else>
        <p class="insp-count">{{ fmt(T.inspector.count, { n: inspRows.length, hops: inspHops.length }) }}</p>
        <div class="evlist">
          <article v-for="r in inspRows" :key="r.ev.id" :id="evidenceDomId(r.index - 1)" class="evcard" tabindex="-1"
                   :class="[r.hopClass, { 'evcard--hot': inspHighlight === r.ev.id, 'evcard--open': !!inspExpanded[r.ev.id] }]">
            <header class="evcard__head"><span class="evcard__n">[{{ r.index }}]</span>
              <span class="evcard__score" :class="{ 'evcard__score--low': r.ev.score < UiPolicy.LOW_SCORE }">{{ r.ev.score.toFixed(UiPolicy.SCORE_PRECISION) }}</span>
              <span v-if="r.hop" class="tag tag--hop evcard__hop">{{ fmt(T.inspector.filterHop, { n: r.hop }) }} · {{ r.hopLabel }}</span></header>
            <p class="evcard__text" :class="{ 'evcard__text--graph': r.graph }">{{ r.ev.content }}</p>
            <footer class="evcard__foot">
              <span v-if="r.source" class="evcard__source">{{ r.source }}</span>
              <span v-if="r.ev.truncated">{{ T.inspector.truncated }}</span>
              <button v-if="r.graph" type="button" class="link-btn" @click="inspHighlightGraph(r.ev.id)">{{ T.inspector.highlight }}</button>
              <button type="button" class="link-btn" :aria-expanded="!!inspExpanded[r.ev.id]" @click="inspToggleExpanded(r.ev.id)">
                {{ inspExpanded[r.ev.id] ? T.inspector.collapse : T.inspector.expand }}<ag-icon name="chevronDown" :size="12"></ag-icon></button>
            </footer>
          </article>
        </div>
      </template>
    </section>
    <section v-else-if="inspTab === InspectorTab.SUBGRAPH" role="tabpanel" id="insp-panel-subgraph" aria-labelledby="insp-tab-subgraph">
      <p v-if="!inspSubgraph.edges.length" class="empty">{{ T.inspector.subgraphEmpty }}</p>
      <template v-else>
        <h3 class="sg-title">{{ T.inspector.nodes }}</h3>
        <div class="sg-nodes">
          <button v-for="n in inspSubgraph.nodes" :key="n.name" type="button" class="chip chip--sm sg-node" :class="[inspNodeHopClass(n), { 'sg-node--hot': inspHotNodes.has(n.name) }]"
                  @click="inspOpenGraph(n.name)" :title="T.inspector.openInGraph">{{ n.name }}<span v-if="n.type" class="sg-node__type">{{ n.type }}</span></button>
        </div>
        <h3 class="sg-title">{{ T.inspector.edges }}</h3>
        <ul class="sg-edges">
          <li v-for="e in inspSubgraph.edges" :key="e.id" class="sg-edge" :class="{ 'sg-edge--hot': inspHotEdges.has(e.id) }">
            <span class="sg-edge__n">{{ e.head }}</span><span class="sg-edge__r">-[{{ e.relation }}]-&gt;</span><span class="sg-edge__n">{{ e.tail }}</span></li>
        </ul>
      </template>
      <div v-if="inspGraphView" class="sg-more"><component :is="inspGraphView" :key="inspGraphKey"></component></div>
    </section>
    <section v-else role="tabpanel" id="insp-panel-log" aria-labelledby="insp-tab-log">
      <dl class="log__kv">
        <dt>{{ T.log.route }}</dt><dd>{{ inspRouteLabel }}</dd>
        <dt>{{ T.log.queryId }}</dt><dd>{{ inspAnswer.query_id }}</dd>
      </dl>
      <div class="log__head"><span>{{ T.log.trace }} · {{ fmt(T.log.frames, { n: inspLog.length }) }}</span>
        <button type="button" class="btn btn--xs btn--ghost" @click="inspCopyLog"><ag-icon name="copy" :size="12"></ag-icon>{{ inspLogCopied ? T.log.copied : T.log.copy }}</button></div>
      <pre v-if="inspLog.length" class="log__pre"><span v-for="(f, i) in inspLog" :key="i" class="log__line"><b>{{ f.at }} {{ f.name }}</b> {{ f.summary }}</span></pre>
      <p v-else class="empty">{{ T.log.empty }}</p>
    </section>
  </div>
  <button type="button" class="inspector__strip" @click="inspToggle" :aria-label="T.inspector.open" :title="T.inspector.open + ' (E)'"><ag-icon name="panelRightOpen"></ag-icon><span class="sr-only">{{ T.inspector.title }}</span></button>
</aside>
<div v-if="inspectorOpen" class="inspector-backdrop" @click="closeInspector"></div>`;
