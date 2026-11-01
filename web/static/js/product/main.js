// @ts-check
import { createApp, watch, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { AgIcon } from '../icons.js';
import { initTheme } from '../theme.js';
import { initializeSession, user } from './session.js';
import { safeNext } from './contracts.js';
import { P } from './copy.js';
import { buildRouter } from './router.js';

initializeSession();
initTheme();
const router = buildRouter();
const app = createApp({
  setup() {
    watch(user, value => {
      const route = router.currentRoute.value;
      if (!value && route.meta.auth) router.replace({ path: '/login', query: { next: safeNext(route.fullPath) } });
    });
    return { P };
  },
  template: `<router-view v-slot="{Component, route}"><component :is="Component" :key="route.meta.auth ? 'workspace' : route.path"></component></router-view>`,
});
app.component('ag-icon', AgIcon);
app.use(router);
router.afterEach(() => nextTick(() => { document.title = `${P.notebook} · ${P.brand}`; }));
app.mount('#app');
