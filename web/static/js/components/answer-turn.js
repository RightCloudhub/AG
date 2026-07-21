/* Answer card for one completed/errored turn: citations, claims, feedback,
 * folds, copy, and agentic retry. Options API pure object (no Vue import).
 */
import {
  buildAnswerSegments,
  buildChainSummary,
  buildClaimItems,
  confidenceLine,
} from "../chain-view.js";

const COPY_IDLE = "复制";
const COPY_OK = "已复制";
const COPY_FAIL = "复制失败";
const COPY_RESET_MS = 1600;

export const AnswerTurn = {
  name: "AnswerTurn",
  props: { turn: { type: Object, required: true } },
  emits: ["send-feedback", "retry-agentic"],
  data() {
    return { copyState: COPY_IDLE, activeClaim: 0 };
  },
  computed: {
    isError() {
      return Boolean(this.turn.error) && !this.turn.result;
    },
    payload() {
      return this.turn.result || {};
    },
    segments() {
      if (!this.turn.result) return [];
      return buildAnswerSegments(this.payload.answer, this.payload.claims);
    },
    claimItems() {
      return buildClaimItems(this.payload.claims);
    },
    chainJson() {
      if (!this.turn.result) return "";
      return JSON.stringify(buildChainSummary(this.payload), null, 2);
    },
    metaLine() {
      if (!this.turn.result) return "";
      return confidenceLine(this.payload);
    },
    steps() {
      return this.payload.steps || [];
    },
    paths() {
      return this.payload.explored_paths || [];
    },
    fbState() {
      return (this.turn.feedback && this.turn.feedback.state) || "idle";
    },
    fbMessage() {
      return (this.turn.feedback && this.turn.feedback.message) || "";
    },
  },
  methods: {
    onCite(n) {
      this.activeClaim = n;
      const el = this.$el.querySelector(`#claim-${this.turn.id}-${n}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    },
    async copyChain() {
      try {
        await navigator.clipboard.writeText(this.chainJson);
        this.copyState = COPY_OK;
      } catch {
        this.copyState = COPY_FAIL;
      }
      window.setTimeout(() => {
        this.copyState = COPY_IDLE;
      }, COPY_RESET_MS);
    },
    sendGood() {
      this.$emit("send-feedback", { turn: this.turn, accurate: true });
    },
    sendBad() {
      this.$emit("send-feedback", { turn: this.turn, accurate: false });
    },
    retry() {
      this.$emit("retry-agentic", this.turn);
    },
  },
  template: `
    <article class="msg assistant">
      <div class="avatar" aria-hidden="true">A</div>
      <div class="bubble">
        <template v-if="isError">
          <div class="answer-text">{{ turn.error }}</div>
          <div class="retry-row">
            <button type="button" class="chip" @click="retry">强制 Agentic 重问</button>
          </div>
        </template>
        <template v-else>
          <div class="bubble-meta">{{ metaLine }}</div>
          <div class="answer-text">
            <template v-for="(seg, i) in segments" :key="i">
              <span v-if="seg.type === 'text'">{{ seg.text }}</span>
              <sup v-else class="cite-badge">
                <button
                  type="button"
                  class="cite-btn"
                  :title="seg.title"
                  @click="onCite(seg.n)"
                >{{ seg.n }}</button>
              </sup>
            </template>
          </div>

          <div v-if="claimItems.length" class="claims-panel">
            <div class="card-label">论断与引用</div>
            <ol class="claims-list">
              <li
                v-for="c in claimItems"
                :key="c.n"
                :id="'claim-' + turn.id + '-' + c.n"
                :class="{ 'claim-active': activeClaim === c.n }"
              >
                <div class="claim-text">{{ c.text }}</div>
                <div class="claim-ev">{{ c.evidence }}</div>
              </li>
            </ol>
          </div>

          <div class="feedback-row">
            <span class="feedback-label">这个回答准确吗？</span>
            <button
              type="button"
              class="fb-btn good"
              :class="{ active: fbState === 'good' }"
              :disabled="fbState === 'sending'"
              @click="sendGood"
            >准确</button>
            <button
              type="button"
              class="fb-btn bad"
              :class="{ active: fbState === 'bad' }"
              :disabled="fbState === 'sending'"
              @click="sendBad"
            >不准确</button>
            <input
              class="fb-input"
              type="text"
              placeholder="可选原因…"
              v-model="turn.fbReason"
              :disabled="fbState === 'sending' || fbState === 'good' || fbState === 'bad'"
            />
          </div>
          <p v-if="fbMessage" class="feedback-note">{{ fbMessage }}</p>

          <details class="fold" open>
            <summary>
              子问题分解树
            </summary>
            <plan-tree :steps="steps"></plan-tree>
          </details>

          <details class="fold" open>
            <summary>图路径</summary>
            <path-list :paths="paths"></path-list>
          </details>

          <details class="fold">
            <summary>
              步骤与证据
            </summary>
            <steps-list :steps="steps"></steps-list>
          </details>

          <details class="fold">
            <summary>
              推理链 JSON
              <button
                type="button"
                class="mini-btn"
                @click.stop.prevent="copyChain"
              >{{ copyState }}</button>
              <span v-if="copyState !== '复制'" class="copy-note">{{ copyState }}</span>
            </summary>
            <pre class="mono">{{ chainJson }}</pre>
          </details>

          <div class="retry-row">
            <button type="button" class="chip" @click="retry">强制 Agentic 重问</button>
          </div>
        </template>
      </div>
    </article>
  `,
};
