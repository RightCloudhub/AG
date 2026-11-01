// @ts-check
import { createRouter, createWebHistory } from '../../vendor/vue-router.esm-browser.prod.js';
import { ensureSession, user } from './session.js';
import { safeNext } from './contracts.js';
import Home from './home.js';
import Auth from './auth_view.js';
import Invite from './invite.js';
import Workspace from './workspace.js';
import ErrorView from './error_view.js';

export function buildRouter() {
  const router = createRouter({ history: createWebHistory(), routes: [
    { path: '/', component: Home },
    ...['login', 'register', 'forgot', 'reset'].map(mode => ({ path: `/${mode}`, component: Auth, props: { mode }, meta: { guest: mode === 'login' || mode === 'register' } })),
    { path: '/invite/:token', component: Invite },
    { path: '/app', component: Workspace, meta: { auth: true }, children: [
      { path: '', redirect: '/app/chat' },
      { path: 'chat/:id?', component: () => import('../views/chat.js'), meta: { capability: 'chat' } },
      { path: 'graph', component: () => import('../admin/graph.js'), meta: { capability: 'graph' } },
      { path: 'knowledge', component: () => import('../admin/knowledge.js'), meta: { capability: 'knowledge' } },
      { path: 'review', component: () => import('../admin/review.js'), meta: { capability: 'review' } },
      { path: 'ops', component: () => import('../admin/ops.js'), meta: { capability: 'ops' } },
      { path: 'forbidden', component: ErrorView, props: { status: 403 } },
      { path: ':pathMatch(.*)*', component: ErrorView },
    ] },
    { path: '/:pathMatch(.*)*', component: ErrorView },
  ] });
  router.beforeEach(async to => {
    await ensureSession();
    if (to.meta.auth && !user.value) return { path: '/login', query: { next: safeNext(to.fullPath) } };
    if (to.meta.guest && user.value) return safeNext(to.query.next);
    if (to.meta.capability && !user.value?.capabilities[to.meta.capability]) return '/app/forbidden';
  });
  return router;
}
