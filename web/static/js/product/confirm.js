// @ts-check
import { ref, onMounted, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { P } from './copy.js';
import { useForm } from './forms.js';

/** @typedef {{title: string, subject: string, body: string, action: () => Promise<void>}} Confirmation */
export function useConfirmation() {
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<Confirmation | null>} */
  const confirmation = ref(null);
  /** @param {Confirmation} value */
  function ask(value) { confirmation.value = value; }
  function dismiss() { confirmation.value = null; }
  return { confirmation, ask, dismiss };
}
export const ConfirmDialog = {
  props: { request: { type: Object, required: true }, close: { type: Function, required: true } },
  /** @param {{request: Confirmation, close: () => void}} props */
  setup(props) {
    const form = useForm();
    /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<HTMLDialogElement | null>} */
    const dialog = ref(null);
    const previous = document.activeElement;
    function close() { if (!form.busy.value) props.close(); }
    async function submit() { await form.run(async () => { await props.request.action(); props.close(); }); }
    onMounted(() => dialog.value?.showModal());
    onUnmounted(() => { dialog.value?.close(); if (previous instanceof HTMLElement) previous.focus(); });
    return { P, request: props.request, dialog, ...form, close, submit };
  },
  template: `<dialog ref="dialog" class="product-dialog" aria-labelledby="confirmation-title" aria-describedby="confirmation-body" @cancel.prevent="close">
    <h2 id="confirmation-title">{{ request.title }}</h2><p class="confirmation-subject">{{ request.subject }}</p><p id="confirmation-body" class="dim">{{ request.body }}</p>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }}</p>
    <div class="product-dialog__actions"><button type="button" class="btn btn--secondary" autofocus @click="close" :disabled="busy">{{ P.cancel }}</button>
      <button type="button" class="btn btn--danger" @click="submit" :disabled="busy">{{ busy ? P.saving : P.confirm }}</button></div></dialog>`,
};
export default ConfirmDialog;
