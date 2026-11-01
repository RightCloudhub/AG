// @ts-check
import { ref, nextTick } from '../../vendor/vue.esm-browser.prod.js';
import { ApiError } from '../http.js';
import { isRecord } from '../contracts.js';
import { P } from './copy.js';

/** @param {unknown} error */
export function errorText(error) {
  if (!(error instanceof ApiError)) return P.error.network;
  return /** @type {Record<string, string>} */ (P.error)[error.code] || P.error.unexpected;
}
/** @param {string} password */
export function passwordStrength(password) {
  if (!password) return 0;
  if (password.length > 128) return 1;
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^\w\s]/].filter(pattern => pattern.test(password)).length;
  return password.length >= 20 ? 3 : password.length >= 12 && kinds >= 3 ? 2 : 1;
}
/** @param {string} mode @param {{email: string, password: string, confirmation: string, display_name: string}} data */
export function validateAuth(mode, data) {
  /** @type {Record<string, string>} */
  const errors = {};
  if (mode !== 'reset' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email.trim())) errors.email = P.fields.invalid_email;
  if (mode === 'register' && (!data.display_name.trim() || data.display_name.trim().length > 120)) errors.display_name = P.fields.invalid_name;
  if (mode === 'login' && !data.password) errors.password = P.fields.required;
  if (['register', 'reset'].includes(mode)) {
    if (passwordStrength(data.password) < 2) errors.password = P.fields.weak_password;
    if (data.password !== data.confirmation) errors.confirmation = P.mismatch;
  }
  return errors;
}
export function useForm() {
  const busy = ref(false), message = ref(''), notice = ref('');
  /** @type {import('../../vendor/vue.esm-browser.prod.js').Ref<Record<string, string>>} */
  const errors = ref({});
  /** @param {unknown} error */
  function failed(error) {
    if (error instanceof Error && error.name === 'AbortError') return;
    message.value = errorText(error);
    if (error instanceof ApiError && isRecord(error.details) && isRecord(error.details.field_errors)) {
      const labels = /** @type {Record<string, string>} */ (P.fields);
      errors.value = Object.fromEntries(Object.entries(error.details.field_errors).map(([key, value]) => [key, labels[String(value)] || P.fields.invalid_value]));
    }
    focusError();
  }
  function focusError() { nextTick(() => document.querySelector('[aria-invalid="true"]') instanceof HTMLElement && /** @type {HTMLElement} */ (document.querySelector('[aria-invalid="true"]')).focus()); }
  /** @param {() => Promise<unknown>} action */
  async function run(action) {
    if (busy.value) return;
    busy.value = true; message.value = ''; notice.value = ''; errors.value = {};
    try { await action(); } catch (error) { failed(error); }
    finally { busy.value = false; }
  }
  return { busy, message, notice, errors, failed, focusError, run };
}
