/* Cross-view workspace state (identity, pending hand-offs between views). */
import { vue } from "./runtime.js";

let state = null;

export function workspace() {
  if (!state) {
    state = vue().reactive({
      me: null, // GET /v1/me data
      health: null, // GET /healthz
      pendingQuestion: "", // graph view → chat view hand-off
      traceQueryId: "", // chat view → ops view hand-off
      reviewPending: 0,
    });
  }
  return state;
}

export function canTrace() {
  return Boolean(workspace().me?.capabilities?.ops);
}

const THEME_KEY = "agr_theme";

export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

export function applyTheme(theme) {
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode: theme just won't persist */
  }
}

export function fmtTime(ts) {
  if (ts === null || ts === undefined || ts === "") return "—";
  const n = Number(ts);
  const date = Number.isFinite(n) ? new Date(n < 1e12 ? n * 1000 : n) : new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(2)} MB`;
}
