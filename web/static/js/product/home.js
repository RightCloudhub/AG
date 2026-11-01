// @ts-check
import { toggleTheme } from '../theme.js';
import { P } from './copy.js';
import { user } from './session.js';
import { publicFrame } from './public_frame.js';

export default {
  setup() { return { P, user, toggleTheme }; },
  template: publicFrame(`<div class="home-intro"><h2>{{ P.homeTitle }}</h2><p>{{ P.homeIntro }}</p>
    <router-link v-if="user" class="btn btn--primary auth-submit" to="/app/chat">{{ P.openApp }}<ag-icon name="arrowRight"></ag-icon></router-link>
    <template v-else><router-link class="btn btn--primary auth-submit" to="/register">{{ P.createAccount }}<ag-icon name="arrowRight"></ag-icon></router-link>
    <p class="auth-footer">{{ P.hasAccount }} <router-link to="/login">{{ P.signIn }}</router-link></p></template></div>`),
};
