// @ts-check
/** Async continuations belong to both a mounted view and its identity generation. */
import { onUnmounted } from '../vendor/vue.esm-browser.prod.js';
import { isCurrentRequest, requestGeneration } from './request_scope.js';
export function useViewLifetime() {
  const generation = requestGeneration();
  let mounted = true;
  onUnmounted(() => { mounted = false; });
  return () => mounted && isCurrentRequest(generation);
}
