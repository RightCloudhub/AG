// @ts-check
export const AUTH_TEMPLATE = `
<header class="auth-heading"><h2>{{ screen.title }}</h2><p>{{ screen.subtitle }}</p></header>
<p v-if="message" class="form-message form-message--error" role="alert">{{ message }}</p>
<p v-if="notice" class="form-message" role="status">{{ notice }}</p>
<form v-if="!notice || mode === 'login'" class="product-form" novalidate @submit.prevent="submit" :aria-busy="busy">
  <div v-if="mode === 'register'" class="form-field">
    <label for="auth-name">{{ P.name }}</label>
    <input id="auth-name" v-model="fields.display_name" name="name" autocomplete="nickname" maxlength="120" required :placeholder="P.nameHint"
      :aria-invalid="!!errors.display_name" aria-describedby="auth-name-error" :disabled="busy">
    <span id="auth-name-error" class="field-error">{{ errors.display_name }}</span>
  </div>
  <div v-if="mode !== 'reset'" class="form-field">
    <label for="auth-email">{{ P.email }}</label>
    <input id="auth-email" v-model="fields.email" name="email" type="email" autocomplete="username" required maxlength="254" :placeholder="P.emailHint"
      :aria-invalid="!!errors.email" aria-describedby="auth-email-error" :disabled="busy">
    <span id="auth-email-error" class="field-error">{{ errors.email }}</span>
  </div>
  <div v-if="mode !== 'forgot'" class="form-field">
    <label for="auth-password">{{ mode === 'reset' ? P.newPassword : P.password }}</label>
    <input id="auth-password" v-model="fields.password" name="password" type="password" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
      required maxlength="128" :aria-invalid="!!errors.password" :aria-describedby="mode === 'login' ? 'auth-password-error' : 'password-hint auth-password-error'" :disabled="busy">
    <span id="auth-password-error" class="field-error">{{ errors.password }}</span>
    <template v-if="mode !== 'login'"><p id="password-hint" class="hint">{{ P.passwordHint }}</p>
      <div class="password-strength" :data-strength="strength"><span>{{ P.strength[strength] }}</span><meter min="0" max="3" :value="strength" :aria-label="P.strength[strength]"></meter></div></template>
  </div>
  <div v-if="mode === 'register' || mode === 'reset'" class="form-field">
    <label for="auth-confirm">{{ P.confirmation }}</label>
    <input id="auth-confirm" v-model="fields.confirmation" name="confirmation" type="password" autocomplete="new-password" required maxlength="128"
      :aria-invalid="!!errors.confirmation" aria-describedby="auth-confirm-error" :disabled="busy">
    <span id="auth-confirm-error" class="field-error">{{ errors.confirmation }}</span>
  </div>
  <router-link v-if="mode === 'login'" to="/forgot" class="auth-forgot">{{ P.forgot }}</router-link>
  <button class="btn btn--primary auth-submit" type="submit" :disabled="busy || (mode === 'reset' && !token)">
    <span v-if="busy" class="spinner"></span>{{ busy ? screen.busy : screen.submit }}<ag-icon v-if="!busy" name="arrowRight"></ag-icon></button>
</form>
<p class="auth-footer" v-if="mode === 'login'">{{ P.noAccount }} <router-link :to="{path: '/register', query: {next: nextPath}}">{{ P.createAccount }}</router-link></p>
<p class="auth-footer" v-else-if="mode === 'register'">{{ P.hasAccount }} <router-link :to="{path: '/login', query: {next: nextPath}}">{{ P.signIn }}</router-link></p>
<p class="auth-footer" v-else><router-link to="/login">{{ P.returnLogin }}</router-link><router-link v-if="mode === 'reset' && !token" to="/forgot">{{ P.forgot }}</router-link></p>`;
