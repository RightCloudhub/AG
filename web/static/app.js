/* AgenticGraphRAG · 知识推理工作台 bootstrap (ADR-006, zero-build).
 *
 * No npm / bundler: the pinned Vue 3 ESM build (with the in-browser template
 * compiler) is loaded at runtime — the vendored copy first (fully offline, see
 * web/static/vendor/README.md), then pinned CDN mirrors as fallbacks.
 */
import { setVue } from "./js/runtime.js";

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
  throw new Error(failures.join("\n"));
}

/* Boot failure notice — DOM APIs + textContent only (no markup injection). */
function renderBootError(detail) {
  document.getElementById("boot")?.remove();
  const mount = document.getElementById("app");
  if (!mount) return;
  mount.removeAttribute("v-cloak");
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
    setVue(vue);
    // Components read the runtime lazily, so import them only after setVue().
    const [{ registerComponents }, { rootComponent }] = await Promise.all([
      import("./js/components/index.js"),
      import("./js/root.js"),
    ]);
    const app = vue.createApp(rootComponent);
    registerComponents(app);
    app.mount("#app");
    document.getElementById("boot")?.remove();
  } catch (err) {
    renderBootError(String(err && err.message ? err.message : err));
  }
}

boot();
