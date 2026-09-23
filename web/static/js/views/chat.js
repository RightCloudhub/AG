import {
  friendlyError,
  postFeedback,
  postQuery,
  streamQuery,
} from "../api.js";
import { describeStreamEvent, describeThinkingEvent } from "../chain-view.js";

const DEFAULT_HOPS = 5;
const MAX_HOPS = 10;
const MAX_COMPOSER_HEIGHT = 160;
const QUESTIONS = Object.freeze([
  "Who is the CEO of Apex Holdings?",
  "Who is the CEO of the parent company of BrightLink Logistics?",
  "What is the parent company of NovaTech Industries?",
]);

export const ChatView = {
  name: "ChatView",
  template: `
    <section class="chat-layout" aria-label="知识问答">
      <div class="chat-toolbar">
        <div><span class="eyebrow">KNOWLEDGE ASSISTANT</span><p>答案附带来源，可展开查看推理步骤。</p></div>
        <div class="toolbar-actions">
          <button v-if="turns.length" class="button button-secondary" type="button" @click="exportConversation">导出记录</button>
          <button v-if="turns.length" class="button button-quiet" type="button" @click="clearConversation">清空对话</button>
          <details class="chat-options">
            <summary class="button button-secondary">查询选项</summary>
            <div class="options-popover">
              <label class="toggle-row"><input type="checkbox" v-model="settings.forceAgentic" /><span>强制完整 Agent 推理</span></label>
              <label class="field-label" for="maxHops">最大推理跳数 <strong>{{ settings.maxHops }}</strong></label>
              <input id="maxHops" type="range" min="1" :max="MAX_HOPS" v-model.number="settings.maxHops" />
              <label class="toggle-row"><input type="checkbox" v-model="settings.useStream" /><span>显示实时推理过程</span></label>
            </div>
          </details>
        </div>
      </div>
      <main class="chat-thread" ref="thread" aria-live="polite" aria-relevant="additions text">
        <div v-if="!turns.length" class="chat-welcome">
          <div class="welcome-mark" aria-hidden="true">知</div>
          <p class="eyebrow">ASK ACROSS YOUR KNOWLEDGE</p>
          <h2>让知识之间建立联系</h2>
          <p class="welcome-copy">提出跨实体、跨文档的问题。每个回答都会展示依据，便于核查。</p>
          <div class="suggestion-grid">
            <button v-for="question in suggestions" :key="question" type="button" class="suggestion-card"
                    :disabled="busy" @click="askSuggestion(question)">
              <span>{{ question }}</span><span class="suggestion-arrow" aria-hidden="true">↗</span>
            </button>
          </div>
        </div>
        <div v-else class="chat-messages">
          <template v-for="turn in turns" :key="turn.id">
            <article class="user-message"><span class="user-avatar" aria-hidden="true">{{ identity.user_id.slice(0, 1).toUpperCase() }}</span>
              <div><span class="message-label">你的问题</span><p>{{ turn.question }}</p></div></article>
            <progress-log :turn="turn"></progress-log>
            <thinking-panel :turn="turn"></thinking-panel>
            <answer-turn v-if="turn.result || turn.error" :turn="turn"
              @send-feedback="sendFeedback" @retry-agentic="retryAgentic"></answer-turn>
          </template>
        </div>
      </main>
      <footer class="composer-area">
        <form class="composer-form" autocomplete="off" @submit.prevent="submitAsk">
          <label class="sr-only" for="questionInput">输入问题</label>
          <textarea id="questionInput" ref="draftBox" v-model="draft" rows="1" maxlength="2000"
            placeholder="询问你的知识库…" :disabled="busy" @keydown.enter.exact.prevent="submitAsk" @input="autoResize"></textarea>
          <span class="composer-count" aria-live="polite">{{ draft.length }}/2000</span>
          <button v-if="busy" class="button button-danger stop-action" type="button" @click="stopStreaming">停止</button>
          <button v-else class="button button-primary send-action" type="submit" :disabled="!draft.trim()">发送 <span aria-hidden="true">↗</span></button>
        </form>
        <p class="composer-note">每次提问相互独立 · Enter 发送 · Shift+Enter 换行 · 请核对回答引用来源。</p>
      </footer>
    </section>
  `,
  props: { identity: { type: Object, required: true } },
  data() {
    return {
      draft: "",
      busy: false,
      turns: [],
      suggestions: QUESTIONS,
      MAX_HOPS,
      settings: { forceAgentic: false, maxHops: DEFAULT_HOPS, useStream: true },
      _controller: null,
      _sequence: 0,
    };
  },
  methods: {
    submitAsk() {
      const question = this.draft.trim();
      if (!question || this.busy) return;
      this.draft = "";
      this.autoResize();
      this.askQuestion(question, {});
    },
    askSuggestion(question) {
      if (!this.busy) this.askQuestion(question, {});
    },
    retryAgentic(turn) {
      if (!this.busy) this.askQuestion(turn.question, { forceAgentic: true });
    },
    createTurn(question, options) {
      this._sequence += 1;
      return {
        id: `${Date.now()}-${this._sequence}`,
        question,
        forceAgentic: Boolean(options.forceAgentic) || this.settings.forceAgentic,
        status: "streaming",
        progress: [],
        thinking: [],
        result: null,
        error: "",
        feedback: { state: "idle", message: "" },
        fbReason: "",
      };
    },
    requestBody(turn) {
      return {
        question: turn.question,
        force_agentic: turn.forceAgentic,
        max_hops: Math.max(1, Math.min(MAX_HOPS, Number(this.settings.maxHops) || DEFAULT_HOPS)),
      };
    },
    async askQuestion(question, options) {
      const turn = this.createTurn(question, options);
      this.turns.push(turn);
      this.busy = true;
      this._controller = new AbortController();
      this.scrollThreadSoon();
      try {
        if (this.settings.useStream) {
          await streamQuery({
            body: this.requestBody(turn),
            signal: this._controller.signal,
            onEvent: (event) => this.handleStreamEvent(turn, event),
          });
          if (turn.status === "streaming") this.finishWithError(turn, new Error("连接已结束，但没有收到答案。"));
        } else {
          this.addProgress(turn, "info", "正在检索并生成答案…");
          this.finishWithResult(turn, await postQuery(this.requestBody(turn), this._controller.signal));
        }
      } catch (error) {
        this.finishWithError(turn, error);
      } finally {
        this.busy = false;
        this._controller = null;
        this.scrollThreadSoon();
      }
    },
    async handleStreamEvent(turn, event) {
      if (event.type === "answer") {
        this.finishWithResult(turn, event.payload);
      } else if (event.type === "error") {
        const payload = event.payload || {};
        this.finishWithError(turn, new Error(payload.message || payload.code || "查询失败"));
      } else {
        const thought = describeThinkingEvent(event);
        if (thought) turn.thinking.push({ key: turn.thinking.length, ...thought });
        const progress = describeStreamEvent(event);
        if (progress) this.addProgress(turn, progress.kind, progress.text);
      }
      await this.$nextTick();
      await new Promise((resolve) => requestAnimationFrame(resolve));
    },
    finishWithResult(turn, result) {
      turn.result = result;
      turn.status = "done";
      this.addProgress(turn, "done", "回答已生成");
    },
    finishWithError(turn, error) {
      if (turn.status !== "streaming") return;
      turn.error = friendlyError(error);
      turn.status = "error";
      this.addProgress(turn, "error", turn.error);
    },
    addProgress(turn, kind, text) {
      turn.progress.push({ key: turn.progress.length, kind, text });
      this.scrollThreadSoon();
    },
    stopStreaming() {
      const turn = this.turns[this.turns.length - 1];
      if (!turn || turn.status !== "streaming") return;
      turn.status = "aborted";
      turn.error = "查询已停止。你可以稍后重新提问。";
      this.addProgress(turn, "error", turn.error);
      if (this._controller) this._controller.abort();
    },
    async sendFeedback({ turn, accurate }) {
      if (!turn.result || turn.feedback.state === "sending") return;
      turn.feedback = { state: "sending", message: "" };
      try {
        await postFeedback({ query_id: turn.result.query_id, accurate, reason: turn.fbReason || "" });
        turn.feedback = { state: accurate ? "good" : "bad", message: "反馈已记录，谢谢。" };
      } catch (error) {
        turn.feedback = { state: "idle", message: `反馈未能提交：${friendlyError(error)}` };
      }
    },
    clearConversation() {
      if (window.confirm("清空当前页面中的所有问答记录？")) this.turns = [];
    },
    exportConversation() {
      const payload = this.turns.map(({ question, result, error }) => ({ question, result, error }));
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `agentic-graphrag-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    },
    scrollThreadSoon() {
      this.$nextTick(() => {
        const thread = this.$refs.thread;
        if (thread) thread.scrollTo({ top: thread.scrollHeight, behavior: "smooth" });
      });
    },
    autoResize() {
      const box = this.$refs.draftBox;
      if (!box) return;
      box.style.height = "auto";
      box.style.height = `${Math.min(box.scrollHeight, MAX_COMPOSER_HEIGHT)}px`;
    },
  },
};
