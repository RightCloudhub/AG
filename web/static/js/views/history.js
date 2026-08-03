/* Q&A history view (P5-UI-02). Lists recent reasoning chains (admin) with
 * question/answer previews; clicking a row expands the full chain JSON.
 * Dynamic text via mustache only; JSON shown in a <pre>.
 */
import { fetchRecentQueries, friendlyError } from "../api.js";

const PREVIEW_LEN = 160;

export const HistoryView = {
  name: "HistoryView",
  data() {
    return {
      loading: false,
      error: "",
      rows: [],
      expandedId: "",
      copyState: "",
    };
  },
  methods: {
    async refresh() {
      this.loading = true;
      this.error = "";
      try {
        const res = await fetchRecentQueries();
        this.rows = res.rows;
      } catch (err) {
        this.error = friendlyError(err);
      } finally {
        this.loading = false;
      }
    },
    preview(text) {
      const s = String(text || "");
      return s.length > PREVIEW_LEN ? `${s.slice(0, PREVIEW_LEN)}…` : s;
    },
    routeLabel(row) {
      return row.route || "—";
    },
    chainJson(row) {
      return JSON.stringify(row, null, 2);
    },
    toggle(row) {
      this.expandedId = this.expandedId === row.query_id ? "" : row.query_id;
      this.copyState = "";
    },
    async copyJson(row) {
      try {
        await navigator.clipboard.writeText(this.chainJson(row));
        this.copyState = "已复制";
      } catch {
        this.copyState = "复制失败";
      }
    },
  },
  activated() {
    this.refresh();
  },
  template: `
    <div class="view-page">
      <header class="view-head">
        <h1>问答历史</h1>
        <p class="topbar-sub">最近推理链（admin）· 点击展开完整链</p>
      </header>

      <div class="toolbar">
        <button type="button" class="chip" @click="refresh" :disabled="loading">刷新</button>
      </div>

      <p v-if="error" class="view-error">{{ error }}</p>
      <p v-if="loading" class="muted">加载中…</p>
      <p v-else-if="!rows.length" class="muted">暂无历史查询</p>

      <div v-else class="history-list">
        <article
          v-for="row in rows"
          :key="row.query_id"
          class="card history-item"
          @click="toggle(row)"
        >
          <div class="history-head">
            <span class="tag">{{ routeLabel(row) }}</span>
            <span class="muted">{{ row.query_id }}</span>
          </div>
          <h3 class="history-q">{{ row.question }}</h3>
          <p class="history-a">{{ preview(row.answer) }}</p>

          <div v-if="expandedId === row.query_id" class="history-detail" @click.stop>
            <button type="button" class="mini-btn" @click="copyJson(row)">
              {{ copyState || "复制 JSON" }}
            </button>
            <pre class="mono">{{ chainJson(row) }}</pre>
          </div>
        </article>
      </div>
    </div>
  `,
};
