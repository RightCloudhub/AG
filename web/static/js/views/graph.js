/* Knowledge graph explorer view (P5-UI-02). Browse entities (paginated),
 * filter by name, and expand an entity's one-hop neighbors inline. All data
 * comes from /v1/graph/* endpoints; no third-party graph renderer.
 */
import {
  fetchEntities,
  fetchEntity,
  fetchEntityNeighbors,
  fetchRelations,
  friendlyError,
} from "../api.js";

const PAGE_SIZE = 50;

export const GraphView = {
  name: "GraphView",
  data() {
    return {
      loading: false,
      error: "",
      entities: [],
      relations: [],
      total: 0,
      offset: 0,
      pageSize: PAGE_SIZE,
      filter: "",
      detail: null,
      neighbors: [],
      expandedId: "",
    };
  },
  computed: {
    filteredEntities() {
      const q = this.filter.trim().toLowerCase();
      if (!q) return this.entities;
      return this.entities.filter((e) => e.name.toLowerCase().includes(q));
    },
    canPrev() {
      return this.offset > 0;
    },
    canNext() {
      return this.offset + this.pageSize < this.total;
    },
  },
  methods: {
    async refresh() {
      this.loading = true;
      this.error = "";
      try {
        const [ent, rel] = await Promise.all([
          fetchEntities(this.pageSize, this.offset),
          fetchRelations(),
        ]);
        this.entities = ent.rows;
        this.total = ent.meta.total || ent.rows.length;
        this.relations = rel.rows;
      } catch (err) {
        this.error = friendlyError(err);
      } finally {
        this.loading = false;
      }
    },
    prevPage() {
      this.offset = Math.max(0, this.offset - this.pageSize);
      this.refresh();
    },
    nextPage() {
      this.offset += this.pageSize;
      this.refresh();
    },
    async expand(e) {
      this.detail = null;
      this.neighbors = [];
      this.expandedId = e.id;
      try {
        const [d, n] = await Promise.all([fetchEntity(e.name), fetchEntityNeighbors(e.name)]);
        this.detail = d;
        this.neighbors = n.rows;
      } catch (err) {
        this.detail = null;
        this.expandedId = "";
        this.error = friendlyError(err);
      }
    },
    collapse() {
      this.detail = null;
      this.neighbors = [];
      this.expandedId = "";
    },
    attrList() {
      const attrs = (this.detail && this.detail.attributes) || {};
      return Object.entries(attrs).map(([k, v]) => `${k}: ${v}`);
    },
    relationTitle(row) {
      const rel = row.relation || {};
      return `${rel.head_name} -[${rel.type}]-> ${rel.tail_name}`;
    },
  },
  activated() {
    this.refresh();
  },
  template: `
    <div class="view-page">
      <header class="view-head">
        <h1>知识图谱浏览器</h1>
        <p class="topbar-sub">实体浏览 · 一键展开邻居 · 关系列表</p>
      </header>

      <div class="toolbar">
        <label class="sr-only" for="entityFilter">过滤实体</label>
        <input
          id="entityFilter"
          class="toolbar-input"
          type="text"
          placeholder="按名称过滤…"
          v-model="filter"
        />
        <button type="button" class="chip" @click="refresh" :disabled="loading">刷新</button>
      </div>

      <p v-if="error" class="view-error">{{ error }}</p>
      <p v-if="loading" class="muted">加载中…</p>

      <template v-else>
        <section class="card">
          <h2 class="card-title">实体 <span class="muted">（{{ total }}）</span></h2>
          <table class="data-table">
            <thead>
              <tr><th>名称</th><th>类型</th><th>别名</th><th></th></tr>
            </thead>
            <tbody>
              <tr v-for="e in filteredEntities" :key="e.id">
                <td>{{ e.name }}</td>
                <td><span class="tag">{{ e.type }}</span></td>
                <td class="muted">{{ (e.aliases || []).join(", ") }}</td>
                <td class="table-actions">
                  <button type="button" class="mini-btn" @click="expand(e)">展开</button>
                  <button v-if="expandedId === e.id" type="button" class="mini-btn" @click="collapse">收起</button>
                </td>
              </tr>
              <tr v-if="!filteredEntities.length"><td colspan="4" class="muted">无实体</td></tr>
            </tbody>
          </table>
          <div class="pager">
            <button type="button" class="chip" :disabled="!canPrev || loading" @click="prevPage">上一页</button>
            <span class="muted">第 {{ Math.floor(offset / pageSize) + 1 }} 页</span>
            <button type="button" class="chip" :disabled="!canNext || loading" @click="nextPage">下一页</button>
          </div>
        </section>

        <section v-if="detail" class="card">
          <h2 class="card-title">实体详情：{{ detail.name }}</h2>
          <p class="muted">ID {{ detail.id }} · 类型 {{ detail.type }}</p>
          <ul v-if="attrList().length" class="plain-list">
            <li v-for="a in attrList()" :key="a">{{ a }}</li>
          </ul>
          <p v-else class="muted">无附加属性</p>

          <h3 class="card-sub">邻居（{{ neighbors.length }}）</h3>
          <table class="data-table">
            <thead><tr><th>关系</th><th>对端实体</th><th>类型</th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in neighbors" :key="i">
                <td><span class="edge-tag">{{ row.relation.type }}</span></td>
                <td>{{ row.entity.name }}</td>
                <td><span class="tag">{{ row.entity.type }}</span></td>
              </tr>
              <tr v-if="!neighbors.length"><td colspan="3" class="muted">无邻居</td></tr>
            </tbody>
          </table>
        </section>

        <section class="card">
          <h2 class="card-title">关系 <span class="muted">（{{ relations.length }}）</span></h2>
          <div v-if="!relations.length" class="muted">无关系</div>
          <ul v-else class="plain-list">
            <li v-for="r in relations" :key="r.id" :title="relationTitle(r)">
              <span class="edge-tag">{{ r.type }}</span>
              {{ r.head_name }} → {{ r.tail_name }}
            </li>
          </ul>
        </section>
      </template>
    </div>
  `,
};
