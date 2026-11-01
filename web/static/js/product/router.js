// @ts-check
import { createRouter, createWebHistory } from '../../vendor/vue-router.esm-browser.prod.js';
import { ensureSession, user } from './session.js';
import { safeNext } from './contracts.js';
import Home from './home.js';
import Auth from './auth_view.js';
import Invite from './invite.js';
import Workspace from './workspace.js';
import ErrorView from './error_view.js';
import Settings from './settings/layout.js';
import { findConversation } from '../store/conversations.js';

export function buildRouter() {
  const router = createRouter({ history: createWebHistory(), routes: [
    { path: '/', component: Home },
    ...['login', 'register', 'forgot', 'reset'].map(mode => ({ path: `/${mode}`, component: Auth, props: { mode }, meta: { guest: mode === 'login' || mode === 'register' } })),
    { path: '/invite/:token', component: Invite },
    { path: '/app', component: Workspace, meta: { auth: true }, children: [
      { path: '', redirect: '/app/chat' },
      { path: 'chat/:id?', name: 'chat', component: () => import('../views/chat.js'), meta: { capability: 'chat' } },
      { path: 'graph', component: () => import('../admin/graph.js'), meta: { capability: 'graph' } },
      { path: 'knowledge', component: () => import('../admin/knowledge.js'), meta: { capability: 'knowledge' } },
      { path: 'review', component: () => import('../admin/review.js'), meta: { capability: 'review' } },
      { path: 'ops', component: () => import('../admin/ops.js'), meta: { capability: 'ops' } },
      { path: 'settings', component: Settings, children: [
        { path: '', redirect: '/app/settings/profile' },
        { path: 'profile', component: () => import('./settings/profile.js'), meta: { capability: 'settings_profile' } },
        { path: 'security', component: () => import('./settings/security.js'), meta: { capability: 'settings_security' } },
        { path: 'members', component: () => import('./settings/members.js'), meta: { capability: 'settings_members' } },
        { path: 'api-keys', component: () => import('./settings/keys.js'), meta: { capability: 'settings_api_keys' } },
        { path: 'tenant', component: () => import('./settings/tenant.js'), meta: { capability: 'settings_tenant' } },
      ] },
      { path: 'forbidden', component: ErrorView, props: { status: 403 } },
      { path: 'not-found', component: ErrorView },
      { path: ':pathMatch(.*)*', component: ErrorView },
    ] },
    { path: '/:pathMatch(.*)*', component: ErrorView },
  ] });
  router.beforeEach(guardRoute);
  return router;
}

/** @param {import('../../vendor/vue-router.esm-browser.prod.js').Route} to */
export async function guardRoute(to) {
  await ensureSession();
  if (to.meta.auth && !user.value) return { path: '/login', query: { next: safeNext(to.fullPath) } };
  if (to.meta.guest && user.value) return safeNext(to.query.next);
  if (to.meta.capability && !user.value?.capabilities[to.meta.capability]) return '/app/forbidden';
  if (to.name === 'chat' && to.params.id && typeof to.params.id === 'string' && !findConversation(to.params.id)) return '/app/not-found';
}
