// @ts-check
import { computed, ref, onUnmounted } from '../../vendor/vue.esm-browser.prod.js';
import { useRoute, useRouter } from '../../vendor/vue-router.esm-browser.prod.js';
import { request } from '../transport.js';
import { toggleTheme } from '../theme.js';
import { P } from './copy.js';
import { authenticate, clearSession } from './session.js';
import { safeNext } from './contracts.js';
import { passwordStrength, useForm, validateAuth } from './forms.js';
import { AUTH_TEMPLATE } from './auth_template.js';
import { publicFrame } from './public_frame.js';

export default {
  props: { mode: { type: String, default: 'login' } },
  /** @param {{mode: string}} props */
  setup(props) {
    const route = useRoute(), router = useRouter(), form = useForm();
    const mode = computed(() => props.mode);
    const fields = ref({ email: '', display_name: '', password: '', confirmation: '' });
    const token = computed(() => typeof route.query.token === 'string' ? route.query.token : '');
    const nextPath = computed(() => safeNext(route.query.next));
    const screen = computed(() => /** @type {Record<string, {title: string, subtitle: string, submit: string, busy: string}>} */ (P.auth)[mode.value]);
    const strength = computed(() => passwordStrength(fields.value.password));
    if (mode.value === 'reset' && !token.value) form.message.value = P.resetMissing;
    if (mode.value === 'login' && route.query.updated === 'password') form.notice.value = P.resetDone;

    async function submit() {
      form.errors.value = validateAuth(mode.value, fields.value);
      if (Object.keys(form.errors.value).length) { form.focusError(); return; }
      await form.run(async () => {
        const { email, password, display_name } = fields.value;
        if (mode.value === 'login' || mode.value === 'register') {
          await authenticate(mode.value, mode.value === 'login' ? { email: email.trim(), password } : { email: email.trim(), password, display_name: display_name.trim() });
          await router.replace(nextPath.value);
        } else if (mode.value === 'forgot') {
          await request('/v1/auth/forgot', { method: 'POST', body: JSON.stringify({ email: email.trim() }) });
          form.notice.value = P.forgotSent;
        } else {
          await request('/v1/auth/reset', { method: 'POST', body: JSON.stringify({ token: token.value, password }) });
          clearSession();
          await router.replace('/login?updated=password');
        }
        fields.value.password = ''; fields.value.confirmation = '';
      });
    }
    onUnmounted(() => { fields.value.password = ''; fields.value.confirmation = ''; });
    return { P, mode, fields, token, nextPath, screen, strength, ...form, submit, toggleTheme };
  },
  template: publicFrame(AUTH_TEMPLATE),
};
