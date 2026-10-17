/* 推理问答: streamed multi-hop QA with live progress, citations, feedback, retry, export.
 * Each question runs independently — the backend keeps no cross-question context. */
import { postFeedback, postQuery, streamQuery } from "../api.js";
import { describeStreamEvent, PROGRESS_EVENTS } from "../chain-view.js";
import { vue } from "../runtime.js";
import { canTrace, workspace } from "../store.js";
import { toast } from "../components/widgets.js";

const SUGGESTIONS = [
  { kind: "两跳", text: "Who is the CEO of the parent company of NovaTech Industries?" },
  { kind: "履历追溯", text: "Which companies did the CEO of Apex Holdings previously work at?" },
  { kind: "交集", text: "Among suppliers of QuantumEdge Server, which also supply Helix Compute?" },
  { kind: "关系链", text: "What is the relationship chain between Elena Varga and QuantumEdge Server?" },
];
const MAX_HOPS_LIMIT = 20;

export default {
  name: "ChatView",
  setup() {
    const { ref, reactive, nextTick, onActivated, onMounted } = vue();
    const ws = workspace();
    const turns = ref([]);
    const question = ref("");
    const opts = reactive({ stream: true, forceAgentic: false, maxHops: "" });
    const live = reactive({ active: false, steps: [], started: 0 });
    const scroller = ref(null);
    const input = ref(null);
    let ctrl = null;
    let seq = 0;

    const scrollDown = () => nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight }));
    const resize = () => {
      const el = input.value;
      if (!el) return;
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
    };

    function requestBody(text, forceAgentic) {
      const body = { question: text, force_agentic: Boolean(forceAgentic) };
      const hops = Number(opts.maxHops);
      if (hops >= 1 && hops <= MAX_HOPS_LIMIT) body.max_hops = Math.round(hops);
      return body;
    }

    function finish(turn, patch) {
      Object.assign(turn, patch, { pending: false, canTrace: canTrace() });
      live.active = false;
      ctrl = null;
      // Land on the question that was answered, not the bottom of a long source list.
      nextTick(() => {
        const el = scroller.value?.querySelector(`[data-turn="${turn.id}"]`);
        if (el) scroller.value.scrollTo({ top: Math.max(0, el.offsetTop - 16) });
      });
    }

    function track(event, payload) {
      if (!PROGRESS_EVENTS.includes(event)) return;
      live.steps.forEach((s) => (s.done = true));
      live.steps.push({ ...describeStreamEvent(event, payload), done: event === "hop_done" });
      scrollDown();
    }

    function ask(text, forceAgentic = opts.forceAgentic) {
      const q = String(text || "").trim();
      if (!q || live.active) return;
      const turn = reactive({ id: ++seq, question: q, forceAgentic, pending: true, data: null, error: "", cached: false });
      turns.value.push(turn);
      Object.assign(live, { active: true, steps: [], started: Date.now() });
      scrollDown();
      const body = requestBody(q, forceAgentic);
      if (!opts.stream) {
        postQuery(body)
          .then(({ data }) => finish(turn, { data }))
          .catch((err) => finish(turn, { error: err.message }));
        return;
      }
      ctrl = streamQuery(body, {
        cache_hit: (p) => ((turn.cached = true), track("cache_hit", p)),
        answer: (p) => finish(turn, { data: p }),
        error: (p) => finish(turn, { error: p?.message || "推理失败" }),
        done: () => turn.pending && finish(turn, { error: turn.data ? "" : "连接已结束，未收到答案" }),
        unknown: (p, event) => track(event, p),
      });
    }

    function send() {
      const q = question.value;
      question.value = "";
      nextTick(resize);
      ask(q);
    }

    function stopStreaming() {
      ctrl?.abort();
      const turn = turns.value[turns.value.length - 1];
      if (turn?.pending) finish(turn, { error: "已停止生成" });
    }

    function retryAgentic(turn, force) {
      ask(turn.question, force);
    }

    async function sendFeedback(turn, { accurate, reason }) {
      try {
        await postFeedback({ query_id: turn.data.query_id, accurate, reason: reason || "" });
        turn.feedback = { accurate };
        toast(accurate ? "感谢反馈" : "已提交，问题将进入审核队列");
      } catch (err) {
        toast(`反馈失败：${err.message}`, "error");
      }
    }

    function exportConversation() {
      const lines = ["# 知识推理工作台 · 对话导出", "", `导出时间：${new Date().toLocaleString("zh-CN")}`, ""];
      for (const t of turns.value) {
        lines.push(`## ${t.question}`, "");
        if (t.error) lines.push(`> 错误：${t.error}`, "");
        if (!t.data) continue;
        lines.push(t.data.answer || "", "", `- 状态：${t.data.status} · 路由：${t.data.route} · query_id：${t.data.query_id}`);
        (t.data.steps || []).forEach((s) => lines.push(`- 第 ${s.hop} 跳：${s.sub_question} → ${s.conclusion || "—"}`));
        (t.data.evidence || []).slice(0, 10).forEach((e, i) => lines.push(`  ${i + 1}. ${e.content}`));
        lines.push("");
      }
      const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `agr-conversation-${Date.now()}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    }

    function clearConversation() {
      if (live.active) stopStreaming();
      turns.value = [];
    }

    function openTrace(queryId) {
      ws.traceQueryId = queryId;
      window.location.hash = "ops";
    }

    function onKeydown(e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    }

    function takePending() {
      if (!ws.pendingQuestion) return;
      question.value = ws.pendingQuestion;
      ws.pendingQuestion = "";
      nextTick(() => (resize(), input.value?.focus()));
    }

    onMounted(() => (takePending(), input.value?.focus()));
    onActivated(takePending);

    return {
      SUGGESTIONS, turns, question, opts, live, scroller, input,
      ask, send, stopStreaming, retryAgentic, sendFeedback, exportConversation,
      clearConversation, openTrace, onKeydown, resize,
    };
  },
  template: `
<div class="chat">
  <div class="chat__scroll" ref="scroller">
    <div class="chat__column">
      <section v-if="!turns.length" class="welcome">
        <div class="welcome__seal"><app-icon name="graph" /></div>
        <h1>从图谱里推理出答案</h1>
        <p>提出需要多跳推理的问题：Agent 会拆解子问题，在知识图谱、向量与全文三路检索中取证，并给出可点击核对的引用。</p>
        <div class="suggestions">
          <button v-for="s in SUGGESTIONS" :key="s.text" class="suggestion" @click="ask(s.text)">
            <span class="suggestion__kind"><app-icon name="sparkle" size="sm" />{{ s.kind }}</span>
            <span class="suggestion__text">{{ s.text }}</span>
          </button>
        </div>
      </section>

      <div v-for="t in turns" :key="t.id" class="turn" :data-turn="t.id">
        <div class="turn-user">{{ t.question }}
          <div class="turn-user__meta"><span v-if="t.forceAgentic">强制 Agentic</span></div>
        </div>
        <div v-if="t.pending" class="progress-live" aria-live="polite">
          <div class="progress-live__head"><span class="spinner"></span>正在推理<span class="muted">{{ live.steps.length }} 个事件</span></div>
          <ol class="progress-log">
            <li v-for="(s, i) in live.steps" :key="i" class="progress-state"
                :class="s.done ? 'progress-state--done' : 'progress-state--current'">
              <span class="progress-state__dot"><app-icon :name="s.icon" size="sm" /></span>
              <div><div>{{ s.title }}</div><div v-if="s.detail" class="progress-state__detail">{{ s.detail }}</div></div>
            </li>
            <li v-if="!live.steps.length" class="progress-state progress-state--current">
              <span class="progress-state__dot"><app-icon name="route" size="sm" /></span><div>{{ opts.stream ? '连接推理流…' : '等待完整答案…' }}</div>
            </li>
          </ol>
        </div>
        <div v-else-if="t.error" class="turn-error" role="alert">
          <app-icon name="alert" /><div><strong>{{ t.error }}</strong>
          <div style="margin-top:8px"><button class="btn btn--sm" @click="retryAgentic(t, t.forceAgentic)"><app-icon name="retry" size="sm" />重试</button></div></div>
        </div>
        <answer-turn v-else-if="t.data" :turn="t" @retry="(force) => retryAgentic(t, force)"
                     @feedback="(fb) => sendFeedback(t, fb)" @trace="openTrace" />
      </div>
    </div>
  </div>

  <div class="composer">
    <form class="composer__box" @submit.prevent="send">
      <label class="sr-only" for="q">问题</label>
      <textarea id="q" ref="input" class="composer__input" rows="1" v-model="question" maxlength="2000"
                placeholder="问一个需要多跳推理的问题…" @keydown="onKeydown" @input="resize"></textarea>
      <div class="composer__bar">
        <div class="composer__opts">
          <label class="toggle"><input type="checkbox" v-model="opts.stream" />流式推理</label>
          <label class="toggle"><input type="checkbox" v-model="opts.forceAgentic" />强制 Agentic</label>
          <label class="hops">最大跳数<input type="number" min="1" max="20" placeholder="默认" v-model="opts.maxHops" /></label>
        </div>
        <div class="composer__send">
          <div class="chat-tools" v-if="turns.length">
            <button type="button" class="mini-btn" @click="exportConversation" aria-label="导出对话"><app-icon name="download" size="sm" />导出</button>
            <button type="button" class="mini-btn" @click="clearConversation" aria-label="清空对话"><app-icon name="x" size="sm" />清空</button>
          </div>
          <button v-if="live.active" type="button" class="stop-btn" @click="stopStreaming" aria-label="停止生成"><app-icon name="stop" /></button>
          <button v-else type="submit" class="send-btn" :disabled="!question.trim()" aria-label="发送"><app-icon name="send" /></button>
        </div>
      </div>
    </form>
    <div class="composer__hint"><span>每个问题独立推理，不继承上一轮上下文。</span><span><span class="kbd">Enter</span> 发送 · <span class="kbd">Shift</span>+<span class="kbd">Enter</span> 换行</span></div>
  </div>
</div>`,
};
