/* Shared presentational widgets: icon, stat card, state card, pager, toasts, evidence graph. */
import { ICONS } from "../icons.js";
import { vue } from "../runtime.js";

/* ── Toast store (module singleton, created lazily once Vue is loaded) ── */
let toastState = null;

function toasts() {
  if (!toastState) toastState = vue().reactive({ items: [], seq: 0 });
  return toastState;
}

export function toast(message, kind = "info", ms = 3200) {
  const state = toasts();
  const id = ++state.seq;
  state.items.push({ id, message: String(message), kind });
  setTimeout(() => {
    const i = state.items.findIndex((t) => t.id === id);
    if (i >= 0) state.items.splice(i, 1);
  }, ms);
}

export const AppIcon = {
  name: "AppIcon",
  props: { name: { type: String, required: true }, size: { type: String, default: "" } },
  computed: {
    paths() {
      return ICONS[this.name] || ICONS.info;
    },
  },
  template: `<svg class="icon" :class="size ? 'icon--' + size : ''" viewBox="0 0 24 24" aria-hidden="true">
    <path v-for="(d, i) in paths" :key="i" :d="d" /></svg>`,
};

export const StatCard = {
  name: "StatCard",
  props: { label: String, value: [String, Number], unit: String, hint: String, icon: String },
  template: `<div class="stat-card">
    <div class="stat-card__label"><app-icon v-if="icon" :name="icon" size="sm" />{{ label }}</div>
    <div class="stat-card__value">{{ value ?? '—' }}<span v-if="unit" class="stat-card__unit">{{ unit }}</span></div>
    <div v-if="hint" class="stat-card__hint">{{ hint }}</div>
  </div>`,
};

export const StateCard = {
  name: "StateCard",
  props: {
    title: String,
    text: String,
    icon: { type: String, default: "info" },
    error: Boolean,
    loading: Boolean,
  },
  template: `<div class="state-card" :class="{ 'state-card--error': error }" :role="error ? 'alert' : 'status'">
    <div class="state-card__icon"><span v-if="loading" class="spinner"></span><app-icon v-else :name="icon" /></div>
    <div class="state-card__title">{{ loading ? '加载中…' : title }}</div>
    <p v-if="text && !loading">{{ text }}</p>
    <slot />
  </div>`,
};

export const DataPager = {
  name: "DataPager",
  props: { page: Number, total: Number, limit: Number, unit: { type: String, default: "条" } },
  emits: ["go"],
  computed: {
    pages() {
      return Math.max(1, Math.ceil((this.total || 0) / (this.limit || 1)));
    },
  },
  template: `<div class="pager">
    <span>共 {{ total || 0 }} {{ unit }}</span>
    <div class="pager__nav">
      <button class="btn btn--sm" :disabled="page <= 1" @click="$emit('go', page - 1)">上一页</button>
      <span class="mono">{{ page }} / {{ pages }}</span>
      <button class="btn btn--sm" :disabled="page >= pages" @click="$emit('go', page + 1)">下一页</button>
    </div>
  </div>`,
};

export const ToastHost = {
  name: "ToastHost",
  setup() {
    return { state: toasts() };
  },
  template: `<div class="toasts" role="status" aria-live="polite">
    <div v-for="t in state.items" :key="t.id" class="toast" :class="'toast--' + t.kind">{{ t.message }}</div>
  </div>`,
};

export const EvidenceGraph = {
  name: "EvidenceGraph",
  props: { graph: { type: Object, required: true }, activeId: String },
  emits: ["pick"],
  methods: {
    mid(e) {
      return { x: (e.a.x + e.b.x) / 2, y: (e.a.y + e.b.y) / 2 - 4 };
    },
    short(name) {
      return name.length > 20 ? name.slice(0, 19) + "…" : name;
    },
  },
  template: `<svg class="egraph" :viewBox="'0 0 ' + graph.width + ' ' + graph.height" role="img"
      aria-label="证据子图：答案涉及的实体与关系">
    <g v-for="e in graph.edges" :key="e.key" @click="$emit('pick', e.id)" style="cursor:pointer">
      <line class="egraph__edge" :class="{ 'egraph__edge--hot': e.id === activeId }"
            :x1="e.a.x" :y1="e.a.y" :x2="e.b.x" :y2="e.b.y" />
      <text v-if="e.labelled || e.id === activeId" class="egraph__label" :x="mid(e).x" :y="mid(e).y" text-anchor="middle">{{ e.rel }}</text>
    </g>
    <g v-for="n in graph.nodes" :key="n.name">
      <circle :class="n.answer ? 'egraph__node egraph__node--answer' : 'egraph__node'" :cx="n.x" :cy="n.y" r="7" />
      <text class="egraph__name" :x="n.x" :y="n.y + 20" text-anchor="middle">{{ short(n.name) }}</text>
    </g>
  </svg>`,
};
