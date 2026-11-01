// @ts-check
import { computed } from '../../../vendor/vue.esm-browser.prod.js';
import { useRoute } from '../../../vendor/vue-router.esm-browser.prod.js';
import { P } from '../copy.js';
import { SETTINGS } from '../contracts.js';
import { user } from '../session.js';
export default {
  setup() {
    const route = useRoute();
    const navigation = computed(() => SETTINGS.filter(id => user.value?.capabilities[`settings_${id.replace('-', '_')}`]));
    const tenant = computed(() => user.value?.tenants.find(row => row.id === user.value?.tenant_id));
    return { P, route, navigation, tenant };
  },
  template: `<section class="settings-page">
    <header class="settings-header"><div><p>{{ tenant?.name }}</p><h1 tabindex="-1">{{ P.settings }}</h1></div><router-link to="/app/chat" class="btn btn--ghost">{{ P.back }}<ag-icon name="arrowRight"></ag-icon></router-link></header>
    <nav class="settings-nav" :aria-label="P.settings"><router-link v-for="id in navigation" :key="id" :to="'/app/settings/' + id" active-class="settings-nav__active">{{ P.settingsNav[id] }}</router-link></nav>
    <div class="settings-body"><router-view :key="route.path"></router-view></div>
  </section>`,
};
