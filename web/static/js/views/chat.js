// @ts-check
/**
 * Chat view — multi-hop QA with SSE streaming, evidence, reasoning chain, feedback.
 */
import { ref, computed, nextTick, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { streamQuery, postFeedback } from '../api.js';
import { record, text, number, parseAnswer } from '../responses.js';
import { MessageRole, QueryRoute, StreamEvent, UiPolicy, KeyboardKey } from '../contracts.js';

const HINTS = [
  'Apex Holdings 的 CEO 是谁？',
  'TechVentures 投资了哪些公司？',
  'NovaTech 被哪家子公司收购了？',
];

/** @typedef {{role: import('../contracts.js').MessageRoleName, text?: string, data?: import('../responses.js').Answer, feedbackSent?: boolean, feedbackValue?: boolean, showSteps?: boolean}} ChatMessage */

function useChatState() {
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<ChatMessage[]>} */
  const messages = ref([]);
  const question = ref('');
  const streaming = ref(false);
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<{icon: string, text: string, type: import('../contracts.js').StreamEventName}[]>} */
  const streamSteps = ref([]);
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<AbortController | null>} */
  const abortCtrl = ref(null);
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<HTMLElement | null>} */
  const chatEl = ref(null);
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<HTMLTextAreaElement | null>} */
  const inputEl = ref(null);

  const canSend = computed(() => question.value.trim().length > 0 && !streaming.value);

  function scrollBottom() {
    nextTick(() => {
      if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight;
    });
  }

  /** @param {string} text */
  function useHint(text) {
    question.value = text;
    if (inputEl.value) inputEl.value.focus();
  }

  onMounted(() => { if (inputEl.value) inputEl.value.focus(); });
  return { messages, question, streaming, streamSteps, abortCtrl, chatEl, inputEl, canSend, scrollBottom, useHint };
}

export default {
  name: 'ChatView',
  setup() {
    const { messages, question, streaming, streamSteps, abortCtrl, chatEl, inputEl, canSend, scrollBottom, useHint } = useChatState();

    function sendQuestion() {
      const q = question.value.trim();
      if (!q || streaming.value) return;
      messages.value.push({ role: MessageRole.USER, text: q });
      question.value = '';
      streaming.value = true;
      streamSteps.value = [];
      scrollBottom();

      const ctrl = streamQuery({ question: q, max_hops: UiPolicy.DEFAULT_MAX_HOPS }, {
        [StreamEvent.TRIAGE](p)       { streamSteps.value.push({ icon: '🧭', text: `路由: ${text(record(p).route) || text(record(p).path) || 'analyzing'}`, type: StreamEvent.TRIAGE }); scrollBottom(); },
        [StreamEvent.THINKING](p)     { streamSteps.value.push({ icon: '💭', text: text(record(p).thought) || text(record(p).text) || '思考中…', type: StreamEvent.THINKING }); scrollBottom(); },
        [StreamEvent.SUB_QUESTION](p) { streamSteps.value.push({ icon: '🔍', text: text(record(p).sub_question) || text(record(p).question) || text(record(p).text) || '', type: StreamEvent.SUB_QUESTION }); scrollBottom(); },
        [StreamEvent.HOP_DONE](p)     { streamSteps.value.push({ icon: '✓', text: `第 ${number(record(p).hop)} 跳完成`, type: StreamEvent.HOP_DONE }); scrollBottom(); },
        [StreamEvent.CACHE_HIT](p)    { streamSteps.value.push({ icon: '⚡', text: '缓存命中', type: StreamEvent.CACHE_HIT }); scrollBottom(); },
        [StreamEvent.ANSWER](p)       { messages.value.push({ role: MessageRole.ASSISTANT, data: parseAnswer(p) }); streaming.value = false; streamSteps.value = []; scrollBottom(); },
        [StreamEvent.ERROR](p)        { messages.value.push({ role: MessageRole.ERROR, text: text(record(p).message, '未知错误') }); streaming.value = false; streamSteps.value = []; scrollBottom(); },
        done()          { if (streaming.value) { streaming.value = false; streamSteps.value = []; } },
      });
      abortCtrl.value = ctrl;
    }

    function cancelStream() {
      if (abortCtrl.value) { abortCtrl.value.abort(); streaming.value = false; streamSteps.value = []; }
    }

    /** @param {KeyboardEvent} e */
    function handleKeydown(e) {
      if (e.key === KeyboardKey.ENTER && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
    }

    /** @param {ChatMessage} msg @param {boolean} accurate */
    async function submitFeedback(msg, accurate) {
      if (!msg.data?.query_id || msg.feedbackSent) return;
      try {
        await postFeedback({ query_id: msg.data.query_id, accurate });
        msg.feedbackSent = true;
        msg.feedbackValue = accurate;
      } catch { /* toast handled globally */ }
    }

    /** @param {ChatMessage} msg */
    function toggleSteps(msg) {
      msg.showSteps = !msg.showSteps;
    }


    return {
      messages, question, streaming, streamSteps, canSend, chatEl, inputEl,
      HINTS, useHint, sendQuestion, cancelStream, handleKeydown,
      submitFeedback, toggleSteps, MessageRole, QueryRoute, UiPolicy,
    };
  },

  template: `
<div class="chat-container">
  <div class="chat-messages" ref="chatEl">
    <!-- Welcome -->
    <div v-if="messages.length === 0 && !streaming" class="chat-welcome">
      <div class="chat-welcome__icon">🧠</div>
      <div class="chat-welcome__title">AgenticGraphRAG</div>
      <div class="chat-welcome__desc">图谱增强多跳推理问答系统 — 提出一个多跳问题，观察 Agent 的推理过程</div>
      <div class="chat-welcome__hints">
        <button v-for="h in HINTS" :key="h" class="chat-hint" @click="useHint(h)">{{ h }}</button>
      </div>
    </div>

    <!-- Messages -->
    <template v-for="(msg, i) in messages" :key="i">
      <!-- User message -->
      <div v-if="msg.role === MessageRole.USER" class="msg msg--user">
        <div class="msg__bubble">{{ msg.text }}</div>
      </div>

      <!-- Error message -->
      <div v-else-if="msg.role === MessageRole.ERROR" class="msg msg--assistant">
        <div class="msg__bubble" style="border-color: var(--color-danger);">
          <span class="badge badge--danger" style="margin-right:8px;">错误</span>
          {{ msg.text }}
        </div>
      </div>

      <!-- Assistant answer -->
      <div v-else class="answer-section">
        <div class="answer-card">
          <div class="answer-card__header">
            <div class="answer-card__route">
              <span class="badge" :class="msg.data.route === QueryRoute.FAST_PATH ? 'badge--success' : 'badge--accent'">
                {{ msg.data.route || QueryRoute.AGENTIC }}
              </span>
              <span v-if="msg.data.status" class="badge badge--muted">{{ msg.data.status }}</span>
            </div>
            <span style="font-size:var(--text-xs);color:var(--color-text-muted);font-family:var(--font-mono);">
              {{ msg.data.query_id?.slice(0, UiPolicy.SHORT_ID_PREVIEW) }}
            </span>
          </div>
          <div class="answer-card__body">{{ msg.data.answer }}</div>

          <!-- Evidence -->
          <div v-if="msg.data.evidence.length" class="evidence-list" style="padding:0 var(--space-5) var(--space-4);">
            <div v-for="ev in msg.data.evidence" :key="ev.id" class="evidence-item">
              <span class="evidence-item__id">[{{ ev.id }}]</span>
              <span class="evidence-item__score">{{ ev.score.toFixed(UiPolicy.SCORE_PRECISION) }}</span>
              {{ ev.content }}
            </div>
          </div>

          <!-- Steps toggle -->
          <div v-if="msg.data.steps?.length" class="steps-section" style="padding:0 var(--space-5) var(--space-4);">
            <div class="steps-toggle" @click="toggleSteps(msg)">
              {{ msg.showSteps ? '▼' : '▶' }} {{ msg.data.steps.length }} 步推理过程
            </div>
            <template v-if="msg.showSteps">
              <div v-for="s in msg.data.steps" :key="s.hop" class="step-item">
                <div class="step-item__hop">第 {{ s.hop }} 跳</div>
                <div class="step-item__question">{{ s.sub_question }}</div>
                <div v-if="s.conclusion" class="step-item__conclusion">{{ s.conclusion }}</div>
              </div>
            </template>
          </div>

          <!-- Meta -->
          <div class="answer-card__meta">
            <span class="answer-card__meta-item">⏱ {{ msg.data.cost?.latency_ms || 0 }}ms</span>
            <span class="answer-card__meta-item">🔗 {{ msg.data.steps?.length || 0 }} 跳</span>
            <span class="answer-card__meta-item">📊 {{ msg.data.cost?.tokens || 0 }} tokens</span>
            <span class="answer-card__meta-item">📞 {{ msg.data.cost?.llm_calls || 0 }} 次大模型调用</span>
          </div>

          <!-- Feedback -->
          <div class="feedback-bar">
            <span class="feedback-bar__label">这个回答有帮助吗？</span>
            <button class="btn btn--sm" :class="msg.feedbackValue === true ? 'btn--success' : 'btn--ghost'"
                    :disabled="msg.feedbackSent" @click="submitFeedback(msg, true)">👍</button>
            <button class="btn btn--sm" :class="msg.feedbackValue === false ? 'btn--danger' : 'btn--ghost'"
                    :disabled="msg.feedbackSent" @click="submitFeedback(msg, false)">👎</button>
          </div>
        </div>
      </div>
    </template>

    <!-- Streaming progress -->
    <div v-if="streaming" class="stream-progress">
      <div v-for="(s, i) in streamSteps" :key="i" class="stream-step"
           :class="{ 'stream-step--active': i === streamSteps.length - 1 }">
        <span class="stream-step__icon">{{ s.icon }}</span>
        <span>{{ s.text }}</span>
      </div>
      <div v-if="streamSteps.length === 0" class="stream-step stream-step--active">
        <span class="stream-step__icon"><span class="spinner"></span></span>
        <span>连接中…</span>
      </div>
    </div>
  </div>

  <!-- Input -->
  <div class="chat-input-area">
    <div class="chat-input-wrap">
      <textarea ref="inputEl" class="chat-input" rows="1" placeholder="输入一个复杂的问题…"
                v-model="question" @keydown="handleKeydown"
                :disabled="streaming"></textarea>
      <button v-if="streaming" class="chat-send-btn" style="background:var(--color-danger);" @click="cancelStream">■</button>
      <button v-else class="chat-send-btn" :disabled="!canSend" @click="sendQuestion">↑</button>
    </div>
  </div>
</div>
`
};
