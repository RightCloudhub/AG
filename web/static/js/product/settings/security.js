// @ts-check
import { ref, computed, onMounted, onUnmounted } from '../../../vendor/vue.esm-browser.prod.js';
import { useRouter } from '../../../vendor/vue-router.esm-browser.prod.js';
import { request } from '../../transport.js';
import { rows } from '../../responses.js';
import { P } from '../copy.js';
import { useForm, passwordStrength } from '../forms.js';
import { clearSession } from '../session.js';
import { useConfirmation } from '../confirm.js';
import { parseSession, dateTime } from './data.js';

export default {
  setup() {
    const router = useRouter(), form = useForm(), confirm = useConfirmation();
    const passwords = ref({ current_password: '', password: '', confirmation: '' });
    const strength = computed(() => passwordStrength(passwords.value.password));
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseSession>[]>} */
    const sessions = ref([]);
    const loading = ref(true);
    async function load() { sessions.value = rows((await request('/v1/auth/sessions')).data, parseSession); }
    async function changePassword() {
      form.errors.value = {};
      if (!passwords.value.current_password) form.errors.value.current_password = P.fields.required;
      if (strength.value < 2) form.errors.value.password = P.fields.weak_password;
      if (passwords.value.password !== passwords.value.confirmation) form.errors.value.confirmation = P.mismatch;
      if (Object.keys(form.errors.value).length) { form.focusError(); return; }
      await form.run(async () => {
        const { current_password, password } = passwords.value;
        await request('/v1/auth/password/change', { method: 'POST', body: JSON.stringify({ current_password, password }) });
        clearSession(true, '/login?updated=password');
        await router.replace('/login?updated=password');
      });
    }
    /** @param {ReturnType<typeof parseSession>} row */
    function revoke(row) {
      confirm.ask({ title: P.revokeSession, subject: `${row.current ? P.currentSession : P.otherSession} · ${dateTime(row.created_at)}`, body: P.revokeSessionBody, action: async () => {
        await request(`/v1/auth/sessions/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
        if (row.current) { clearSession(); await router.replace('/login'); } else await load();
      } });
    }
    onMounted(() => form.run(load).finally(() => { loading.value = false; }));
    onUnmounted(() => { passwords.value = { current_password: '', password: '', confirmation: '' }; });
    return { P, passwords, strength, sessions, loading, ...form, ...confirm, dateTime, changePassword, revoke, load };
  },
  template: `<div><header class="settings-title"><h2>{{ P.settingsNav.security }}</h2><p>{{ P.securityIntro }}</p></header>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }} <button type="button" class="btn btn--ghost" @click="run(load)">{{ P.retry }}</button></p>
    <section class="settings-block"><div><h3>{{ P.changePassword }}</h3><p>{{ P.passwordHint }}</p></div>
      <form class="product-form" novalidate @submit.prevent="changePassword" :aria-busy="busy">
        <div class="form-field"><label for="current-password">{{ P.currentPassword }}</label><input id="current-password" v-model="passwords.current_password" type="password" autocomplete="current-password" required maxlength="128" :disabled="busy" :aria-invalid="!!errors.current_password" aria-describedby="current-password-error"><span id="current-password-error" class="field-error">{{ errors.current_password }}</span></div>
        <div class="form-field"><label for="new-password">{{ P.newPassword }}</label><input id="new-password" v-model="passwords.password" type="password" autocomplete="new-password" required maxlength="128" :disabled="busy" :aria-invalid="!!errors.password" aria-describedby="new-password-error"><span id="new-password-error" class="field-error">{{ errors.password }}</span><p class="hint">{{ P.strength[strength] }}</p></div>
        <div class="form-field"><label for="confirm-password">{{ P.confirmation }}</label><input id="confirm-password" v-model="passwords.confirmation" type="password" autocomplete="new-password" required maxlength="128" :disabled="busy" :aria-invalid="!!errors.confirmation" aria-describedby="confirm-password-error"><span id="confirm-password-error" class="field-error">{{ errors.confirmation }}</span></div>
        <button type="submit" class="btn btn--primary settings-save" :disabled="busy">{{ busy ? P.saving : P.changePassword }}</button>
      </form></section>
    <section class="settings-list"><div class="settings-list__heading"><div><h3>{{ P.sessionsTitle }}</h3><p>{{ P.sessionsIntro }}</p></div><span class="dim">{{ sessions.length }}</span></div>
      <p v-if="loading" class="settings-empty" role="status">{{ P.loading }}</p>
      <div v-else-if="sessions.length" class="table-scroll"><table class="product-table"><thead><tr><th>{{ P.session }}</th><th>{{ P.createdAt }}</th><th>{{ P.expiresAt }}</th><th>{{ P.actions }}</th></tr></thead>
        <tbody><tr v-for="row in sessions" :key="row.id"><td :data-label="P.session">{{ row.current ? P.currentSession : P.otherSession }}</td><td :data-label="P.createdAt">{{ dateTime(row.created_at) }}</td><td :data-label="P.expiresAt">{{ dateTime(row.expires_at) }}</td><td><button type="button" class="btn btn--ghost" @click="revoke(row)" :disabled="busy">{{ P.revoke }}</button></td></tr></tbody></table></div>
      <p v-else class="settings-empty">{{ P.noSessions }}</p>
    </section><confirm-dialog v-if="confirmation" :request="confirmation" :close="dismiss"></confirm-dialog></div>`,
};
