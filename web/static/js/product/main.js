// @ts-check
import { createApp, watch, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { AgIcon } from '../icons.js';
import { initTheme } from '../theme.js';
import { initializeSession, user, loginDestination } from './session.js';
import { P } from './copy.js';
import { buildRouter } from './router.js';
import { ConfirmDialog } from './confirm.js';

initializeSession();
initTheme();
const router = buildRouter();
const app = createApp({
  setup() {
    watch(user, value => {
      const route = router.currentRoute.value;
      if (!value && route.meta.auth) router.replace(loginDestination(route.fullPath));
    });
    return { P };
  },
  template: `<router-view v-slot="{Component, route}"><component :is="Component" :key="route.meta.auth ? 'workspace' : route.path"></component></router-view>`,
});
app.component('ag-icon', AgIcon);
app.component('confirm-dialog', ConfirmDialog);
app.use(router);
router.afterEach(() => nextTick(() => { document.title = `${P.notebook} · ${P.brand}`; }));
app.mount('#app');
