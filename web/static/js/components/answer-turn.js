/* One assistant answer: answer text with numbered citations, claims, sources,
 * reasoning chain, evidence sub-graph, raw JSON, feedback and retry. */
import { buildAnswerSegments, buildEvidenceGraph, buildPlanNodes, citationIndex, describeEvidence } from "../chain-view.js";
import { toast } from "./widgets.js";

export const RETRY_FORCE_LABEL = "强制 Agentic 重问";
export const RETRY_AGAIN_LABEL = "再问一次";

const STATUS = {
  answered: { text: "已回答", cls: "badge--good" },
  partial: { text: "部分回答", cls: "badge--warn" },
  no_answer: { text: "无法回答", cls: "badge--bad" },
};
const SOURCES_PREVIEW = 6;
const CONFIDENCE = { high: "badge--good", medium: "badge--warn", low: "badge--bad" };

export const AnswerTurn = {
  name: "AnswerTurn",
  props: { turn: { type: Object, required: true } },
  emits: ["retry", "feedback", "trace"],
  data() {
    return { tab: "sources", activeId: "", note: "", noteOpen: false, allSources: false };
  },
  computed: {
    d() {
      return this.turn.data || {};
    },
    evidence() {
      return this.d.evidence || this.d.metadata?.evidence || [];
    },
    sources() {
      // Same numbering as the answer's citation marks: cited evidence first, then the rest.
      const index = citationIndex(this.d.claims, this.evidence);
      let next = index.size;
      return this.evidence
        .map((ev, i) => ({ ...describeEvidence(ev, i), n: index.get(ev.id) || ++next }))
        .sort((a, b) => a.n - b.n);
    },
    shownSources() {
      const list = this.allSources ? this.sources : this.sources.slice(0, SOURCES_PREVIEW);
      const active = this.sources.find((s) => s.id === this.activeId);
      return active && !list.includes(active) ? [...list, active] : list;
    },
    segments() {
      return buildAnswerSegments(this.d.answer, this.d.claims, this.evidence);
    },
    nodes() {
      return buildPlanNodes(this.d.steps);
    },
    graph() {
      return buildEvidenceGraph(this.evidence, this.d.answer);
    },
    status() {
      return STATUS[this.d.status] || { text: this.d.status || "—", cls: "" };
    },
    confidence() {
      const c = this.d.metadata?.confidence;
      return c ? { text: `置信度 ${c.level}${c.score != null ? " · " + c.score : ""}`, cls: CONFIDENCE[c.level] || "" } : null;
    },
    routeText() {
      return this.d.route === "fast_path" || this.d.route === "fast" ? "快速通道" : "深度推理";
    },
    alreadyForceAgentic() {
      return Boolean(this.turn.forceAgentic);
    },
    retryLabel() {
      return this.alreadyForceAgentic ? RETRY_AGAIN_LABEL : RETRY_FORCE_LABEL;
    },
    json() {
      return JSON.stringify(this.d, null, 2);
    },
    tabs() {
      return [
        { id: "sources", label: `来源 ${this.sources.length}`, icon: "file" },
        { id: "chain", label: `推理链 ${this.nodes.length}`, icon: "route" },
        ...(this.graph ? [{ id: "graph", label: "证据子图", icon: "graph" }] : []),
        { id: "json", label: "原始 JSON", icon: "layers" },
      ];
    },
  },
  methods: {
    pick(id) {
      this.activeId = this.activeId === id ? "" : id;
      if (this.activeId && this.tab !== "graph") this.tab = "sources";
    },
    claimActive(claim) {
      return Boolean(this.activeId) && (claim.evidence_ids || []).includes(this.activeId);
    },
    async copy() {
      try {
        await navigator.clipboard.writeText(this.d.answer || "");
        toast("答案已复制");
      } catch {
        toast("复制失败：浏览器未授权剪贴板", "error");
      }
    },
    vote(accurate) {
      if (this.turn.feedback) return;
      if (accurate) this.$emit("feedback", { accurate: true, reason: "" });
      else this.noteOpen = true;
    },
    submitNote() {
      this.$emit("feedback", { accurate: false, reason: this.note.trim() });
      this.noteOpen = false;
    },
  },
  template: `<article class="answer" :aria-label="'回答：' + (turn.question || '')">
  <header class="answer__head">
    <span class="badge" :class="status.cls">{{ status.text }}</span>
    <span class="badge badge--info"><app-icon name="route" size="sm" />{{ routeText }}</span>
    <span v-if="confidence" class="badge" :class="confidence.cls">{{ confidence.text }}</span>
    <span v-if="turn.cached" class="badge badge--accent"><app-icon name="bolt" size="sm" />缓存</span>
    <span class="answer__qid" :title="d.query_id">#{{ (d.query_id || '').slice(0, 8) }}</span>
  </header>

  <div class="answer__body">
    <p class="answer__lead"><template v-for="(s, i) in segments" :key="i"><template v-if="s.type === 'text'">{{ s.text }}</template><button v-else class="cite" :class="{ 'cite--on': activeId === s.id }" type="button" :aria-label="'查看证据 ' + s.n" @click="pick(s.id)">{{ s.n }}</button></template></p>
    <p v-if="d.missing_info && d.missing_info.length" class="secondary" style="margin-top:12px">缺失信息：{{ d.missing_info.join('；') }}</p>
  </div>

  <ul v-if="d.claims && d.claims.length > 1" class="claims" aria-label="论断">
    <li v-for="(c, i) in d.claims" :key="i" class="claim" :class="{ 'claim-active': claimActive(c) }">{{ c.text }}</li>
  </ul>

  <nav class="tabs" role="tablist">
    <button v-for="t in tabs" :key="t.id" role="tab" :aria-selected="tab === t.id" @click="tab = t.id">
      <app-icon :name="t.icon" size="sm" />{{ t.label }}
    </button>
  </nav>

  <section class="tab-panel" role="tabpanel">
    <div v-if="tab === 'sources'">
      <div v-if="sources.length" class="sources">
        <button v-for="s in shownSources" :key="s.id" type="button" class="source" :class="{ 'source--on': activeId === s.id }" @click="pick(s.id)">
          <span class="source__head"><span class="cite">{{ s.n }}</span><app-icon :name="s.kind === 'graph' ? 'graph' : 'file'" size="sm" />{{ s.label }}<span v-if="s.score !== null" class="source__score">{{ s.score.toFixed(3) }}</span></span>
          <span class="source__text">{{ s.text }}</span>
        </button>
      </div>
      <button v-if="sources.length > 6" class="mini-btn" style="margin-top:8px" @click="allSources = !allSources">{{ allSources ? '收起' : '展开全部 ' + sources.length + ' 条来源' }}</button>
      <p v-if="!sources.length" class="muted">本次回答没有检索到证据。</p>
    </div>

    <ol v-else-if="tab === 'chain'" class="chain" aria-label="推理链">
      <li v-for="n in nodes" :key="n.key" class="chain-node">
        <span class="chain-node__num">{{ n.hop }}</span>
        <div class="chain-node__q">{{ n.question }}</div>
        <div v-if="n.conclusion" class="chain-node__ans"><app-icon name="check" size="sm" />{{ n.conclusion }}</div>
        <div class="chain-node__tools">
          <span v-for="(t, i) in n.tools" :key="i" class="chip" :title="t.reason">{{ t.tool }} · {{ t.hits }} 命中</span>
          <span v-if="n.critic" class="chip">critic: {{ n.critic }}</span>
        </div>
      </li>
      <li v-if="!nodes.length" class="path-overflow">快速通道直接作答，没有拆解子问题。</li>
    </ol>

    <div v-else-if="tab === 'graph' && graph">
      <evidence-graph :graph="graph" :active-id="activeId" @pick="pick" />
      <p class="path-overflow" style="margin-top:8px">高亮节点为答案实体；点击连线定位对应证据。</p>
    </div>

    <pre v-else class="json-view">{{ json }}</pre>
  </section>

  <div v-if="noteOpen" class="feedback-note">
    <input class="input" v-model="note" maxlength="500" placeholder="哪里不对？（可选，提交后进入审核队列）" @keydown.enter="submitNote" />
    <button class="btn btn--primary btn--sm" @click="submitNote">提交</button>
    <button class="btn btn--ghost btn--sm" @click="noteOpen = false">取消</button>
  </div>
  <div v-if="d.status !== 'answered'" class="retry-row">
    <button class="btn btn--sm" @click="$emit('retry', !alreadyForceAgentic)"><app-icon name="retry" size="sm" />{{ retryLabel }}</button>
  </div>

  <footer class="answer__foot">
    <span class="metric-inline"><app-icon name="clock" size="sm" />{{ d.cost?.latency_ms ?? 0 }} ms</span>
    <span class="metric-inline"><app-icon name="route" size="sm" />{{ nodes.length }} 跳</span>
    <span class="metric-inline"><app-icon name="cpu" size="sm" />{{ d.cost?.llm_calls ?? 0 }} 次 LLM</span>
    <span class="metric-inline"><app-icon name="coins" size="sm" />{{ d.cost?.tokens ?? 0 }} tokens</span>
    <span v-if="turn.feedback" class="badge" :class="turn.feedback.accurate ? 'badge--good' : 'badge--warn'">{{ turn.feedback.accurate ? '已标记有用' : '已反馈问题' }}</span>
    <div class="answer__actions">
      <button class="mini-btn" @click="copy" aria-label="复制答案"><app-icon name="copy" size="sm" /></button>
      <button class="mini-btn" :class="{ 'mini-btn--on': turn.feedback && turn.feedback.accurate }" :disabled="!!turn.feedback" @click="vote(true)" aria-label="回答准确"><app-icon name="thumbUp" size="sm" /></button>
      <button class="mini-btn" :class="{ 'mini-btn--on': turn.feedback && !turn.feedback.accurate }" :disabled="!!turn.feedback" @click="vote(false)" aria-label="回答有误"><app-icon name="thumbDown" size="sm" /></button>
      <button class="mini-btn" @click="$emit('retry', alreadyForceAgentic)" :aria-label="retryLabel"><app-icon name="retry" size="sm" /></button>
      <button v-if="turn.canTrace" class="mini-btn" @click="$emit('trace', d.query_id)" aria-label="查看追溯"><app-icon name="link" size="sm" /></button>
    </div>
  </footer>
</article>`,
};
