// @ts-check
import { ref, computed, onMounted } from '../../vendor/vue.esm-browser.prod.js';
import { useRoute, useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { request } from '../transport.js';
import { record, text } from '../responses.js';
import { toggleTheme } from '../theme.js';
import { P } from './copy.js';
import { publicFrame } from './public_frame.js';
import { useForm } from './forms.js';
import { user, logout, switchTenant } from './session.js';

export default {
  setup() {
    const route = useRoute(), router = useRouter(), form = useForm();
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<{tenant_name: string, email: string, role: string} | null>} */
    const invitation = ref(null);
    const available = ref(true);
    const path = `/v1/invites/${encodeURIComponent(String(route.params.token))}`;
    const next = computed(() => route.fullPath);
    const matches = computed(() => user.value?.email.toLowerCase() === invitation.value?.email.toLowerCase());
    async function accept() {
      await form.run(async () => {
        const result = await request(`${path}/accept`, { method: 'POST' });
        await switchTenant(text(record(result.data).tenant_id));
        await router.replace('/app/chat');
      });
    }
    async function changeAccount() { await form.run(async () => { await logout(); await router.replace({ path: '/login', query: { next: next.value } }); }); }
    onMounted(() => form.run(async () => {
      try {
        const data = record((await request(path)).data);
        invitation.value = { tenant_name: text(data.tenant_name), email: text(data.email), role: text(data.role) };
      } catch { available.value = false; }
    }));
    return { P, user, invitation, available, matches, next, ...form, accept, changeAccount, toggleTheme };
  },
  template: publicFrame(`<header class="auth-heading"><h2>{{ P.inviteTitle }}</h2><p>{{ P.inviteIntro }}</p></header>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }}</p>
    <p v-if="busy && !invitation" role="status">{{ P.loading }}</p>
    <p v-if="!available" class="form-message form-message--error" role="alert">{{ P.inviteUnavailable }}</p>
    <div v-if="invitation" class="invite-details"><h3>{{ invitation.tenant_name }}</h3>
      <dl><dt>{{ P.inviteFor }}</dt><dd>{{ invitation.email }}</dd><dt>{{ P.invitedRole }}</dt><dd>{{ P.roles[invitation.role] }}</dd></dl>
      <button v-if="user && matches" type="button" class="btn btn--primary auth-submit" :disabled="busy" @click="accept">{{ busy ? P.loading : P.acceptInvite }}</button>
      <template v-else-if="user"><p class="form-message">{{ P.inviteMismatch }}</p><button type="button" class="btn btn--secondary auth-submit" @click="changeAccount" :disabled="busy">{{ P.signOut }}</button></template>
      <template v-else><router-link class="btn btn--primary auth-submit" :to="{path: '/login', query: {next}}">{{ P.inviteLogin }}</router-link>
      <router-link class="auth-footer" :to="{path: '/register', query: {next}}">{{ P.inviteRegister }}</router-link></template></div>`),
};
