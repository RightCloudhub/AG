// @ts-check
/** The notebook column (§4.3): 新问题, history by day, the admin group by role, the scope; a sheet on a phone. */

export const NOTEBOOK_TEMPLATE = `
<aside class="notebook" :class="{ 'notebook--open': notebookOpen }" :aria-label="T.notebook.history" :aria-hidden="notebookHidden">
  <div class="notebook__top">
    <button type="button" class="btn btn--secondary notebook__new" @click="newQuestion">
      <ag-icon name="plus" :size="14"></ag-icon><span>{{ T.notebook.new }}</span><kbd>⌘N</kbd></button>
    <button type="button" class="btn btn--icon btn--ghost notebook__close" @click="closeNotebook" :aria-label="T.notebook.close"><ag-icon name="close"></ag-icon></button>
  </div>
  <div class="notebook__mode">
    <label for="nb-mode">{{ T.topbar.mode }}</label>
    <select id="nb-mode" class="select" :value="mode" @change="setMode($event.target.value)">
      <option v-for="m in modeOptions" :key="m.id" :value="m.id">{{ m.label }}</option></select>
  </div>
  <div class="notebook__list">
    <section v-for="g in groups" :key="g.id" class="nb-group">
      <h2 class="nb-group__title">{{ T.notebook[g.id] }}</h2>
      <ul>
        <li v-for="c in g.items" :key="c.id" class="nb-item" :class="{ 'nb-item--active': c.id === activeConversationId }">
          <button type="button" class="nb-item__btn" :title="c.title" :aria-current="c.id === activeConversationId ? 'page' : null" @click="selectChat(c.id)">
            <span class="nb-item__title">{{ c.title || T.notebook.unnamed }}</span>
            <span v-if="hopCount(c)" class="nb-item__hops">{{ fmt(T.hoprail.summary, { n: hopCount(c) }) }}</span>
          </button>
          <button type="button" class="btn btn--icon btn--xs btn--ghost nb-item__remove" @click="removeChat(c.id)" :aria-label="T.notebook.remove">
            <ag-icon name="close" :size="12"></ag-icon></button>
        </li>
      </ul>
    </section>
    <p v-if="!conversations.length" class="nb-empty">{{ T.notebook.empty }}</p>
  </div>
  <div class="notebook__admin">
    <h2 class="nb-group__title">{{ T.notebook.admin }}</h2>
    <nav :aria-label="T.notebook.admin">
      <button v-for="nav in adminNav" :key="nav.id" type="button" class="nb-link" :class="{ 'nb-link--active': currentView === nav.id }"
              :aria-current="currentView === nav.id ? 'page' : null" :title="nav.label" @click="navigate(nav.id)">
        <ag-icon :name="nav.icon" :size="16"></ag-icon><span>{{ nav.label }}</span></button>
    </nav>
  </div>
  <div class="notebook__foot">{{ user?.user_id || T.topbar.healthUnknown }} · {{ user?.tenant_id || 'default' }}</div>
</aside>
<div v-if="notebookOpen" class="notebook-backdrop" @click="closeNotebook"></div>`;
