/* Small presentational widgets for the trial chat UI (Options API, pure
 * objects — no Vue import). Templates use mustache / textContent only.
 */
import {
  buildPathRows,
  buildPlanNodes,
  buildStepItems,
} from "../chain-view.js";

const STATUS_LABELS = Object.freeze({
  streaming: "进行中",
  done: "完成",
  error: "出错",
  aborted: "已停止",
});

const STAGE_LABELS = Object.freeze({
  plan: "规划",
  retrieve: "检索",
  think: "思考",
});

export const ProgressLog = {
  name: "ProgressLog",
  props: { turn: { type: Object, required: true } },
  computed: {
    statusLabel() {
      return STATUS_LABELS[this.turn.status] || this.turn.status || "";
    },
    /* Stay open while streaming; after done keep open so the trail is visible
     * (auto-collapse made offline/batched streams look like "no live progress"). */
    isOpen() {
      return this.turn.status === "streaming" || this.turn.status === "done";
    },
    liveLine() {
      const list = this.turn.progress || [];
      if (!list.length) return this.turn.status === "streaming" ? "等待事件…" : "";
      return list[list.length - 1].text || "";
    },
  },
  template: `
    <details class="progress-card" :open="isOpen">
      <summary class="progress-summary">
        <span class="card-label">推理进度</span>
        <span class="progress-state" :data-status="turn.status">{{ statusLabel }}</span>
      </summary>
      <p v-if="turn.status === 'streaming' && liveLine" class="progress-live">{{ liveLine }}</p>
      <ul class="progress-list">
        <li
          v-for="item in turn.progress"
          :key="item.key"
          :class="item.kind"
        >{{ item.text }}</li>
      </ul>
    </details>
  `,
};

export const ThinkingPanel = {
  name: "ThinkingPanel",
  props: { turn: { type: Object, required: true } },
  computed: {
    items() {
      return this.turn.thinking || [];
    },
    visible() {
      return this.items.length > 0 || this.turn.status === "streaming";
    },
    isOpen() {
      return this.turn.status === "streaming" || this.turn.status === "done";
    },
    statusLabel() {
      if (this.turn.status === "streaming") return "思考中…";
      if (this.turn.status === "done") return "已完成";
      return STATUS_LABELS[this.turn.status] || "";
    },
  },
  methods: {
    stageLabel(stage) {
      return STAGE_LABELS[stage] || stage || "思考";
    },
  },
  template: `
    <details v-if="visible" class="thinking-card" :open="isOpen">
      <summary class="thinking-summary">
        <span class="card-label">思考过程</span>
        <span class="thinking-state" :data-status="turn.status">{{ statusLabel }}</span>
      </summary>
      <div class="thinking-body">
        <p v-if="!items.length" class="thinking-placeholder muted">正在组织推理…</p>
        <div
          v-for="item in items"
          :key="item.key"
          class="thinking-item"
          :data-stage="item.stage"
        >
          <div class="thinking-head">
            <span class="thinking-stage">{{ stageLabel(item.stage) }}</span>
            <span class="thinking-text">{{ item.text }}</span>
          </div>
          <pre v-if="item.detail" class="thinking-detail">{{ item.detail }}</pre>
        </div>
      </div>
    </details>
  `,
};

export const PlanTree = {
  name: "PlanTree",
  props: { steps: { type: Array, default: () => [] } },
  computed: {
    nodes() {
      return buildPlanNodes(this.steps);
    },
  },
  template: `
    <ul class="plan-tree">
      <li v-if="!nodes.length" class="muted">无子问题步骤</li>
      <li
        v-for="node in nodes"
        :key="node.key"
        class="plan-node"
        :class="'status-' + node.statusClass"
      >
        <div v-if="node.deps" class="plan-deps">depends: {{ node.deps }}</div>
        <div class="plan-head">{{ node.head }}</div>
        <div class="plan-q">{{ node.question }}</div>
        <div v-if="node.conclusion" class="plan-c">→ {{ node.conclusion }}</div>
      </li>
    </ul>
  `,
};

export const PathList = {
  name: "PathList",
  props: { paths: { type: Array, default: () => [] } },
  computed: {
    model() {
      return buildPathRows(this.paths);
    },
  },
  template: `
    <div class="path-list">
      <div v-if="!model.rows.length" class="muted">无探索路径</div>
      <div v-for="(row, ri) in model.rows" :key="ri" class="path-row">
        <template v-for="(seg, si) in row" :key="si">
          <span v-if="si > 0" class="path-arrow">→</span>
          <span :class="seg.kind === 'edge' ? 'path-edge' : 'path-node'">{{ seg.text }}</span>
        </template>
      </div>
      <div v-if="model.hiddenCount" class="path-overflow muted">
        +{{ model.hiddenCount }} 条未显示
      </div>
    </div>
  `,
};

export const StepsList = {
  name: "StepsList",
  props: { steps: { type: Array, default: () => [] } },
  computed: {
    items() {
      return buildStepItems(this.steps);
    },
  },
  template: `
    <div class="steps">
      <div v-if="!items.length" class="muted">无步骤</div>
      <div v-for="item in items" :key="item.key" class="step">
        <div class="hop">{{ item.hopLabel }}</div>
        <div><strong>子问题</strong> {{ item.subQuestion }}</div>
        <div><strong>结论</strong> {{ item.conclusion }}</div>
        <div><strong>证据</strong> {{ item.evidence }}</div>
        <div><strong>工具</strong> {{ item.tools }}</div>
      </div>
    </div>
  `,
};
