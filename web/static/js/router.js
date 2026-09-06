/* Hash router for the console shell (P5-UI-02; ADR-006 — no vue-router).
 * Views live at "#/" + view id (e.g. #/chat). Pure parsing/navigation only:
 * the shell component owns current-view state and the hashchange listener.
 */
const HASH_PREFIX = "#/";

export function parseViewHash(raw) {
  if (typeof raw !== "string" || !raw.startsWith(HASH_PREFIX)) return "";
  return raw.slice(HASH_PREFIX.length).trim();
}

export function currentViewHash() {
  try {
    return parseViewHash(window.location.hash);
  } catch {
    return "";
  }
}

export function navigateToView(viewId) {
  try {
    window.location.hash = HASH_PREFIX + viewId;
  } catch {
    /* non-browser context */
  }
}
