/* AgenticGraphRAG trial UI bootstrap (P4-UI-01 / P5-UI-01, ADR-006).
 *
 * Zero-build: no npm / bundler. The pinned Vue 3 ESM runtime is loaded at
 * runtime — a locally vendored copy first (fully offline once vendored, see
 * web/static/vendor/README.md), then pinned CDN mirrors as fallback.
 */
import { registerComponents } from "./js/components/index.js";
import { rootComponent } from "./js/root.js";

const VUE_VERSION = "3.5.13";
const VENDOR_VUE_PATH = "/web/static/vendor/vue.esm-browser.prod.js";
const VUE_SOURCES = [
  VENDOR_VUE_PATH,
  `https://cdn.jsdelivr.net/npm/vue@${VUE_VERSION}/dist/vue.esm-browser.prod.js`,
  `https://unpkg.com/vue@${VUE_VERSION}/dist/vue.esm-browser.prod.js`,
];

async function loadVueRuntime() {
  const failures = [];
  for (const src of VUE_SOURCES) {
    try {
      return await import(src);
    } catch (err) {
      failures.push(`${src} → ${err && err.message ? err.message : err}`);
    }
  }
  throw new Error(failures.join("; "));
}

/* Boot failure notice built with DOM APIs + textContent only (no direct HTML). */
function renderBootError(detail) {
  const mount = document.getElementById("app");
  if (!mount) return;
  mount.replaceChildren();
  const card = document.createElement("div");
  card.className = "boot-error";
  const title = document.createElement("h2");
  title.textContent = "无法加载 Vue 运行时";
  const hint = document.createElement("p");
  hint.textContent =
    "离线环境请先 vendor（见 web/static/vendor/README.md），" +
    `或确认可访问 CDN。所需版本：vue@${VUE_VERSION}。`;
  const pre = document.createElement("pre");
  pre.textContent = detail;
  card.append(title, hint, pre);
  mount.append(card);
}

async function boot() {
  try {
    const vue = await loadVueRuntime();
    const app = vue.createApp(rootComponent);
    registerComponents(app);
    app.mount("#app");
  } catch (err) {
    renderBootError(String(err && err.message ? err.message : err));
  }
}

boot();
