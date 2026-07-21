/* Root chat component (Options API; template is the in-DOM markup under
 * #app in web/index.html). Holds conversation state and orchestrates the
 * query/stream/feedback flows. Each turn is an independent question — no
 * multi-turn context is ever sent to the server (V1 boundary).
 *
 * P5-UI-01 (ADR-006): mounted from web/static/app.js.
 */
import { fetchHealth, friendlyError, postFeedback, postQuery, streamQuery } from "./api.js";
import { describeStreamEvent } from "./chain-view.js";

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

export const rootComponent = {
  name: "AgrChatApp",
  data() {
    return {
      draft: "",
      busy: false,
      turnSeq: 0,
      turns: [],
      suggestions: SUGGESTED_QUESTIONS,
      settings: { forceAgentic: false, maxHops: DEFAULT_MAX_HOPS, useStream: true },
      health: { state: "checking", label: "检测服务中…" },
    };
  },
  mounted() {
    this.checkHealth();
  },
  methods: {
    async checkHealth() {
      try {
        await fetchHealth();
        this.health = { state: "ok", label: "服务正常" };
      } catch {
        this.health = { state: "down", label: "服务不可用" };
      }
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

    handleStreamEvent(turn, evt) {
      if (evt.type === "answer") {
        this.finishWithResult(turn, evt.payload);
        return;
      }
      if (evt.type === "error") {
        const payload = evt.payload || {};
        this.finishWithError(turn, new Error(payload.message || payload.code || "流式错误"));
        return;
      }
      const note = describeStreamEvent(evt);
      if (note) this.addProgress(turn, note.kind, note.text);
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

    stopStreaming() {
      const turn = this.turns[this.turns.length - 1];
      if (turn && turn.status === "streaming") {
        turn.status = "aborted";
        this.addProgress(turn, "error", "已停止（该问题未完成，可重试）");
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

    /* Follow new output only when the user is already near the bottom. */
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
};
