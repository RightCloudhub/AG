// @ts-check
export const PRODUCT_TOPBAR_TEMPLATE = `<header class="topbar product-topbar">
  <div class="topbar__left"><button type="button" class="btn btn--icon btn--ghost menu-btn" @click="openNotebook" :aria-label="P.menu"><ag-icon name="menu"></ag-icon></button>
    <router-link to="/app/chat" class="topbar__brand" :title="P.brand"><ag-icon name="mark" :size="22"></ag-icon><span>{{ P.brand }}</span></router-link>
    <button class="tenant-trigger" type="button" popovertarget="tenant-menu" :disabled="changingIdentity" :aria-label="P.switchTenant + ' · ' + tenant?.name"><span>{{ tenant?.name }}</span><ag-icon name="chevronDown" :size="14"></ag-icon></button>
    <knowledge-select></knowledge-select>
  </div>
  <button type="button" class="topbar__search" @click="openPaletteDialog" :aria-label="P.search"><ag-icon name="search" :size="14"></ag-icon><span>{{ P.search }}</span><kbd>⌘K</kbd></button>
  <div class="topbar__right"><label for="top-mode" class="sr-only">{{ T.topbar.mode }}</label><select id="top-mode" class="select select--quiet mode-select" :value="mode" @change="setMode($event.target.value)"><option v-for="item in modeOptions" :key="item.id" :value="item.id">{{ item.label }}</option></select>
    <span class="health" :class="'health--' + healthKind" tabindex="0" role="status" :aria-label="healthTitle"><span class="health__dot" aria-hidden="true"></span><span class="popover">{{ healthTitle }}</span></span>
    <button type="button" class="user-trigger" popovertarget="user-menu" :aria-label="P.accountMenu + ' · ' + user?.display_name"><span class="user-initial" aria-hidden="true">{{ initial(user?.display_name || '') }}</span><span class="user-trigger__name">{{ user?.display_name }}</span><ag-icon name="chevronDown" :size="14"></ag-icon></button>
  </div>
  <div id="tenant-menu" class="identity-popover tenant-popover" popover><h2>{{ P.switchTenant }}</h2><div class="tenant-options">
    <button v-for="space in user?.tenants" :key="space.id" type="button" class="tenant-option" :aria-current="space.id === user?.tenant_id ? 'true' : null" @click="chooseTenant(space.id)" :disabled="changingIdentity"><span><strong>{{ space.name }}</strong><small>{{ P.roles[space.role] }}</small></span><ag-icon v-if="space.id === user?.tenant_id" name="check"></ag-icon></button></div>
    <button type="button" class="identity-popover__action" @click="openCreateTenant"><ag-icon name="plus"></ag-icon>{{ P.createTenant }}</button></div>
  <div id="user-menu" class="identity-popover user-popover" popover><div class="identity-popover__user"><strong>{{ user?.display_name }}</strong><span>{{ user?.email }}</span></div>
    <nav :aria-label="P.accountMenu"><router-link to="/app/settings/profile" @click="closePopovers"><ag-icon name="settings"></ag-icon>{{ P.settingsNav.profile }}</router-link>
      <button type="button" @click="toggleTheme"><ag-icon name="contrast"></ag-icon>{{ P.theme }}</button>
      <router-link to="/app/settings/security" @click="closePopovers"><ag-icon name="settings"></ag-icon>{{ P.settingsNav.security }}</router-link></nav>
    <button type="button" class="identity-popover__action" @click="signOut" :disabled="busy">{{ P.signOut }}</button></div>
</header>`;
