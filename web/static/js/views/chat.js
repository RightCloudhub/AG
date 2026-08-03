/* Chat view — the full conversation UI extracted from the old single-page
 * root component (P5-UI-01 → P5-UI-02). Holds its own turns[] state; each
 * turn is an independent request (no multi-turn context sent, V1 boundary).
 * Options API pure object (no Vue import).
 */
import {
  friendlyError,
  getApiKey,
  postFeedback,
  postQuery,
  setApiKey,
  streamQuery,
} from "../api.js";
import { describeStreamEvent, describeThinkingEvent } from "../chain-view.js";

const DEFAULT_MAX_HOPS = 5;
const MIN_HOPS = 1;
const MAX_HOPS = 10;
const COMPOSER_MAX_HEIGHT_PX = 128;
const NEAR_BOTTOM_PX = 120;

const SUGGESTED_QUESTIONS = Object.freeze([
  "Who is the CEO of Apex Holdings?",
  "Who is the CEO of the parent company of BrightLink Logistics?",
  "What is the parent company of NovaTech Industries?",
]);

export const ChatView = {
  name: "ChatView",
  data() {
    return {
      draft: "",
      busy: false,
      turnSeq: 0,
      turns: [],
      suggestions: SUGGESTED_QUESTIONS,
      settings: {
        forceAgentic: false,
        maxHops: DEFAULT_MAX_HOPS,
        useStream: true,
        apiKey: getApiKey(),
      },
    };
  },
  methods: {
    saveApiKey() {
      setApiKey((this.settings.apiKey || "").trim());
    },
    submitAsk() {
      const question = this.draft.trim();
      if (!question || this.busy) return;
      this.draft = "";
      this.autoResize();
      this.askQuestion(question, {});
    },
    askSuggestion(question) {
      if (this.busy) return;
      this.askQuestion(question, {});
    },
    retryAgentic(turn) {
      if (this.busy) return;
      this.askQuestion(turn.question, { forceAgentic: true });
    },
    async askQuestion(question, opts) {
      const turn = this.createTurn(question, opts);
      this.turns.push(turn);
      this.busy = true;
      this._controller = new AbortController();
      this.scrollThreadSoon();
      try {
        if (this.settings.useStream) await this.runStream(turn);
        else await this.runJson(turn);
      } catch (err) {
        this.finishWithError(turn, err);
      } finally {
        this.busy = false;
        this._controller = null;
        this.scrollThreadSoon();
      }
    },
    createTurn(question, opts) {
      this.turnSeq += 1;
      return {
        id: this.turnSeq,
        question,
        forceAgentic: Boolean(opts.forceAgentic) || this.settings.forceAgentic,
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
      const hops = Number(this.settings.maxHops) || DEFAULT_MAX_HOPS;
      return {
        question: turn.question,
        force_agentic: turn.forceAgentic,
        max_hops: Math.min(MAX_HOPS, Math.max(MIN_HOPS, hops)),
      };
    },
    async runJson(turn) {
      this.addProgress(turn, "info", "同步查询 /v1/query …");
      const data = await postQuery(this.requestBody(turn));
      this.finishWithResult(turn, data);
    },
    async runStream(turn) {
      this.addProgress(turn, "info", "连接流式接口…");
      await streamQuery({
        body: this.requestBody(turn),
        signal: this._controller.signal,
        onEvent: (evt) => this.handleStreamEvent(turn, evt),
      });
      if (turn.status === "streaming") {
        this.finishWithError(turn, new Error("流式连接提前结束（未收到 answer）"));
      }
    },
    async handleStreamEvent(turn, evt) {
      if (evt.type === "answer") {
        this.finishWithResult(turn, evt.payload);
        await this.$nextTick();
        return;
      }
      if (evt.type === "error") {
        const payload = evt.payload || {};
        this.finishWithError(turn, new Error(payload.message || payload.code || "流式错误"));
        await this.$nextTick();
        return;
      }
      const thought = describeThinkingEvent(evt);
      if (thought) this.addThinking(turn, thought);
      const note = describeStreamEvent(evt);
      if (note) this.addProgress(turn, note.kind, note.text);
      await this.$nextTick();
      await new Promise((r) => requestAnimationFrame(r));
    },
    finishWithResult(turn, data) {
      turn.result = data;
      turn.status = "done";
      this.addProgress(turn, "done", "完成");
    },
    finishWithError(turn, err) {
      if (turn.status !== "streaming") return;
      turn.error = friendlyError(err);
      turn.status = "error";
      this.addProgress(turn, "error", `错误: ${turn.error}`);
    },
    addProgress(turn, kind, text) {
      turn.progress.push({ key: turn.progress.length, kind, text });
      this.scrollThreadSoon();
    },
    addThinking(turn, thought) {
      turn.thinking.push({
        key: turn.thinking.length,
        stage: thought.stage,
        text: thought.text,
        detail: thought.detail || "",
      });
      this.scrollThreadSoon();
    },
    stopStreaming() {
      const turn = this.turns[this.turns.length - 1];
      if (turn && turn.status === "streaming") {
        turn.status = "aborted";
        turn.error = "已停止（该问题未完成，可重试）";
        this.addProgress(turn, "error", turn.error);
      }
      if (this._controller) this._controller.abort();
    },
    async sendFeedback(payload) {
      const { turn, accurate } = payload;
      if (!turn.result || turn.feedback.state === "sending") return;
      turn.feedback = { state: "sending", message: "" };
      try {
        await postFeedback({
          query_id: turn.result.query_id,
          accurate,
          reason: turn.fbReason || "",
        });
        turn.feedback = { state: accurate ? "good" : "bad", message: "反馈已提交，感谢" };
      } catch (err) {
        turn.feedback = { state: "idle", message: `反馈失败: ${friendlyError(err)}` };
      }
    },
    scrollThreadSoon() {
      const el = this.$refs.thread;
      if (!el) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
      if (!nearBottom) return;
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    },
    autoResize() {
      const box = this.$refs.draftBox;
      if (!box) return;
      box.style.height = "auto";
      box.style.height = `${Math.min(box.scrollHeight, COMPOSER_MAX_HEIGHT_PX)}px`;
    },
  },
  template: `
    <div class="chat-shell">
      <header class="topbar">
        <h1>对话</h1>
        <p class="topbar-sub">单次独立提问 · 答案绑定证据引用</p>
        <details class="rail-settings chat-settings">
          <summary>高级选项</summary>
          <div class="settings-body">
            <label class="check">
              <input type="checkbox" id="forceAgentic" v-model="settings.forceAgentic" />
              <span>强制 Agentic</span>
            </label>
            <label class="field">
              <span>最大跳数</span>
              <input type="number" id="maxHops" min="1" max="10" v-model.number="settings.maxHops" />
            </label>
            <label class="check">
              <input type="checkbox" id="useStream" v-model="settings.useStream" />
              <span>SSE 流式进度</span>
            </label>
            <label class="field">
              <span>API Key</span>
              <input
                type="password"
                id="apiKey"
                autocomplete="off"
                placeholder="Bearer token（可选）"
                v-model="settings.apiKey"
                @change="saveApiKey"
              />
            </label>
          </div>
        </details>
      </header>

      <main class="thread" ref="thread" aria-live="polite" v-cloak>
        <div class="empty-state" v-if="!turns.length">
          <div class="empty-icon" aria-hidden="true">✦</div>
          <h2>有什么想查的？</h2>
          <p>试试多跳关系问题，例如公司母公司的 CEO。</p>
          <div class="suggestions">
            <button
              v-for="q in suggestions"
              :key="q"
              type="button"
              class="chip"
              @click="askSuggestion(q)"
            >{{ q }}</button>
          </div>
        </div>

        <div class="messages" v-else>
          <template v-for="turn in turns" :key="turn.id">
            <article class="msg user">
              <div class="avatar" aria-hidden="true">你</div>
              <div class="bubble"><div class="user-text">{{ turn.question }}</div></div>
            </article>
            <progress-log :turn="turn"></progress-log>
            <thinking-panel :turn="turn"></thinking-panel>
            <answer-turn
              v-if="turn.result || turn.error"
              :turn="turn"
              @send-feedback="sendFeedback"
              @retry-agentic="retryAgentic"
            ></answer-turn>
          </template>
        </div>
      </main>

      <footer class="composer">
        <form id="askForm" class="composer-inner" autocomplete="off" @submit.prevent="submitAsk">
          <label class="sr-only" for="q">问题</label>
          <textarea
            id="q"
            ref="draftBox"
            rows="1"
            placeholder="向知识图谱提问…"
            v-model="draft"
            @keydown.enter.exact.prevent="submitAsk"
            @input="autoResize"
          ></textarea>
          <button
            v-if="busy"
            type="button"
            class="stop-btn"
            aria-label="停止"
            v-cloak
            @click="stopStreaming"
          ><span>停止</span></button>
          <button v-else id="askBtn" type="submit" class="send-btn" aria-label="发送">
            <span>发送</span>
          </button>
        </form>
        <p class="composer-hint">Enter 发送 · Shift+Enter 换行 · 走真实 <code>/v1/query</code> API</p>
      </footer>
    </div>
  `,
};
