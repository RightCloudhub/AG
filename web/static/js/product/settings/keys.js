// @ts-check
import { ref, onMounted, onUnmounted } from '../../../vendor/vue.esm-browser.prod.js';
import { request } from '../../transport.js';
import { rows, record, text } from '../../responses.js';
import { copyText } from '../../chat/presenters.js';
import { P } from '../copy.js';
import { useForm } from '../forms.js';
import { useConfirmation } from '../confirm.js';
import { parseKey, keyStatus, roles, tenantPath, dateTime, SECONDS_PER_DAY } from './data.js';
import { KEYS_TEMPLATE } from './keys_template.js';

export default {
  setup() {
    const form = useForm(), confirm = useConfirmation();
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseKey>[]>} */
    const keys = ref([]);
    const name = ref(''), role = ref('reader'), days = ref('30'), secret = ref(''), copyNotice = ref('');
    const loading = ref(true);
    const expiries = [{ value: '30', label: P.days30 }, { value: '90', label: P.days90 }, { value: '365', label: P.days365 }, { value: '0', label: P.never }];
    async function load() { keys.value = rows((await request(tenantPath('/api-keys'))).data, parseKey); }
    async function create() {
      if (!name.value.trim() || name.value.trim().length > 120) { form.errors.value = { name: P.fields.invalid_name }; form.focusError(); return; }
      await form.run(async () => {
        const duration = Number(days.value);
        const result = await request(tenantPath('/api-keys'), { method: 'POST', body: JSON.stringify({ name: name.value.trim(), role: role.value, expires_at: duration ? Date.now() / 1000 + duration * SECONDS_PER_DAY : null }) });
        secret.value = text(record(result.data).key);
        copyNotice.value = ''; name.value = ''; await load();
      });
    }
    /** @param {ReturnType<typeof parseKey>} key */
    function revoke(key) {
      confirm.ask({ title: P.revokeKey, subject: key.name, body: P.revokeKeyBody, action: async () => {
        await request(tenantPath(`/api-keys/${encodeURIComponent(key.id)}`), { method: 'DELETE' });
        await load();
      } });
    }
    async function copySecret() { copyNotice.value = await copyText(secret.value) ? P.copied : P.copyFailed; }
    function closeSecret() { secret.value = ''; copyNotice.value = ''; }
    onMounted(() => form.run(load).finally(() => { loading.value = false; }));
    onUnmounted(closeSecret);
    return { P, keys, name, role, days, roles, expiries, secret, copyNotice, loading, ...form, ...confirm, dateTime, keyStatus, load, create, revoke, copySecret, closeSecret };
  },
  template: KEYS_TEMPLATE,
};
