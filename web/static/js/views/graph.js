/* 知识图谱: searchable, type-filtered, paginated entity browser. The API exposes
 * entities only (no neighbourhood query), so relations are explored by asking. */
import { fetchEntities } from "../api.js";
import { vue } from "../runtime.js";
import { workspace } from "../store.js";

const PAGE = 24;
const TYPES = [
  { id: "", label: "全部" },
  { id: "Company", label: "公司" },
  { id: "Person", label: "人物" },
  { id: "Product", label: "产品" },
  { id: "Event", label: "事件" },
  { id: "Location", label: "地点" },
];
const TYPE_BADGE = { Company: "badge--info", Person: "badge--accent", Product: "badge--good", Event: "badge--warn" };
const ASK_TEMPLATES = {
  Company: (n) => `Who is the CEO of ${n}?`,
  Person: (n) => `Which companies did ${n} previously work at?`,
  Product: (n) => `Which company competes with the producer of ${n}?`,
};

export default {
  name: "GraphView",
  setup() {
    const { ref, onMounted } = vue();
    const ws = workspace();
    const items = ref([]);
    const total = ref(0);
    const page = ref(1);
    const query = ref("");
    const type = ref("");
    const loading = ref(false);
    const error = ref("");
    let timer = 0;

    async function load() {
      loading.value = true;
      error.value = "";
      try {
        const { data, meta } = await fetchEntities({
          limit: PAGE,
          offset: (page.value - 1) * PAGE,
          query: query.value.trim(),
          entity_type: type.value,
        });
        items.value = data || [];
        total.value = meta.total ?? items.value.length;
      } catch (err) {
        items.value = [];
        error.value = err.message;
      } finally {
        loading.value = false;
      }
    }

    function go(p) {
      page.value = p;
      load();
    }

    function onSearch() {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => go(1), 280);
    }

    function pickType(id) {
      type.value = id;
      go(1);
    }

    function askAbout(entity) {
      const make = ASK_TEMPLATES[entity.type] || ((n) => `What is the relationship chain around ${n}?`);
      ws.pendingQuestion = make(entity.name);
      window.location.hash = "chat";
    }

    onMounted(load);
    return { TYPES, TYPE_BADGE, PAGE, items, total, page, query, type, loading, error, load, go, onSearch, pickType, askAbout };
  },
  template: `
<div class="view-pad">
  <div class="toolbar">
    <div class="search">
      <app-icon name="search" size="sm" />
      <label class="sr-only" for="entitySearch">搜索实体</label>
      <input id="entitySearch" class="input" v-model="query" @input="onSearch" placeholder="按名称、别名或 ID 搜索实体" />
    </div>
    <div class="segmented" role="group" aria-label="实体类型">
      <button v-for="t in TYPES" :key="t.id" :aria-pressed="type === t.id" @click="pickType(t.id)">{{ t.label }}</button>
    </div>
    <div class="toolbar__end">
      <span class="badge">{{ total }} 个实体</span>
      <button class="btn btn--sm" @click="load" :disabled="loading"><app-icon name="refresh" size="sm" />刷新</button>
    </div>
  </div>

  <state-card v-if="loading && !items.length" loading />
  <state-card v-else-if="error" error icon="alert" title="实体加载失败" :text="error">
    <button class="btn btn--sm" @click="load">重试</button>
  </state-card>
  <state-card v-else-if="!items.length" icon="graph" title="没有匹配的实体" text="换个关键词，或在「知识接入」上传文档、由 worker 抽取入图后再来。" />

  <template v-else>
    <div class="entity-grid">
      <article v-for="e in items" :key="e.id" class="entity-card">
        <div style="display:flex;gap:8px;align-items:center">
          <span class="badge" :class="TYPE_BADGE[e.type] || ''">{{ e.type || '未知类型' }}</span>
          <span class="mono muted" style="margin-left:auto;font-size:11px">{{ (e.id || '').slice(0, 10) }}</span>
        </div>
        <div class="entity-card__name">{{ e.name }}</div>
        <div v-if="e.aliases && e.aliases.length" class="facets">
          <span v-for="a in e.aliases" :key="a" class="chip">{{ a }}</span>
        </div>
        <div style="margin-top:auto">
          <button class="btn btn--sm btn--ghost" @click="askAbout(e)"><app-icon name="ask" size="sm" />就此提问</button>
        </div>
      </article>
    </div>
    <div class="card"><data-pager :page="page" :total="total" :limit="PAGE" unit="个实体" @go="go" /></div>
  </template>
</div>`,
};
