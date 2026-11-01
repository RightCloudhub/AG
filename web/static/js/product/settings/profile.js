// @ts-check
import { ref } from '../../../vendor/vue.esm-browser.prod.js';
import { request } from '../../transport.js';
import { theme, setTheme } from '../../theme.js';
import { P } from '../copy.js';
import { user, applyUser } from '../session.js';
import { parseUser } from '../contracts.js';
import { useForm } from '../forms.js';
import { initial } from './data.js';

export default {
  setup() {
    const form = useForm();
    const name = ref(user.value?.display_name || '');
    async function save() {
      if (!name.value.trim() || name.value.trim().length > 120) { form.errors.value = { display_name: P.fields.invalid_name }; form.focusError(); return; }
      await form.run(async () => {
        const result = await request('/v1/me', { method: 'PATCH', body: JSON.stringify({ display_name: name.value.trim() }) });
        applyUser(parseUser(result.data));
        name.value = user.value?.display_name || '';
        form.notice.value = P.saved;
      });
    }
    return { P, user, name, ...form, save, theme, setTheme, initial };
  },
  template: `<div><header class="settings-title"><h2>{{ P.settingsNav.profile }}</h2><p>{{ P.profileIntro }}</p></header>
    <p v-if="message" class="form-message form-message--error" role="alert">{{ message }}</p><p v-if="notice" class="form-message" role="status">{{ notice }}</p>
    <section class="settings-block"><div><h3>{{ P.accountSection }}</h3><div class="profile-avatar">{{ initial(user?.display_name || '') }}</div></div>
      <form class="product-form" @submit.prevent="save" novalidate :aria-busy="busy">
        <div class="form-field"><label for="profile-name">{{ P.name }}</label><input id="profile-name" v-model="name" autocomplete="nickname" maxlength="120" :disabled="busy" required :aria-invalid="!!errors.display_name" aria-describedby="profile-name-error"><span id="profile-name-error" class="field-error">{{ errors.display_name }}</span></div>
        <div class="form-field"><label for="profile-email">{{ P.email }}</label><input id="profile-email" :value="user?.email" type="email" readonly aria-describedby="profile-email-hint"><p class="hint" id="profile-email-hint">{{ P.emailReadOnly }}</p></div>
        <button type="submit" class="btn btn--primary settings-save" :disabled="busy || name.trim() === user?.display_name">{{ busy ? P.saving : P.save }}</button>
      </form></section>
    <section class="settings-block"><div><h3>{{ P.appearance }}</h3><p>{{ P.appearanceIntro }}</p></div>
      <fieldset class="theme-options"><legend class="sr-only">{{ P.appearance }}</legend><label v-for="value in ['dark', 'light']" :key="value" :data-choice="value" :class="{'theme-choice--active': theme === value}" class="theme-choice">
        <input type="radio" name="theme" :checked="theme === value" @change="setTheme(value)"><span>{{ P[value] }}</span><span class="theme-swatch" aria-hidden="true"></span></label></fieldset></section></div>`,
};
