# Vendored assets

Everything the workspace loads at runtime lives here: checked in and pinned, because `web/` is zero-build
(no npm, no bundler, no CDN at load time; ADR-006 in `plan/engineering/tech-stack.md`). Nothing here is
edited by hand.

## Vue 3.5.13, `vue.esm-browser.prod.js` (MIT)

The full ESM browser build, in-browser template compilation included, loaded by `app.js` through a dynamic
`import()`. `vue.esm-browser.prod.d.ts` is the matching type surface for `tsc`; the runtime exposes only
`ref` / `shallowRef` / `computed` / `nextTick` / `onMounted` / `onUnmounted` / `createApp`. Upgrading the
version means updating this file, the ADR, and `VUE_VERSION` in `app.js` together.

## Lucide icon subset, `lucide-icons.js` (ISC, v1.65.0)

The 33 Lucide icons the workspace draws. `Prompt.md` §3.3 fixes the set (Lucide, 16px, 1.5 stroke,
`currentColor`), so the geometry is upstream, not hand-drawn. This file is **generated**: do not edit it.
`scripts/build_lucide_icons.mjs` holds the single source of truth (the app's icon names mapped onto Lucide
ids) and writes both tables.

```bash
npm pack lucide-static@1.65.0 && tar xzf lucide-static-1.65.0.tgz
node scripts/build_lucide_icons.mjs --source package/icon-nodes.json --version 1.65.0   # regenerate
node scripts/build_lucide_icons.mjs --check --source package/icon-nodes.json            # verify no drift
```

`ICON_IDS` maps the app's names (`close`, `warning`, `panelRightClose`, ...) onto Lucide ids, so no id is
written twice in the tree; `LUCIDE` holds each icon's own `[tag, attributes]` node list on the 24-unit grid.
`web/static/js/icons.js` resolves a name to its nodes and renders them with `<ag-icon>`. The product's brand
mark `mark` is the one hand-drawn glyph and lives in that module, not here. Provenance is repeated in
`docs/ops/EXTERNAL_RUNTIMES.md` (and the headless-Chromium note in `docs/EXTERNAL_RUNTIMES.md`).
