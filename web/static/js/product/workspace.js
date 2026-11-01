// @ts-check
import { P, T, fmt } from './copy.js';
import { user, sessionRevision, changingIdentity, sessionError, reloadSession } from './session.js';
import { preferences } from '../store/preferences.js';
import { toggleTheme } from '../theme.js';
import { usePalette } from '../shell/use_palette.js';
import { PALETTE_TEMPLATE } from '../shell/tpl_topbar.js';
import { useNotebook } from './use_notebook.js';
import { useWorkspaceBehavior } from './workspace_behavior.js';
import { PRODUCT_TOPBAR_TEMPLATE } from './topbar_template.js';
import { PRODUCT_NOTEBOOK_TEMPLATE } from './notebook_template.js';
import { initial } from './settings/data.js';

export default {
  setup() {
    const notebook = useNotebook();
    const palette = usePalette({ ...notebook, toggleTheme });
    const behavior = useWorkspaceBehavior(notebook, palette);
    return { P, T, fmt, user, sessionRevision, changingIdentity, sessionError, reloadSession, preferences, toggleTheme, initial,
      ...notebook, ...palette, ...behavior, closePalette: palette.closePaletteDialog };
  },
  template: `<div class="app product-app" :class="{'app--insp-collapsed': preferences.inspectorCollapsed}">
    ${PRODUCT_TOPBAR_TEMPLATE}
    <p v-if="message" class="banner" role="alert">{{ message }}</p>
    <div v-else-if="offline" class="banner banner--err" role="alert"><ag-icon name="warning" :size="14"></ag-icon><span>{{ T.states.offline }}</span><button type="button" class="btn btn--xs btn--secondary" @click="probe">{{ T.states.retry }}</button></div>
    ${PRODUCT_NOTEBOOK_TEMPLATE}
    <div v-if="changingIdentity" class="product-error" role="status">{{ P.switching }}</div>
    <div v-else-if="sessionError" class="product-error" role="alert"><h1>{{ P.loadError }}</h1><button type="button" class="btn btn--primary" @click="reloadSession">{{ T.states.retry }}</button></div>
    <router-view v-else :key="sessionRevision"></router-view>
    ${PALETTE_TEMPLATE}
    <create-tenant-dialog v-if="creatingTenant" :close="closeCreateTenant"></create-tenant-dialog>
  </div>`,
};
