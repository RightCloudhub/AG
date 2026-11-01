/** Owned boundary for the locally vendored Vue Router 4.5.1 runtime. */
import type { Component, Ref } from './vue.esm-browser.prod.js';
export interface Route {
  path: string; fullPath: string; name?: string;
  params: Record<string, string | string[]>;
  query: Record<string, string | null | (string | null)[]>;
  meta: { auth?: boolean; guest?: boolean; capability?: string };
}
export interface RouteRecord {
  path: string; name?: string; component?: Component | (() => Promise<unknown>);
  redirect?: string; props?: boolean | Record<string, unknown>;
  meta?: Route['meta']; children?: RouteRecord[];
}
export interface Router {
  currentRoute: Ref<Route>;
  push(to: string | { path: string; query?: Record<string, string> }): Promise<unknown>;
  replace(to: string | { path: string; query?: Record<string, string> }): Promise<unknown>;
  beforeEach(guard: (to: Route) => unknown): void;
  afterEach(guard: (to: Route) => void): void;
  isReady(): Promise<void>;
}
export function createWebHistory(): unknown;
export function createRouter(options: { history: unknown; routes: RouteRecord[] }): Router;
export function useRoute(): Route;
export function useRouter(): Router;
