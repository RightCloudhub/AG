// @ts-check
/**
 * Review view — human review queue with approve/reject/skip.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchReviewQueue, decideReview } from '../api.js';
import { ReviewStatus, ReviewDecision, ReviewType, UiPolicy } from '../contracts.js';
import { text as T, fmt } from '../i18n.js';
import { openNotebook } from '../store/shell.js';

/** @param {import('../contracts.js').ReviewStatusName} status */
function statusBadge(status) {
  const m = { [ReviewStatus.PENDING]: 'tag--warn', [ReviewStatus.APPROVED]: 'tag--ok',
              [ReviewStatus.REJECTED]: 'tag--err', [ReviewStatus.SKIPPED]: '' };
  return m[status] || '';
}

/** @param {import('../contracts.js').ReviewTypeName} type */
function typeBadge(type) {
  const m = { [ReviewType.SPOTCHECK]: 'tag--accent', [ReviewType.FEEDBACK]: 'tag--accent',
              [ReviewType.RESOLUTION]: 'tag--accent', [ReviewType.CONFLICT]: 'tag--warn', [ReviewType.EXTRACTION]: 'tag--accent' };
  return m[type] || '';
}

/** @param {import('../contracts.js').ReviewStatusName} status */
function statusLabel(status) {
  const m = { [ReviewStatus.PENDING]: T.review.pending, [ReviewStatus.APPROVED]: T.review.approved,
              [ReviewStatus.REJECTED]: T.review.rejected, [ReviewStatus.SKIPPED]: T.review.rejected };
  return m[status] || status;
}

export default {
  name: 'ReviewView',
  setup() {
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../responses.js').ReviewItem[]>} */
    const items = ref([]);
    const total = ref(0);
    const pendingCount = ref(0);
    const loading = ref(false);
    const error = ref('');
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<import('../contracts.js').ReviewStatusName>} */
    const statusFilter = ref(ReviewStatus.PENDING); // pending, approved, rejected
    const page = ref(1);
    const limit = UiPolicy.TABLE_PAGE_SIZE;

    async function load() {
      loading.value = true;
      try {
        const { data, meta } = await fetchReviewQueue({
          limit, offset: (page.value - 1) * limit,
          status: statusFilter.value,
        });
        items.value = data || [];
        total.value = meta?.total ?? 0;
        pendingCount.value = meta?.extra?.pending_count ?? 0;
      } catch { items.value = []; }
      finally { loading.value = false; }
    }

    /** @param {string} itemId @param {import('../contracts.js').ReviewDecisionName} decision */
    async function decide(itemId, decision) {
      error.value = '';
      try {
        await decideReview(itemId, { decision });
        await load();
      } catch (e) {
        error.value = fmt(T.review.failed, { msg: e instanceof Error ? e.message : String(e) });
      }
    }

    function totalPages() { return Math.max(1, Math.ceil(total.value / limit)); }
    function prevPage() { if (page.value > 1) { page.value--; load(); } }
    function nextPage() { if (page.value < totalPages()) { page.value++; load(); } }
    /** @param {import('../contracts.js').ReviewStatusName} s */
    function changeStatus(s) { statusFilter.value = s; page.value = 1; load(); }

    onMounted(load);

    return { items, total, pendingCount, loading, error, statusFilter, page,
             load, decide, statusBadge, typeBadge, statusLabel, totalPages,
             prevPage, nextPage, changeStatus, openNotebook, ReviewStatus, ReviewDecision, UiPolicy, T, fmt };
  },

  template: `
<div class="view">
  <div class="view-header">
    <button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="T.notebook.open"><ag-icon name="menu"></ag-icon></button>
    <h1 class="view-header__title">{{ T.review.title }}</h1>
    <div class="view-header__actions">
      <span v-if="pendingCount > 0" class="tag tag--warn">{{ fmt(T.review.pendingCount, { n: pendingCount }) }}</span>
      <button type="button" class="btn btn--secondary btn--sm" @click="load" :disabled="loading"><ag-icon name="refresh" :size="14"></ag-icon>{{ T.common.refresh }}</button>
    </div>
  </div>
  <div class="view-body">
    <div class="tab-bar">
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === ReviewStatus.PENDING }" @click="changeStatus(ReviewStatus.PENDING)">{{ T.review.pending }}</button>
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === ReviewStatus.APPROVED }" @click="changeStatus(ReviewStatus.APPROVED)">{{ T.review.approved }}</button>
      <button type="button" class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === ReviewStatus.REJECTED }" @click="changeStatus(ReviewStatus.REJECTED)">{{ T.review.rejected }}</button>
    </div>
    <p v-if="error" class="row-error" role="alert"><ag-icon name="warning" :size="14"></ag-icon><span>{{ error }}</span></p>

    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>

    <div v-else-if="items.length === 0" class="empty-state empty-state--slot">
      <div class="empty-state__text">{{ fmt(T.review.empty, { status: statusLabel(statusFilter) }) }}</div>
    </div>

    <div v-else class="data-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>{{ T.review.id }}</th>
            <th>{{ T.review.type }}</th>
            <th>{{ T.review.status_ }}</th>
            <th class="num">{{ T.review.confidence }}</th>
            <th>{{ T.review.detail }}</th>
            <th v-if="statusFilter === ReviewStatus.PENDING">{{ T.review.actions }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td class="mono">{{ (item.id || '').slice(0, UiPolicy.ID_PREVIEW) }}</td>
            <td><span class="tag" :class="typeBadge(item.type)">{{ item.type }}</span></td>
            <td><span class="tag" :class="statusBadge(item.status)">{{ item.status }}</span></td>
            <td class="num">{{ (item.confidence ?? 0).toFixed(2) }}</td>
            <td class="cell-clip">{{ JSON.stringify(item.payload || {}).slice(0, UiPolicy.PAYLOAD_PREVIEW) }}</td>
            <td v-if="statusFilter === ReviewStatus.PENDING">
              <div class="row-actions">
                <button type="button" class="btn btn--secondary btn--sm" @click="decide(item.id, ReviewDecision.APPROVE)">{{ T.review.approve }}</button>
                <button type="button" class="btn btn--danger btn--sm" @click="decide(item.id, ReviewDecision.REJECT)">{{ T.review.reject }}</button>
                <button type="button" class="btn btn--ghost btn--sm" @click="decide(item.id, ReviewDecision.SKIP)">{{ T.review.skip }}</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="table-footer">
        <span>{{ fmt(T.review.total, { n: total }) }}</span>
        <div class="pagination">
          <button type="button" class="btn btn--xs btn--secondary" :disabled="page <= 1" @click="prevPage">{{ T.graph.prev }}</button>
          <span class="pagination__info">{{ page }} / {{ totalPages() }}</span>
          <button type="button" class="btn btn--xs btn--secondary" :disabled="page >= totalPages()" @click="nextPage">{{ T.graph.next }}</button>
        </div>
      </div>
    </div>
  </div>
</div>
`,
};
