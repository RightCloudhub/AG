import { friendlyError, listGraphEntities } from "../api.js";

const PAGE_SIZE = 50;

export const GraphView = {
  name: "GraphView",
  template: `
    <section class="view-stack graph-view">
      <div class="graph-intro"><div><p class="eyebrow">KNOWLEDGE GRAPH</p><h2>实体浏览器</h2><p>按名称、别名或实体 ID 搜索当前租户可访问的知识。</p></div>
        <div class="graph-total"><strong>{{ totalLabel }}</strong><span>匹配实体</span></div></div>
      <section class="panel graph-panel">
        <form class="graph-search" @submit.prevent="searchEntities">
          <label class="search-control"><span class="sr-only">搜索实体</span><span aria-hidden="true">⌕</span>
            <input v-model="searchDraft" type="search" maxlength="200" placeholder="搜索名称、别名或 ID" /></label>
          <label class="filter-field"><span>实体类型</span><input v-model.trim="entityType" type="text" maxlength="100" placeholder="所有类型" /></label>
          <button class="button button-primary" type="submit" :disabled="loading">{{ loading ? '查询中…' : '搜索' }}</button>
        </form>
        <div v-if="error" class="state-card state-error"><strong>无法读取图谱</strong><p>{{ error }}</p><button class="button button-secondary" @click="loadPage(0)">重试</button></div>
        <div v-else-if="loading && !entities.length" class="state-card" role="status">正在读取实体…</div>
        <div v-else-if="!entities.length && !loading" class="state-card"><span class="state-mark" aria-hidden="true">图</span><strong>{{ hasSearch ? '没有匹配实体' : '图谱中还没有实体' }}</strong><p>{{ hasSearch ? '检查拼写，或尝试更短的搜索词。' : '已接入图谱的实体会显示在这里。' }}</p></div>
        <div v-else class="entity-table-wrap">
          <table class="data-table"><thead><tr><th scope="col">实体</th><th scope="col">类型</th><th scope="col">别名</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
            <tbody><tr v-for="entity in entities" :key="entity.id" :class="{ 'row-selected': selected?.id === entity.id }">
              <td><button class="entity-name" type="button" @click="selectEntity(entity)">{{ entity.name || '未命名实体' }}</button><code class="entity-id">{{ entity.id }}</code></td>
              <td><span class="entity-type">{{ entity.type || '未分类' }}</span></td>
              <td>{{ (entity.aliases || []).join('、') || '—' }}</td>
              <td><button class="icon-button" type="button" :aria-label="'查看实体 ' + entity.name" @click="selectEntity(entity)">↗</button></td>
            </tr></tbody>
          </table>
        </div>
        <footer class="table-footer"><span>{{ rangeLabel }}</span><div class="pagination">
          <button class="button button-secondary" type="button" :disabled="offset === 0 || loading" @click="loadPage(Math.max(0, offset - PAGE_SIZE))">上一页</button>
          <button class="button button-secondary" type="button" :disabled="!hasNext || loading" @click="loadPage(offset + PAGE_SIZE)">下一页</button>
        </div></footer>
      </section>
      <p v-if="totalIsFloor" class="view-footnote">当前结果达到接口扫描上限（{{ total }} 条）；总数为下限，后续实体不会出现在此页。</p>
      <section v-if="selected" class="panel entity-detail" aria-live="polite">
        <div class="panel-heading"><div><p class="eyebrow">ENTITY RECORD</p><h3>{{ selected.name }}</h3></div><button class="icon-button" type="button" aria-label="关闭实体详情" @click="selected = null">×</button></div>
        <dl class="entity-properties"><div><dt>实体 ID</dt><dd><code>{{ selected.id }}</code><button class="button button-quiet" @click="copyId">复制</button></dd></div><div><dt>类型</dt><dd>{{ selected.type || '未分类' }}</dd></div><div><dt>别名</dt><dd>{{ (selected.aliases || []).join('、') || '暂无别名' }}</dd></div></dl>
        <p class="view-footnote">当前接口提供实体列表；关系邻域详情尚未开放。</p>
      </section>
    </section>
  `,
  data() {
    return {
      PAGE_SIZE,
      entities: [],
      total: 0,
      offset: 0,
      query: "",
      searchDraft: "",
      entityType: "",
      selected: null,
      loading: false,
      error: "",
      totalIsFloor: false,
    };
  },
  computed: {
    hasNext() { return this.offset + this.entities.length < this.total; },
    hasSearch() { return Boolean(this.query || this.entityType); },
    totalLabel() { return this.totalIsFloor ? `${this.total}+` : String(this.total); },
    rangeLabel() {
      if (!this.total) return "0 条记录";
      const start = this.offset + 1;
      const end = Math.min(this.offset + this.entities.length, this.total);
      return `显示 ${start}–${end} 条，共 ${this.totalLabel} 条`;
    },
  },
  activated() { this.loadPage(this.offset); },
  methods: {
    searchEntities() {
      this.query = this.searchDraft.trim();
      this.selected = null;
      this.loadPage(0);
    },
    async loadPage(offset) {
      this.loading = true;
      this.error = "";
      this.offset = offset;
      this.selected = null;
      try {
        const result = await listGraphEntities({
          query: this.query,
          type: this.entityType,
          limit: PAGE_SIZE,
          offset,
        });
        this.entities = result.data;
        this.total = Number(result.meta.total || 0);
        this.totalIsFloor = Boolean(result.meta.extra?.total_is_floor);
      } catch (error) {
        this.error = friendlyError(error);
        this.entities = [];
      } finally {
        this.loading = false;
      }
    },
    selectEntity(entity) { this.selected = this.selected?.id === entity.id ? null : entity; },
    async copyId() {
      try {
        await navigator.clipboard.writeText(this.selected.id);
        this.$emit("notify", "实体 ID 已复制", "success");
      } catch {
        this.$emit("notify", "无法访问剪贴板，请手动选择 ID", "warning");
      }
    },
  },
};
