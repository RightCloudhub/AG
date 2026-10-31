// @ts-check
/**
 * Notebook history persisted in localStorage (§4.3: at most 200, grouped today / this week / earlier).
 * Every update returns a new list; views never mutate stored messages in place.
 */
import { ref } from '../../vendor/vue.esm-browser.prod.js';
import { HistoryGroup, MessageRole, StorageKey, UiPolicy, isRecord } from '../contracts.js';

/** @typedef {import('../chat/types.js').Conversation} Conversation */
/** @typedef {import('../chat/types.js').ChatMessage} ChatMessage */
/** @typedef {import('../contracts.js').HistoryGroupName} HistoryGroupName */

/** @returns {Conversation[]} */
function load() {
  try {
    const raw = localStorage.getItem(StorageKey.CONVERSATIONS);
    if (!raw) return [];
    /** @type {unknown} */
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isConversation) : [];
  } catch { return []; }
}

/** @param {unknown} value @returns {value is Conversation} */
function isConversation(value) {
  return isRecord(value) && typeof value.id === 'string' && typeof value.title === 'string' && Array.isArray(value.messages);
}

/** @param {Conversation[]} list */
function persist(list) {
  try { localStorage.setItem(StorageKey.CONVERSATIONS, JSON.stringify(list)); }
  catch { /* quota exceeded or storage disabled: history simply stays in memory */ }
}

/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<Conversation[]>} */
export const conversations = ref(load());
/** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<string | null>} */
export const activeConversationId = ref(null);

/** @param {Conversation[]} list */
function commit(list) {
  const bounded = list.slice(0, UiPolicy.MAX_CONVERSATIONS);
  conversations.value = bounded;
  persist(bounded);
}

function newId() { return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`; }

/** @param {string} firstQuestion */
export function titleFor(firstQuestion) {
  const compact = firstQuestion.replace(/\s+/g, ' ').trim();
  return compact.length > UiPolicy.CONVERSATION_TITLE_CHARS ? `${compact.slice(0, UiPolicy.CONVERSATION_TITLE_CHARS)}…` : compact;
}

/** @param {string} firstQuestion @returns {Conversation} */
export function createConversation(firstQuestion) {
  const now = Date.now();
  const conversation = { id: newId(), title: titleFor(firstQuestion), createdAt: now, updatedAt: now, messages: [] };
  commit([conversation, ...conversations.value]);
  activeConversationId.value = conversation.id;
  return conversation;
}

/** @param {string | null} id */
export function findConversation(id) {
  return id ? conversations.value.find(c => c.id === id) ?? null : null;
}

/** @param {string} id @param {(conversation: Conversation) => Conversation} update */
function patchConversation(id, update) {
  commit(conversations.value.map(c => (c.id === id ? { ...update(c), updatedAt: Date.now() } : c)));
}

/** @param {string} id @param {ChatMessage} message @returns {number} index of the appended message */
export function appendMessage(id, message) {
  let index = -1;
  patchConversation(id, c => { index = c.messages.length; return { ...c, messages: [...c.messages, message] }; });
  return index;
}

/** @param {string} id @param {number} index @param {Partial<ChatMessage>} patch */
export function updateMessage(id, index, patch) {
  patchConversation(id, c => ({ ...c, messages: c.messages.map((m, i) => (i === index ? { ...m, ...patch } : m)) }));
}

/** @param {string} id @param {string} title */
export function renameConversation(id, title) {
  patchConversation(id, c => ({ ...c, title: titleFor(title) }));
}

/** @param {string} id */
export function removeConversation(id) {
  commit(conversations.value.filter(c => c.id !== id));
  if (activeConversationId.value === id) activeConversationId.value = null;
}

export function startNewConversation() { activeConversationId.value = null; }

/** The hop count shown beside a title: the most hops any answer on the page took. @param {Conversation} c */
export function hopCountOf(c) {
  return c.messages.reduce((max, m) => (m.role === MessageRole.ASSISTANT && m.data ? Math.max(max, m.data.steps.length) : max), 0);
}

/** Group by the day the notebook was last touched: today, the last 7 days, earlier. @param {Conversation[]} list @param {number} now */
export function groupConversations(list, now) {
  const startOfToday = new Date(now); startOfToday.setHours(0, 0, 0, 0);
  const today = startOfToday.getTime(), week = today - (UiPolicy.WEEK_DAYS - 1) * UiPolicy.DAY_MS;
  /** @type {{id: HistoryGroupName, items: Conversation[]}[]} */
  const groups = [{ id: HistoryGroup.TODAY, items: [] }, { id: HistoryGroup.WEEK, items: [] }, { id: HistoryGroup.EARLIER, items: [] }];
  for (const c of list) {
    const when = c.updatedAt || c.createdAt || 0;
    groups[when >= today ? 0 : when >= week ? 1 : 2].items.push(c);
  }
  return groups.filter(g => g.items.length);
}
