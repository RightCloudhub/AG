// @ts-check
/** Business views share navigation without importing the optional product router. */
/** @type {((view: import('./contracts.js').ViewName) => void) | null} */
let navigate = null;
/** @param {(view: import('./contracts.js').ViewName) => void} handler */
export function installNavigation(handler) { navigate = handler; }
/** @param {import('./contracts.js').ViewName} view */
export function navigateView(view) { if (navigate) navigate(view); else window.location.hash = view; }
