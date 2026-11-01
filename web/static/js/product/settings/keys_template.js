// @ts-check
export const KEYS_TEMPLATE = `<div>
  <header class="settings-title"><h2>{{ P.settingsNav['api-keys'] }}</h2><p>{{ P.keysIntro }}</p></header>
  <p v-if="message" class="form-message form-message--error" role="alert">{{ message }} <button type="button" class="btn btn--ghost" @click="run(load)">{{ P.retry }}</button></p>
  <section v-if="secret" class="secret-panel" aria-live="polite"><h3>{{ P.keyOnce }}</h3><p>{{ P.keyOnceHint }}</p><label for="new-service-key" class="sr-only">{{ P.keyOnce }}</label>
    <input id="new-service-key" class="secret-value" :value="secret" readonly autocomplete="off" spellcheck="false" @focus="$event.target.select()">
    <p v-if="copyNotice" role="status">{{ copyNotice }}</p><div class="row-actions"><button type="button" class="btn btn--secondary" @click="copySecret">{{ P.copy }}</button><button type="button" class="btn btn--primary" @click="closeSecret">{{ P.keyStored }}</button></div></section>
  <section v-else class="settings-block"><div><h3>{{ P.createKey }}</h3><p>{{ P.roleHelp[role] }}</p></div><form class="product-form" novalidate @submit.prevent="create" :aria-busy="busy">
    <div class="form-field"><label for="key-name">{{ P.keyName }}</label><input id="key-name" v-model="name" required maxlength="120" :placeholder="P.keyNameHint" :disabled="busy" :aria-invalid="!!errors.name" aria-describedby="key-name-error"><span id="key-name-error" class="field-error">{{ errors.name }}</span></div>
    <div class="form-columns"><div class="form-field"><label for="key-role">{{ P.role }}</label><select id="key-role" v-model="role" :disabled="busy"><option v-for="item in roles" :key="item.value" :value="item.value">{{ item.label }}</option></select></div>
      <div class="form-field"><label for="key-expiry">{{ P.expiry }}</label><select id="key-expiry" v-model="days" :disabled="busy"><option v-for="item in expiries" :key="item.value" :value="item.value">{{ item.label }}</option></select><span class="field-error">{{ errors.expires_at }}</span></div></div>
    <button type="submit" class="btn btn--primary settings-save" :disabled="busy">{{ busy ? P.saving : P.createKey }}</button></form>
  </section>
  <section class="settings-list"><div class="settings-list__heading"><h3>{{ P.settingsNav['api-keys'] }}</h3><span class="dim">{{ keys.length }}</span></div>
    <p v-if="loading" class="settings-empty" role="status">{{ P.loading }}</p>
    <div v-else-if="keys.length" class="table-scroll"><table class="product-table"><thead><tr><th>{{ P.keyName }}</th><th>{{ P.role }}</th><th>{{ P.keyStatus }}</th><th>{{ P.lastSeen }}</th><th>{{ P.expiresAt }}</th><th>{{ P.actions }}</th></tr></thead>
      <tbody><tr v-for="key in keys" :key="key.id"><td><strong>{{ key.name }}</strong><small class="key-prefix">{{ key.prefix }}…</small></td><td :data-label="P.role">{{ P.roles[key.role] }}</td><td :data-label="P.keyStatus"><span class="status-label" :data-state="keyStatus(key)">{{ P[keyStatus(key)] }}</span></td>
        <td :data-label="P.lastSeen">{{ dateTime(key.last_used_at) }}</td><td :data-label="P.expiresAt">{{ key.expires_at === null ? P.never : dateTime(key.expires_at) }}</td><td><button v-if="key.revoked_at === null" type="button" class="btn btn--ghost" @click="revoke(key)" :disabled="busy">{{ P.revoke }}</button><span v-else>{{ P.unknown }}</span></td></tr></tbody></table></div>
    <div v-else class="settings-empty"><h4>{{ P.noKeys }}</h4><p>{{ P.noKeysHint }}</p></div>
  </section><confirm-dialog v-if="confirmation" :request="confirmation" :close="dismiss"></confirm-dialog>
</div>`;
