/* Theme controller: reads the `data-theme` attribute set by the inline
 * bootstrap in index.html, persists toggles under localStorage `agr_theme`.
 */
const THEME_KEY = "agr_theme";

export function currentTheme() {
  try {
    return document.documentElement.getAttribute("data-theme") || "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(next) {
  try {
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* private mode */
  }
  return next;
}
