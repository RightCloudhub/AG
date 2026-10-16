/**
 * Review view — human review queue with approve/reject/skip.
 */
import { ref, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { fetchReviewQueue, decideReview } from '../api.js';

export default {
  name: 'ReviewView',
  setup() {
    const items = ref([]);
    const total = ref(0);
    const pendingCount = ref(0);
    const loading = ref(false);
    const statusFilter = ref('pending'); // pending, approved, rejected
    const page = ref(1);
    const limit = 20;

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

    async function decide(itemId, decision) {
      try {
        await decideReview(itemId, { decision });
        await load();
      } catch (e) {
        alert('Decision failed: ' + e.message);
      }
    }

    function statusBadge(status) {
      const m = { pending: 'badge--warning', approved: 'badge--success',
                  rejected: 'badge--danger', skipped: 'badge--muted' };
      return m[status] || 'badge--muted';
    }

    function typeBadge(type) {
      const m = { spotcheck: 'badge--info', feedback: 'badge--accent',
                  conflict: 'badge--warning', extraction: 'badge--accent' };
      return m[type] || 'badge--muted';
    }

    function totalPages() { return Math.max(1, Math.ceil(total.value / limit)); }
    function prevPage() { if (page.value > 1) { page.value--; load(); } }
    function nextPage() { if (page.value < totalPages()) { page.value++; load(); } }
    function changeStatus(s) { statusFilter.value = s; page.value = 1; load(); }

    onMounted(load);

    return { items, total, pendingCount, loading, statusFilter, page,
             load, decide, statusBadge, typeBadge, totalPages,
             prevPage, nextPage, changeStatus };
  },

  template: `
<div>
  <div class="view-header">
    <div class="view-header__title">🔍 人工审核</div>
    <div class="view-header__actions">
      <span v-if="pendingCount > 0" class="badge badge--warning">{{ pendingCount }} 个待审核</span>
      <button class="btn btn--ghost btn--sm" @click="load" :disabled="loading">↻ 刷新</button>
    </div>
  </div>
  <div class="view-body">
    <div class="tab-bar" style="max-width:400px;">
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === 'pending' }" @click="changeStatus('pending')">待审核</button>
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === 'approved' }" @click="changeStatus('approved')">已批准</button>
      <button class="tab-bar__item" :class="{ 'tab-bar__item--active': statusFilter === 'rejected' }" @click="changeStatus('rejected')">已拒绝</button>
    </div>

    <div v-if="loading" class="empty-state"><div class="spinner spinner--lg"></div></div>

    <div v-else-if="items.length === 0" class="empty-state">
      <div class="empty-state__icon">✅</div>
      <div class="empty-state__text">没有 {{ statusFilter === 'pending' ? '待审核' : (statusFilter === 'approved' ? '已批准' : '已拒绝') }} 的项目</div>
    </div>

    <div v-else class="data-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>类型</th>
            <th>状态</th>
            <th>置信度</th>
            <th>详情</th>
            <th v-if="statusFilter === 'pending'">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td style="font-family:var(--font-mono);font-size:var(--text-xs);">{{ (item.id || '').slice(0, 12) }}</td>
            <td><span class="badge" :class="typeBadge(item.type)">{{ item.type }}</span></td>
            <td><span class="badge" :class="statusBadge(item.status)">{{ item.status }}</span></td>
            <td style="font-family:var(--font-mono);">{{ (item.confidence ?? 0).toFixed(2) }}</td>
            <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:var(--text-xs);">
              {{ JSON.stringify(item.payload || item.data || {}).slice(0, 80) }}
            </td>
            <td v-if="statusFilter === 'pending'">
              <div style="display:flex;gap:var(--space-2);">
                <button class="btn btn--success btn--sm" @click="decide(item.id, 'approve')">通过</button>
                <button class="btn btn--danger btn--sm" @click="decide(item.id, 'reject')">拒绝</button>
                <button class="btn btn--ghost btn--sm" @click="decide(item.id, 'skip')">跳过</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="table-footer">
        <span>共 {{ total }} 项</span>
        <div class="pagination">
          <button class="pagination__btn" :disabled="page <= 1" @click="prevPage">←</button>
          <span class="pagination__info">{{ page }} / {{ totalPages() }}</span>
          <button class="pagination__btn" :disabled="page >= totalPages()" @click="nextPage">→</button>
        </div>
      </div>
    </div>
  </div>
</div>
`
};
