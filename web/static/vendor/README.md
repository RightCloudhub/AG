# Vendored Vue 3 runtime

This directory holds the pinned Vue 3 ESM-browser runtime build, used by
the trial UI (`web/static/app.js`) as the **first-source** before CDN
fallbacks. Once vendored here, the UI works **fully offline** — no npm,
no network, no build tooling.

## What to vendor

Vue **3.5.13**, full build with in-browser template compiler
(`vue.esm-browser.prod.js`).

## How to vendor

```bash
mkdir -p web/static/vendor
curl -fsSL -o web/static/vendor/vue.esm-browser.prod.js \
  https://unpkg.com/vue@3.5.13/dist/vue.esm-browser.prod.js
```

- License: MIT (see Vue license at https://github.com/vuejs/core/blob/main/LICENSE)
- Size: ~170 KB (minified, gzipped ~55 KB)

## Why vendor instead of CDN-only

1. **Deterministic offline dev**: the offline dev loop (`--no-llm`) must
   not require network access.
2. **CDN reliability**: this environment's egress gateway is prone to
   HTTP 403 against jsdelivr/unpkg; vendor = permanent offline.
3. **Pinned version**: avoids surprise breaking changes from unpinned CDN
   URLs.

## Upgrade flow

When upgrading Vue, change the version in **three** places simultaneously:

1. `web/static/app.js` — `VUE_VERSION` constant
2. This file — the `curl` URL above
3. `web/static/vendor/vue.esm-browser.prod.js` — the actual vendored file

Then run the §7 verification checklist in `plan/phases/p5-ui-01-vue-refactor.md`.

## Cross-reference

- `plan/engineering/tech-stack.md` — ADR-006 (Vue 3 selection)
- `docs/EXTERNAL_RUNTIMES.md` — Vue 3 runtime section (§3)
- `web/static/app.js` — boot loader with vendor-first + CDN fallback
