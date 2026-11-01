// @ts-check
import { ref, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { request } from '../transport.js';
import { record, text } from '../responses.js';
import { switchTenant } from './session.js';
import { useForm } from './forms.js';
import { P } from './copy.js';
export default {
  props: { close: { type: Function, required: true } },
  /** @param {{close: () => void}} props */
  setup(props) {
    const router = useRouter(), form = useForm(), name = ref('');
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<HTMLDialogElement | null>} */
    const dialog = ref(null);
    const previous = document.activeElement;
    function close() { if (!form.busy.value) props.close(); }
    async function create() {
      if (!name.value.trim() || name.value.trim().length > 120) { form.errors.value = { name: P.fields.invalid_name }; form.focusError(); return; }
      await form.run(async () => {
        const result = await request('/v1/tenants', { method: 'POST', body: JSON.stringify({ name: name.value.trim() }) });
        await switchTenant(text(record(result.data).id));
        props.close(); await router.replace('/app/chat');
      });
    }
    onMounted(() => dialog.value?.showModal());
    onUnmounted(() => { dialog.value?.close(); if (previous instanceof HTMLElement) previous.focus(); });
    return { P, dialog, name, ...form, close, create };
  },
  template: `<dialog ref="dialog" class="product-dialog" aria-labelledby="create-tenant-title" @cancel.prevent="close"><h2 id="create-tenant-title">{{ P.createTenant }}</h2><p class="dialog-intro">{{ P.createTenantHint }}</p>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }}</p><form class="product-form" @submit.prevent="create" novalidate>
      <div class="form-field"><label for="create-tenant-name">{{ P.tenantName }}</label><input id="create-tenant-name" v-model="name" required maxlength="120" autofocus :disabled="busy" :aria-invalid="!!errors.name" aria-describedby="create-tenant-error"><span id="create-tenant-error" class="field-error">{{ errors.name }}</span></div>
      <div class="product-dialog__actions"><button type="button" class="btn btn--secondary" @click="close" :disabled="busy">{{ P.cancel }}</button><button type="submit" class="btn btn--primary" :disabled="busy">{{ busy ? P.saving : P.createAndEnter }}</button></div></form></dialog>`,
};
