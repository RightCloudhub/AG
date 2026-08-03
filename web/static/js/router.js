/* Hash router helpers for the trial UI (P5-UI-02). Pure functions only —
 * the layout component owns the reactive `view` state and calls these.
 */
export const DEFAULT_VIEW = "chat";
const VIEWS = Object.freeze({
  chat: "chat",
  graph: "graph",
  dashboard: "dashboard",
  review: "review",
  history: "history",
});

/* Strip "#/" and unknown suffix; unknown names fall back to chat. */
export function parseHash(hash) {
  const raw = String(hash || "").replace(/^#\/?/, "").trim().toLowerCase();
  return VIEWS[raw] ? raw : DEFAULT_VIEW;
}

export function hashFor(view) {
  const target = VIEWS[String(view || "").toLowerCase()] || DEFAULT_VIEW;
  return `#/${target}`;
}

export function navigate(view) {
  const next = hashFor(view);
  if (window.location.hash !== next) window.location.hash = next;
}
