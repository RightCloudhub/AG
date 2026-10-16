/**
 * Graph view — entity browser with search, type filter, pagination.
 */
import { ref, watch, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchEntities } from '../api.js';

export default {
  name: 'GraphView',
  setup() {
    const entities = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const search = ref('');
    const typeFilter = ref('');
    const page = ref(1);
    const limit = 24;
    let debounceTimer = null;

    async function load() {
      loading.value = true;
      try {
        const { data, meta } = await fetchEntities({
          limit,
          offset: (page.value - 1) * limit,
          query: search.value,
          entity_type: typeFilter.value,
        });
        entities.value = data || [];
        total.value = meta?.total ?? data?.length ?? 0;
      } catch (e) {
        entities.value = [];
        total.value = 0;
      } finally {
        loading.value = false;
      }
    }

    function totalPages() {
      return Math.max(1, Math.ceil(total.value / limit));
    }

    function prevPage() { if (page.value > 1) { page.value--; load(); } }
    function nextPage() { if (page.value < totalPages()) { page.value++; load(); } }

    function onSearchInput() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => { page.value = 1; load(); }, 300);
    }

    function onTypeChange() { page.value = 1; load(); }

    onMounted(load);

    return { entities, total, loading, search, typeFilter, page, limit,
             totalPages, prevPage, nextPage, onSearchInput, onTypeChange, load };
  },

  template: `
<div>
  <div class="view-header">
    <div class="view-header__title">🕸 知识图谱</div>
    <div class="view-header__actions">
      <span class="badge badge--info">{{ total }} 个实体</span>
    </div>
  </div>
  <div class="view-body">
    <div class="toolbar">
      <div class="search-bar" style="flex:1;max-width:360px;">
        <span class="search-bar__icon">🔍</span>
        <input class="input" placeholder="搜索实体…"
               v-model="search" @input="onSearchInput">
      </div>
      <input class="input" style="max-width:180px;" placeholder="筛选类型…"
             v-model="typeFilter" @change="onTypeChange">
      <button class="btn btn--ghost btn--sm" @click="load" :disabled="loading">↻ 刷新</button>
    </div>

    <div v-if="loading" class="empty-state">
      <div class="spinner spinner--lg"></div>
    </div>

    <div v-else-if="entities.length === 0" class="empty-state">
      <div class="empty-state__icon">🕸</div>
      <div class="empty-state__text">未找到实体</div>
    </div>

    <div v-else class="entity-grid">
      <div v-for="e in entities" :key="e.id" class="entity-card">
        <div class="entity-card__name">{{ e.name }}</div>
        <span class="badge badge--accent entity-card__type">{{ e.type || 'unknown' }}</span>
        <div v-if="e.aliases?.length" class="entity-card__aliases">
          <span v-for="a in e.aliases" :key="a" class="chip">{{ a }}</span>
        </div>
        <div style="margin-top:var(--space-2);font-size:var(--text-xs);color:var(--color-text-muted);font-family:var(--font-mono);">
          {{ e.id?.slice(0, 12) }}
        </div>
      </div>
    </div>

    <div v-if="entities.length > 0" style="margin-top:var(--space-5);display:flex;justify-content:center;">
      <div class="pagination">
        <button class="pagination__btn" :disabled="page <= 1" @click="prevPage">← 上一页</button>
        <span class="pagination__info">第 {{ page }} 页，共 {{ totalPages() }} 页</span>
        <button class="pagination__btn" :disabled="page >= totalPages()" @click="nextPage">下一页 →</button>
      </div>
    </div>
  </div>
</div>
`
};
