// @ts-check
import { computed, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { useRoute, useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { P, T } from './copy.js';
import { user, sessionRevision, changingIdentity, logout } from './session.js';
import { preferences } from '../store/preferences.js';
import { notebookOpen, openNotebook, closeNotebook, closeOverlays } from '../store/shell.js';
import { toggleTheme } from '../theme.js';
import { applyInspectorWidth } from '../shell/use_inspector.js';
import { useForm } from './forms.js';

export default {
  setup() {
    const route = useRoute(), router = useRouter(), form = useForm();
    const items = [{ id: 'chat', icon: 'chat' }, { id: 'graph', icon: 'graph' }, { id: 'knowledge', icon: 'knowledge' }, { id: 'review', icon: 'review' }, { id: 'ops', icon: 'ops' }];
    const navigation = computed(() => items.filter(item => user.value?.capabilities[item.id]));
    const tenant = computed(() => user.value?.tenants.find(item => item.id === user.value?.tenant_id));
    async function signOut() { await form.run(async () => { await logout(); await router.replace('/login'); }); }
    /** @param {KeyboardEvent} event */
    function keydown(event) { if (event.key === 'Escape') closeOverlays(); }
    onMounted(() => { applyInspectorWidth(preferences.value.inspectorWidth); window.addEventListener('keydown', keydown); });
    onUnmounted(() => window.removeEventListener('keydown', keydown));
    return { P, T, user, tenant, navigation, route, sessionRevision, changingIdentity, preferences, notebookOpen, openNotebook, closeNotebook, toggleTheme, signOut, ...form };
  },
  template: `<div class="app product-app" :class="{'app--insp-collapsed': preferences.inspectorCollapsed}">
    <header class="topbar product-topbar">
      <div class="topbar__left"><button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="P.menu"><ag-icon name="menu"></ag-icon></button>
        <router-link class="topbar__brand" to="/app/chat"><ag-icon name="mark" :size="22"></ag-icon><span>{{ P.brand }}</span></router-link><span class="product-scope">{{ tenant?.name }}</span></div>
      <div class="topbar__right"><button type="button" class="btn btn--icon btn--ghost" @click="toggleTheme" :aria-label="P.theme"><ag-icon name="contrast"></ag-icon></button>
        <router-link to="/app/settings/profile" class="product-user">{{ user?.display_name }}</router-link>
        <button type="button" class="btn btn--ghost" @click="signOut" :disabled="busy">{{ P.signOut }}</button></div>
    </header>
    <p v-if="message" class="banner" role="alert">{{ message }}</p>
    <aside class="notebook" :class="{'notebook--open': notebookOpen}">
      <div class="notebook__top"><button type="button" class="btn btn--ghost notebook__close" @click="closeNotebook">{{ P.close }}</button></div>
      <nav class="notebook__list" :aria-label="P.notebook"><router-link v-for="item in navigation" :key="item.id" :to="'/app/' + item.id" class="nb-link" active-class="nb-link--active" :title="T.nav[item.id]" @click="closeNotebook"><ag-icon :name="item.icon"></ag-icon><span>{{ T.nav[item.id] }}</span></router-link></nav>
      <div class="notebook__admin"><router-link class="nb-link" to="/app/settings/profile" @click="closeNotebook"><ag-icon name="settings"></ag-icon><span>{{ P.settings }}</span></router-link></div>
      <div class="notebook__foot">{{ tenant?.name }} · {{ P.roles[user?.role] }}</div>
    </aside>
    <div v-if="notebookOpen" class="notebook-backdrop" @click="closeNotebook"></div>
    <div v-if="changingIdentity" class="product-error" role="status">{{ P.switching }}</div>
    <router-view v-else :key="sessionRevision"></router-view>
  </div>`,
};
