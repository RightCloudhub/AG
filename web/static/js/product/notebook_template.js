// @ts-check
export const PRODUCT_NOTEBOOK_TEMPLATE = `
<aside class="notebook" :class="{'notebook--open': notebookOpen}" :aria-label="T.notebook.history" :aria-hidden="notebookHidden" :inert="notebookHidden">
  <div class="notebook__top"><button type="button" class="btn btn--secondary notebook__new" @click="newQuestion" :title="T.notebook.new"><ag-icon name="plus" :size="14"></ag-icon><span>{{ T.notebook.new }}</span><kbd>⌘N</kbd></button>
    <button type="button" class="btn btn--icon btn--ghost notebook__close" @click="closeNotebook" :aria-label="P.close"><ag-icon name="close"></ag-icon></button></div>
  <div class="notebook__mode"><label for="nb-mode">{{ T.topbar.mode }}</label><select id="nb-mode" class="select" :value="mode" @change="setMode($event.target.value)"><option v-for="item in modeOptions" :key="item.id" :value="item.id">{{ item.label }}</option></select></div>
  <div class="notebook__list"><section v-for="group in groups" :key="group.id" class="nb-group"><h2 class="nb-group__title">{{ T.notebook[group.id] }}</h2><ul>
    <li v-for="note in group.items" :key="note.id" class="nb-item" :class="{'nb-item--active': note.id === activeConversationId}">
      <button type="button" class="nb-item__btn" @click="selectChat(note.id)" :title="note.title" :aria-current="note.id === activeConversationId ? 'page' : null"><span class="nb-item__title">{{ note.title }}</span><span v-if="hopCount(note)" class="nb-item__hops">{{ fmt(T.hoprail.summary, {n: hopCount(note)}) }}</span></button>
      <button type="button" class="btn btn--icon btn--xs btn--ghost nb-item__remove" @click="removeChat(note.id)" :aria-label="T.notebook.remove + ' · ' + note.title"><ag-icon name="close" :size="12"></ag-icon></button></li>
  </ul></section><p v-if="!conversations.length" class="nb-empty">{{ T.notebook.empty }}</p></div>
  <div class="notebook__admin"><h2 class="nb-group__title">{{ T.notebook.admin }}</h2><nav :aria-label="T.notebook.admin">
    <button v-for="item in adminNav" :key="item.id" type="button" class="nb-link" :class="{'nb-link--active': currentView === item.id}" :aria-current="currentView === item.id ? 'page' : null" :title="item.label" @click="navigate(item.id)"><ag-icon :name="item.icon"></ag-icon><span>{{ item.label }}</span></button>
    <router-link class="nb-link" to="/app/settings/profile" :title="P.settings" @click="closeNotebook"><ag-icon name="settings"></ag-icon><span>{{ P.settings }}</span></router-link></nav></div>
  <div class="notebook__foot" :title="tenant?.name">{{ tenant?.name }} · {{ P.roles[user?.role] }}</div>
</aside><div v-if="notebookOpen" class="notebook-backdrop" @click="closeNotebook"></div>`;
