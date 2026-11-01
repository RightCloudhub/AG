// @ts-check
import { ref, onMounted } from '../../../vendor/vue.esm-browser.prod.js';
import { useRouter } from '../../../vendor/vue-router.esm-browser.prod.js';
import { request } from '../../transport.js';
import { P } from '../copy.js';
import { reloadSession, switchTenant } from '../session.js';
import { useForm } from '../forms.js';
import { parseTenant, tenantPath, dateTime } from './data.js';

export default {
  setup() {
    const router = useRouter(), form = useForm();
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseTenant> | null>} */
    const tenant = ref(null);
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseTenant> | null>} */
    const created = ref(null);
    const name = ref(''), newName = ref('');
    async function load() { tenant.value = parseTenant((await request(tenantPath())).data); name.value = tenant.value.name; }
    /** @param {boolean} [creating] */
    async function save(creating = false) {
      const value = creating ? newName.value : name.value;
      if (!value.trim() || value.trim().length > 120) { form.errors.value = { [creating ? 'new_name' : 'name']: P.fields.invalid_name }; form.focusError(); return; }
      await form.run(async () => {
        const result = await request(creating ? '/v1/tenants' : tenantPath(), { method: creating ? 'POST' : 'PATCH', body: JSON.stringify({ name: value.trim() }) });
        if (creating) { created.value = parseTenant(result.data); newName.value = ''; form.notice.value = P.tenantCreated; }
        else { tenant.value = parseTenant(result.data); name.value = tenant.value.name; form.notice.value = P.saved; }
        await reloadSession();
      });
    }
    async function enter() { if (created.value) await form.run(async () => { await switchTenant(created.value?.id || ''); await router.replace('/app/chat'); }); }
    onMounted(() => form.run(load));
    return { P, tenant, created, name, newName, ...form, save, enter, load, dateTime };
  },
  template: `<div><header class="settings-title"><h2>{{ P.settingsNav.tenant }}</h2><p>{{ P.tenantIntro }}</p></header>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }} <button type="button" class="btn btn--ghost" @click="run(load)">{{ P.retry }}</button></p><p v-if="notice" class="form-message" role="status">{{ notice }}</p>
    <section v-if="tenant" class="settings-block"><div><h3>{{ P.tenantDetails }}</h3><p>{{ P[tenant.kind] }}</p></div><form class="product-form" novalidate @submit.prevent="save()">
      <div class="form-field"><label for="tenant-name">{{ P.tenantName }}</label><input id="tenant-name" v-model="name" required maxlength="120" :disabled="busy" :aria-invalid="!!errors.name" aria-describedby="tenant-name-error"><span id="tenant-name-error" class="field-error">{{ errors.name }}</span></div>
      <dl class="settings-facts"><dt>{{ P.memberCount }}</dt><dd>{{ tenant.member_count }}</dd><dt>{{ P.createdAt }}</dt><dd>{{ dateTime(tenant.created_at) }}</dd><dt>{{ P.tenantId }}</dt><dd><code>{{ tenant.id }}</code></dd></dl>
      <button type="submit" class="btn btn--primary settings-save" :disabled="busy || name.trim() === tenant.name">{{ busy ? P.saving : P.save }}</button></form></section>
    <section class="settings-block"><div><h3>{{ P.createTenant }}</h3><p>{{ P.createTenantHint }}</p></div><form class="product-form" novalidate @submit.prevent="save(true)">
      <div class="form-field"><label for="new-tenant-name">{{ P.tenantName }}</label><input id="new-tenant-name" v-model="newName" required maxlength="120" :disabled="busy" :aria-invalid="!!errors.new_name" aria-describedby="new-tenant-name-error"><span id="new-tenant-name-error" class="field-error">{{ errors.new_name }}</span></div>
      <button type="submit" class="btn btn--secondary settings-save" :disabled="busy">{{ P.createTenant }}</button>
      <div v-if="created" class="created-tenant"><strong>{{ created.name }}</strong><button type="button" class="btn btn--primary" @click="enter" :disabled="busy">{{ P.enterTenant }}</button></div></form></section>
  </div>`,
};
