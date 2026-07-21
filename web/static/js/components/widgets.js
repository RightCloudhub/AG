/* ProgressLog component: per-turn collapsible progress card.
 * Props: `turn` (id, status, progress[]).
 * `progress[]` items: { key, kind, text } where kind ∈ {info, done, error}.
 * Template: <details :open="status === 'streaming'"> with <li> list.
 *
 * Pure object (Options API), no Vue import. ADR-006.
 * Dynamic text via mustache only — ADR-006 §8 injection safety.

/* --- shared helpers --------------------------------------------------- */

const STATE_LABEL = {
  info: "处理中",
  done: "完成",
  error: "异常",
};

export const progressLog = {
  name: "ProgressLog",
  props: {
    turn: { type: Object, required: true },
  },
  computed: {
    isOpen() {
      return this.turn.status === "streaming";
    },
    stateLabel() {
      return (
        STATE_LABEL[this.turn.status] || this.turn.status || "未知"
      );
    },
  },
  template: `
    <details class="progress-card" :open="isOpen">
      <summary>
        <span class="progress-state">
          {{ stateLabel }}
        </span>
        <span class="muted">{{ progress.length }} 条记录</span>
      </summary>
      <ul class="progress-list" v-if="progress.length">
        <li
          v-for="item in progress"
          :key="item.key"
          :class="item.kind"
        >{{ item.text }}</li>
      </ul>
      <p class="muted" v-else>等待中…</p>
    </details>
  `,
};
