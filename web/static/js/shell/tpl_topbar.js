// @ts-check
/**
 * Shell fragments (§4.2, §8): the 48px top bar — brand and scope, the ⌘K trigger, the mode select,
 * health, the theme toggle, settings — the settings dialog, and the connection banner.
 */

export const TOPBAR_TEMPLATE = `
<header class="topbar">
  <div class="topbar__left">
    <button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="T.notebook.open"><ag-icon name="menu"></ag-icon></button>
    <a class="topbar__brand" href="#chat" :title="T.app.title + ' · ' + T.app.subtitle"><ag-icon name="mark" :size="20"></ag-icon><span>{{ T.app.subtitle }}</span></a>
    <span class="topbar__scope" v-if="user"><ag-icon name="knowledge" :size="14"></ag-icon>{{ T.topbar.scope }}<b>{{ user.tenant_id || 'default' }}</b></span>
  </div>
  <button type="button" class="topbar__search" @click="openPalette" :aria-label="T.topbar.searchAria">
    <ag-icon name="search" :size="14"></ag-icon><span>{{ T.topbar.search }}</span><kbd>⌘K</kbd>
  </button>
  <div class="topbar__right">
    <label class="sr-only" for="top-mode">{{ T.topbar.mode }}</label>
    <select id="top-mode" class="select select--quiet mode-select" :value="mode" @change="setMode($event.target.value)">
      <option v-for="m in modeOptions" :key="m.id" :value="m.id">{{ m.label }}</option>
    </select>
    <span class="health" :class="'health--' + healthKind" tabindex="0" role="status" :aria-label="healthTitle">
      <span class="health__dot" aria-hidden="true"></span><span class="popover">{{ healthTitle }}</span></span>
    <button type="button" class="btn btn--icon btn--ghost" @click="toggleTheme" :aria-label="T.topbar.theme" :title="T.topbar.theme">
      <ag-icon name="contrast"></ag-icon></button>
    <button type="button" class="btn btn--icon btn--ghost" @click="openSettings" :aria-label="T.topbar.settings" :title="T.topbar.settings">
      <ag-icon name="settings"></ag-icon></button>
  </div>
</header>`;

export const BANNER_TEMPLATE = `
<div v-if="offline" class="banner banner--err" role="alert">
  <ag-icon name="warning" :size="14"></ag-icon><span>{{ T.states.offline }}</span>
  <button type="button" class="btn btn--xs btn--secondary" @click="probe">{{ T.states.retry }}</button>
</div>`;

export const SETTINGS_TEMPLATE = `
<div v-if="settingsOpen" class="modal-backdrop" @click.self="closeSettings">
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title" @keydown.esc="closeSettings">
    <header class="dialog__head"><h2 id="settings-title" class="dialog__title">{{ T.settings.title }}</h2>
      <button type="button" class="btn btn--icon btn--ghost" @click="closeSettings" :aria-label="T.common.close"><ag-icon name="close"></ag-icon></button></header>
    <div class="dialog__body field-stack">
      <label class="field"><span class="field__label">{{ T.settings.apiKey }}</span>
        <input ref="settingsFirstEl" class="input" type="password" autocomplete="off" :placeholder="T.settings.apiKeyPlaceholder"
               :value="apiKey" @change="updateApiKey($event.target.value)" aria-describedby="settings-key-hint">
        <span id="settings-key-hint" class="hint">{{ T.settings.apiKeyHint }}</span></label>
      <label class="field"><span class="field__label">{{ T.settings.hops }}</span>
        <input class="input" type="number" :min="UiPolicy.MIN_MAX_HOPS" :max="UiPolicy.MAX_MAX_HOPS"
               :value="preferences.maxHops" @change="setMaxHops($event.target.value)" aria-describedby="settings-hops-hint">
        <span id="settings-hops-hint" class="hint">{{ T.settings.hopsHint }}</span></label>
      <div class="field"><span class="field__label">{{ T.settings.theme }}</span>
        <span class="hint">{{ T.settings.themeHint }} · {{ theme === 'dark' ? T.topbar.themeDark : T.topbar.themeLight }}</span></div>
      <div class="field"><span class="field__label">{{ T.settings.language }}</span>
        <span class="hint">{{ T.settings.languageValue }} · {{ T.settings.languageHint }}</span></div>
      <div class="field"><span class="field__label">{{ T.settings.scope }}</span>
        <span class="hint">{{ user?.user_id || '-' }} · {{ user?.tenant_id || 'default' }} · {{ user?.role || Role.READER }}</span></div>
      <div class="field"><span class="field__label">{{ T.settings.shortcuts }}</span>
        <dl class="log__kv">
          <dt><kbd>⌘K</kbd></dt><dd class="dim">{{ T.shortcuts.palette }}</dd>
          <dt><kbd>⌘N</kbd></dt><dd class="dim">{{ T.shortcuts.newQuestion }}</dd>
          <dt><kbd>↵</kbd></dt><dd class="dim">{{ T.shortcuts.send }}</dd>
          <dt><kbd>Esc</kbd></dt><dd class="dim">{{ T.shortcuts.stop }}</dd>
          <dt><kbd>[ ]</kbd></dt><dd class="dim">{{ T.shortcuts.prevHop }} / {{ T.shortcuts.nextHop }}</dd>
          <dt><kbd>E</kbd></dt><dd class="dim">{{ T.shortcuts.inspector }}</dd>
          <dt><kbd>⌘⇧L</kbd></dt><dd class="dim">{{ T.shortcuts.theme }}</dd>
        </dl></div>
    </div>
    <footer class="dialog__foot"><button type="button" class="btn btn--primary" @click="closeSettings">{{ T.settings.close }}</button></footer>
  </div>
</div>`;

export const PALETTE_TEMPLATE = `
<div v-if="paletteOpen" class="modal-backdrop" @click.self="closePalette">
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="palette-title" @keydown="paletteKeydown">
    <header class="dialog__head"><h2 id="palette-title" class="sr-only">{{ T.palette.title }}</h2>
      <span class="hint">{{ T.palette.hints }}</span></header>
    <div class="dialog__body field-stack">
      <input ref="paletteFirstEl" class="input" :placeholder="T.palette.placeholder" :aria-label="T.palette.title"
             v-model="paletteQuery" @input="onPaletteInput" role="combobox" aria-autocomplete="list"
             :aria-expanded="paletteResults.length > 0" aria-controls="palette-list">
      <ul id="palette-list" class="menu menu--static" role="listbox" :aria-label="T.palette.title">
        <li v-for="(r, i) in paletteResults" :key="r.id" role="option" :aria-selected="i === paletteActive"
            class="menu__item" :class="{ 'menu__item--active': i === paletteActive }" @mousedown.prevent="runPalette(r)">
          <ag-icon :name="r.icon" :size="14"></ag-icon><span>{{ r.label }}</span><span class="hint">{{ r.kindLabel }}</span></li>
        <li v-if="!paletteResults.length" class="menu__empty hint">{{ T.palette.empty }}</li>
      </ul>
    </div>
  </div>
</div>`;
