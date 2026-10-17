/* Holder for the Vue runtime chosen by app.js (vendored file or CDN mirror).
 * Modules call vue() inside setup(), never at import time. */
let runtime = null;

export function setVue(mod) {
  runtime = mod;
}

export function vue() {
  if (!runtime) throw new Error("Vue runtime not loaded yet");
  return runtime;
}
