/* Console presentational widgets (P5-UI-02 U-06): stat-card, state-card,
 * data-table, filter-bar. Options API pure objects — no Vue import, no
 * mustache bypass. Also hosts small formatting helpers shared by views.
 */

export function formatTimestamp(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return "";
  try {
    return new Date(n * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return String(ts);
  }
}

export function truncateText(text, max = 64) {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export function shortId(id) {
  const s = String(id ?? "");
  return s.length > 8 ? s.slice(0, 8) : s;
}

/* Map an EnvelopeError to state-card props (U-04: no permission probing —
 * errors carry the envelope code and views render the matching state). */
export function errorState(err) {
  const code = err && err.code;
  if (code === "FORBIDDEN") {
    return { kind: "forbidden", title: "无权限", detail: "当前身份的角色不足以查看该数据。" };
  }
  if (code === "UNAUTHORIZED") {
    return { kind: "forbidden", title: "未认证", detail: "请先在侧栏配置有效的 API Key。" };
  }
  const detail = (err && err.message) || "未知错误";
  if (code === "SERVICE_UNAVAILABLE") return { kind: "error", title: "服务未就绪", detail };
  return { kind: "error", title: "请求失败", detail };
}

export const StatCard = {
  name: "StatCard",
  props: {
    label: { type: String, required: true },
    value: { type: [String, Number], default: "" },
    hint: { type: String, default: "" },
  },
  template: `
    <div class="stat-card">
      <div class="stat-value">{{ value }}</div>
      <div class="stat-label">{{ label }}</div>
      <div v-if="hint" class="stat-hint">{{ hint }}</div>
    </div>
  `,
};

export const StateCard = {
  name: "StateCard",
  props: {
    kind: { type: String, default: "info" },
    title: { type: String, required: true },
    detail: { type: String, default: "" },
  },
  computed: {
    glyph() {
      const glyphs = { forbidden: "禁", error: "错", empty: "空", loading: "…", info: "记" };
      return glyphs[this.kind] || "记";
    },
  },
  template: `
    <div class="state-card" :data-kind="kind">
      <div class="state-glyph" aria-hidden="true">{{ glyph }}</div>
      <div class="state-body">
        <div class="state-title">{{ title }}</div>
        <p v-if="detail" class="state-detail">{{ detail }}</p>
      </div>
    </div>
  `,
};

export const DataTable = {
  name: "DataTable",
  props: {
    columns: { type: Array, required: true },
    rows: { type: Array, default: () => [] },
    emptyText: { type: String, default: "暂无数据" },
    clickable: { type: Boolean, default: false },
  },
  emits: ["row-click"],
  methods: {
    cellText(col, row) {
      const value = col.render ? col.render(row) : row[col.key];
      return value === null || value === undefined ? "" : String(value);
    },
    onRowClick(row) {
      if (this.clickable) this.$emit("row-click", row);
    },
  },
  template: `
    <div class="data-table-wrap">
      <table class="data-table" :class="{ clickable }">
        <thead>
          <tr><th v-for="col in columns" :key="col.key" :scope="'col'">{{ col.label }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, ri) in rows" :key="ri" :class="{ clickable }" @click="onRowClick(row)">
            <td v-for="col in columns" :key="col.key">{{ cellText(col, row) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="table-empty muted">{{ emptyText }}</p>
    </div>
  `,
};

export const FilterBar = {
  name: "FilterBar",
  props: {
    filters: { type: Array, required: true },
    values: { type: Object, required: true },
    applyLabel: { type: String, default: "应用" },
  },
  emits: ["apply", "update"],
  methods: {
    onInput(key, event) {
      this.$emit("update", { key, value: event.target.value });
    },
  },
  template: `
    <form class="filter-bar" @submit.prevent="$emit('apply')">
      <label v-for="f in filters" :key="f.key" class="filter-field">
        <span>{{ f.label }}</span>
        <select
          v-if="f.type === 'select'"
          :value="values[f.key]"
          @change="onInput(f.key, $event)"
        >
          <option v-for="opt in f.options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
        <input
          v-else
          :type="f.type || 'text'"
          :value="values[f.key]"
          :placeholder="f.placeholder || ''"
          @input="onInput(f.key, $event)"
        />
      </label>
      <button type="submit" class="chip">{{ applyLabel }}</button>
    </form>
  `,
};
