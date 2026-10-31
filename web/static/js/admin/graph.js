// @ts-check
/**
 * Graph view — entity browser with search, type filter, pagination.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchEntities } from '../api.js';
import { UiPolicy } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { graphFocus } from '../store/graph_focus.js';
import { openNotebook } from '../store/shell.js';

export default {
  name: 'GraphView',
  setup() {
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').Entity[]>} */
    const entities = ref([]);
    const total = ref(0);
    const loading = ref(false);
    // Pre-filled when opened from a chat citation (store/graph_focus.js).
    const search = ref(graphFocus.value);
    const typeFilter = ref('');
    const page = ref(1);
    const limit = UiPolicy.GRAPH_PAGE_SIZE;
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    let debounceTimer;

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
      debounceTimer = setTimeout(() => { page.value = 1; load(); }, UiPolicy.SEARCH_DEBOUNCE_MS);
    }

    function onTypeChange() { page.value = 1; load(); }

    onMounted(load);

    return { entities, total, loading, search, typeFilter, page, limit, totalPages, prevPage, nextPage,
             onSearchInput, onTypeChange, load, openNotebook, T, fmt, UiPolicy };
  },

  template: `
<div class="view">
  <div class="view-header">
    <button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="T.notebook.open"><ag-icon name="menu"></ag-icon></button>
    <h1 class="view-header__title">{{ T.graph.title }}</h1>
    <div class="view-header__actions"><span class="hint num">{{ fmt(T.graph.entities, { n: total }) }}</span></div>
  </div>
  <div class="view-body">
    <div class="toolbar">
      <div class="search-bar">
        <span class="search-bar__icon"><ag-icon name="search" :size="14"></ag-icon></span>
        <input class="input" :placeholder="T.graph.search" :aria-label="T.graph.search" v-model="search" @input="onSearchInput">
      </div>
      <input class="input toolbar__filter" :placeholder="T.graph.filterType" :aria-label="T.graph.filterType" v-model="typeFilter" @change="onTypeChange">
      <button type="button" class="btn btn--secondary btn--sm" @click="load" :disabled="loading"><ag-icon name="refresh" :size="14"></ag-icon>{{ T.common.refresh }}</button>
    </div>

    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>
    <div v-else-if="entities.length === 0" class="empty-state empty-state--slot"><div class="empty-state__text">{{ T.graph.empty }}</div></div>
    <div v-else class="entity-grid">
      <div v-for="e in entities" :key="e.id" class="entity-card">
        <div class="entity-card__name">{{ e.name }}</div>
        <span class="tag entity-card__type">{{ e.type || T.graph.unknownType }}</span>
        <div v-if="e.aliases?.length" class="entity-card__aliases">
          <span v-for="a in e.aliases" :key="a" class="chip chip--sm">{{ a }}</span>
        </div>
        <div class="entity-card__id">{{ e.id?.slice(0, UiPolicy.ID_PREVIEW) }}</div>
      </div>
    </div>

    <div v-if="entities.length > 0" class="pager">
      <div class="pagination">
        <button type="button" class="btn btn--xs btn--secondary" :disabled="page <= 1" @click="prevPage">{{ T.graph.prev }}</button>
        <span class="pagination__info">{{ fmt(T.graph.page, { page, pages: totalPages() }) }}</span>
        <button type="button" class="btn btn--xs btn--secondary" :disabled="page >= totalPages()" @click="nextPage">{{ T.graph.next }}</button>
      </div>
    </div>
  </div>
</div>
`,
};
