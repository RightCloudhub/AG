/* AnswerTurn component: per-turn answer card.
 *
 * Props: `turn` { id, result, error, status, feedback, fbReason }.
 * Emits: `send-feedback` { turn, accurate, reason },
 *        `retry-agentic` { turn }.
 *
 * Template: error variant (retry chip) | result variant (meta line,
 * answer segments with citation superscripts, claims panel with active
 * highlighting, feedback row, four collapsible folds: plan, steps, paths,
 * chain JSON).
 *
 * Pure object (Options API), no Vue import. ADR-006.
 * Dynamic text via mustache only — ADR-006 §8 injection safety.
 *
 * ⚠ in-DOM template: event names use kebab-case (`@click.stop.prevent`). */

import {
  buildAnswerSegments,
  buildClaimItems,
  buildPlanNodes,
  buildStepItems,
  buildPathRows,
  buildChainSummary,
  confidenceLine,
} from "../chain-view.js";

export const answerTurn = {
  name: "AnswerTurn",
  props: {
    turn: { type: Object, required: true },
  },
  emits: ["send-feedback", "retry-agentic"],
  data() {
    return {
      copyState: "idle", /* idle | copied */
      activeClaim: -1,   /* -1 = none; ≥0 = selected citation index */
    };
  },
  computed: {
    isError() {
      return !this.turn.result && this.turn.error;
    },
    result() {
      return this.turn.result || {};
    },
    data() {
      return this.result.data || this.result;
    },
    metaLine() {
      if (!this.data) return "";
      return confidenceLine(this.data);
    },
    segments() {
      if (!this.data) return [];
      return buildAnswerSegments(
        this.data.answer || this.data,
        this.data.claims,
      );
    },
    claimItems() {
      if (!this.data) return [];
      return buildClaimItems(this.data.claims);
    },
    planNodes() {
      if (!this.data || !this.data.steps) return [];
      return buildPlanNodes(this.data.steps);
    },
    stepItems() {
      if (!this.data || !this.data.steps) return [];
      return buildStepItems(this.data.steps);
    },
    pathRows() {
      if (!this.data) return { rows: [], hiddenCount: 0 };
      return buildPathRows(this.data.explored_paths);
    },
    chainSummary() {
      if (!this.data) return null;
      return buildChainSummary(this.data);
    },
    chainJson() {
      if (!this.chainSummary) return "";
      try {
        return JSON.stringify(this.chainSummary, null, 2);
      } catch {
        return "(invalid JSON)";
      }
    },
  },
  methods: {
    sendGoodFeedback() {
      this.$emit("send-feedback", {
        turn: this.turn,
        accurate: true,
        reason: this.turn.fbReason || "",
      });
    },
    sendBadFeedback() {
      this.$emit("send-feedback", {
        turn: this.turn,
        accurate: false,
        reason: this.turn.fbReason || "",
      });
    },
    retryAgentic() {
      this.$emit("retry-agentic", this.turn);
    },
    selectClaim(n) {
      this.activeClaim = this.activeClaim === n ? -1 : n;
      // scroll claim panel into view
      const el = document.querySelector(".claims-panel");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    },
    async copyChainJson() {
      if (!this.chainJson || this.copyState === "copied") return;
      try {
        await navigator.clipboard.writeText(this.chainJson);
        this.copyState = "copied";
        setTimeout(() => {
          this.copyState = "idle";
        }, 2000);
      } catch {
        this.copyState = "idle";
      }
    },
    pathChipText(part) {
      return part.text;
    },
  },
  template: `
    <article class="msg assistant">
      <div class="avatar" aria-hidden="true">✦</div>
      <div class="bubble">
        <!-- Error variant -->
        <template v-if="isError">
          <p class="answer-text" style="color: var(--bad)">
            查询失败：{{ error }}
          </p>
          <div class="retry-row" v-if="status === 'error' || status === 'aborted'">
            <button
              type="button"
              class="mini-btn"
              @click="retryAgentic"
            >强制 Agentic 重问</button>
          </div>
        </template>

        <!-- Result variant -->
        <template v-else>
          <p class="bubble-meta">{{ metaLine }}</p>
          <div class="answer-text">
            <template v-for="(seg, i) in segments" :key="i">
              <template v-if="seg.type === 'text'">{{ seg.text }}</template>
              <button
                v-else-if="seg.type === 'cite'"
                type="button"
                class="cite-btn"
                :title="seg.title"
                @click="selectClaim(seg.n - 1)"
              >{{ seg.n }}</button>
            </template>
          </div>

          <!-- Claims panel -->
          <div class="claims-panel" v-if="claimItems.length">
            <p class="card-label">论断与引用</p>
            <ol class="claims-list">
              <li
                v-for="claim in claimItems"
                :key="claim.n"
                :class="{ 'claim-active': activeClaim === claim.n - 1 }"
              >
                <span class="claim-text">{{ claim.text }}</span>
                <span class="claim-ev">{{ claim.evidence }}</span>
              </li>
            </ol>
          </div>

          <!-- Feedback row -->
          <div class="feedback-row">
            <span class="feedback-label">有帮助吗？</span>
            <button
              type="button"
              class="fb-btn good"
              :class="{ active: feedback.state === 'good' }"
              :disabled="feedback.state === 'sending'"
              @click="sendGoodFeedback"
            >有帮助</button>
            <button
              type="button"
              class="fb-btn bad"
              :class="{ active: feedback.state === 'bad' }"
              :disabled="feedback.state === 'sending'"
              @click="sendBadFeedback"
            >无帮助</button>
            <input
              v-if="!feedback.state || feedback.state === 'idle'"
              type="text"
              class="fb-input"
              placeholder="原因（可选）…"
              v-model="turn.fbReason"
            />
            <span
              v-if="feedback.message"
              class="feedback-note"
              :class="feedback.state"
            >{{ feedback.message }}</span>
          </div>

          <!-- Folds: plan tree -->
          <details class="fold" v-if="planNodes.length">
            <summary>推理计划</summary>
            <ul class="plan-tree">
              <li
                v-for="node in planNodes"
                :key="node.key"
                :class="['plan-node', node.statusClass]"
              >
                <div class="plan-head">{{ node.head }}</div>
                <div class="plan-q" v-if="node.question">{{ node.question }}</div>
                <div class="plan-c" v-if="node.conclusion">{{ node.conclusion }}</div>
                <div class="plan-deps" v-if="node.deps">依赖: {{ node.deps }}</div>
              </li>
            </ul>
          </details>

          <!-- Folds: steps -->
          <details class="fold" v-if="stepItems.length">
            <summary>步骤与证据</summary>
            <div class="steps">
              <div
                v-for="step in stepItems"
                :key="step.key"
                class="step"
              >
                <div class="hop">{{ step.hopLabel }}</div>
                <div><strong>问：</strong>{{ step.subQuestion }}</div>
                <div><strong>答：</strong>{{ step.conclusion }}</div>
                <div><strong>证据：</strong>{{ step.evidence }}</div>
                <div><strong>工具：</strong>{{ step.tools }}</div>
              </div>
            </div>
          </details>

          <!-- Folds: paths -->
          <details class="fold" v-if="pathRows.rows.length">
            <summary>探索路径</summary>
            <div class="path-list">
              <div
                v-for="(row, ri) in pathRows.rows"
                :key="ri"
                class="path-row"
              >
                <template v-for="(part, pi) in row" :key="pi">
                  <span
                    v-if="part.kind === 'node'"
                    class="path-node"
                  >{{ pathChipText(part) }}</span>
                  <span
                    v-else
                    class="path-edge"
                  >{{ pathChipText(part) }}</span>
                </template>
              </div>
            </div>
            <p class="muted" v-if="pathRows.hiddenCount > 0">
              +{{ pathRows.hiddenCount }} 条未显示
            </p>
          </details>

          <!-- Folds: reasoning chain JSON -->
          <details class="fold" v-if="chainSummary">
            <summary>推理链 JSON</summary>
            <div class="summary-bar">
              <span class="muted">{{ data.query_id || '无 query_id' }}</span>
              <button
                type="button"
                class="mini-btn"
                @click="copyChainJson"
              >{{ copyState === 'copied' ? '已复制' : '复制' }}</button>
            </div>
            <pre class="mono">{{ chainJson }}</pre>
          </details>
        </template>
      </div>
    </article>
  `,
};
