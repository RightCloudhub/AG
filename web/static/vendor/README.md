# Vue 3 runtime vendor (offline)

The trial UI (ADR-006) loads Vue 3 via **runtime ESM** — no npm, no bundler.
A locally vendored copy is preferred so `/web` works fully offline.

## Pin

| Item | Value |
|------|--------|
| Package | `vue` |
| Version | **3.5.13** |
| File | `vue.esm-browser.prod.js` (full build, in-browser template compiler) |
| License | MIT |
| Size | ~170 KB minified |

## One-shot download

```bash
mkdir -p web/static/vendor
curl -fsSL -o web/static/vendor/vue.esm-browser.prod.js \
  https://unpkg.com/vue@3.5.13/dist/vue.esm-browser.prod.js
```

Mirror (same pin):

```bash
curl -fsSL -o web/static/vendor/vue.esm-browser.prod.js \
  https://cdn.jsdelivr.net/npm/vue@3.5.13/dist/vue.esm-browser.prod.js
```

**Recommendation:** commit the vendored file so CI and air-gapped demos need no CDN.

## Load order (`web/static/app.js`)

1. `/web/static/vendor/vue.esm-browser.prod.js` (this directory)
2. jsDelivr pin `vue@3.5.13`
3. unpkg pin `vue@3.5.13`

If all three fail, the boot error card points here.

## Upgrade checklist

1. Bump `VUE_VERSION` in `web/static/app.js`
2. Re-download the matching `vue.esm-browser.prod.js` into this directory
3. Update ADR-006 in `plan/engineering/tech-stack.md`
4. Run the verification checklist in `plan/phases/p5-ui-01-vue-refactor.md` §7

See also: [docs/EXTERNAL_RUNTIMES.md](../../../docs/EXTERNAL_RUNTIMES.md) (Vue row + vendor note).
