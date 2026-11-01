// @ts-check
import { createApp, watch, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { AgIcon } from '../icons.js';
import { initTheme } from '../theme.js';
import { initializeSession, user, loginDestination } from './session.js';
import { P } from './copy.js';
import { buildRouter } from './router.js';
import { ConfirmDialog } from './confirm.js';
import CreateTenant from './create_tenant.js';
import { installNavigation } from '../navigation.js';
import { safeNext } from './contracts.js';

initializeSession();
initTheme();
const router = buildRouter();
installNavigation(view => { void router.push(`/app/${view}`); });
const app = createApp({
  setup() {
    watch(user, (value, previous) => {
      const route = router.currentRoute.value;
      if (!value && route.meta.auth) router.replace(loginDestination(route.fullPath));
      else if (value && route.meta.guest) router.replace(safeNext(route.query.next));
      else if (value && previous && route.meta.auth && (value.user_id !== previous.user_id || value.tenant_id !== previous.tenant_id)) router.replace('/app/chat');
      else if (value && route.meta.capability && !value.capabilities[route.meta.capability]) router.replace('/app/forbidden');
    });
    return { P };
  },
  template: `<router-view v-slot="{Component, route}"><component :is="Component" :key="route.meta.auth ? 'workspace' : route.path"></component></router-view>`,
});
app.component('ag-icon', AgIcon);
app.component('confirm-dialog', ConfirmDialog);
app.component('create-tenant-dialog', CreateTenant);
app.use(router);
router.afterEach(() => nextTick(() => { document.title = `${P.notebook} · ${P.brand}`; }));
app.mount('#app');
