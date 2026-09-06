/* Graph browse view (P5-UI-02 U-11, reader+): paginated entity table.
 * Entity detail / neighborhood needs a new API and is out of scope (§9).
 */
import { fetchGraphEntities } from "../api-console.js";
import { errorState, truncateText } from "../components/console-widgets.js";

const PAGE_SIZE = 20;

export const GraphView = {
  name: "GraphView",
  data() {
    return {
      entities: [],
      total: 0,
      totalIsFloor: false,
      offset: 0,
      error: null,
      columns: [
        { key: "name", label: "实体" },
        { key: "type", label: "类型" },
        { key: "aliases", label: "别名", render: (row) => truncateText((row.aliases || []).join("、"), 40) },
        { key: "id", label: "ID", render: (row) => truncateText(row.id, 24) },
      ],
    };
  },
  computed: {
    page() {
      return Math.floor(this.offset / PAGE_SIZE) + 1;
    },
    pageCount() {
      return Math.max(1, Math.ceil(this.total / PAGE_SIZE));
    },
  },
  mounted() {
    this.load();
  },
  methods: {
    async load() {
      this.error = null;
      try {
        const env = await fetchGraphEntities({ limit: PAGE_SIZE, offset: this.offset });
        this.entities = env.data || [];
        this.total = (env.meta && env.meta.total) || this.entities.length;
        this.totalIsFloor = Boolean(env.meta && env.meta.extra && env.meta.extra.total_is_floor);
      } catch (err) {
        this.error = errorState(err);
      }
    },
    turnPage(delta) {
      const next = (this.page + delta - 1) * PAGE_SIZE;
      if (next < 0 || (delta > 0 && this.page >= this.pageCount)) return;
      this.offset = next;
      this.load();
    },
  },
  template: `
    <section class="view console-view">
      <state-card v-if="error" :kind="error.kind" :title="error.title" :detail="error.detail"></state-card>
      <div class="panel" v-else>
        <div class="panel-head">
          <h2>图实体</h2>
          <button type="button" class="mini-btn" @click="load">刷新</button>
        </div>
        <data-table :columns="columns" :rows="entities" empty-text="图谱为空"></data-table>
        <div class="pager">
          <span class="muted">
            第 {{ page }} / {{ pageCount }} 页 · 共 {{ total }}{{ totalIsFloor ? "+" : "" }} 条
          </span>
          <span class="pager-btns">
            <button type="button" class="mini-btn" :disabled="page <= 1" @click="turnPage(-1)">上一页</button>
            <button type="button" class="mini-btn" :disabled="page >= pageCount" @click="turnPage(1)">下一页</button>
          </span>
        </div>
      </div>
    </section>
  `,
};
