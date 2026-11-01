// @ts-check
import { P } from './copy.js';
export default {
  props: { status: { type: Number, default: 404 } },
  /** @param {{status: number}} props */
  setup(props) { return { P, status: props.status }; },
  template: `<main class="product-error"><span class="product-error__code">{{ status }}</span>
    <h1 tabindex="-1">{{ status === 403 ? P.error.title403 : P.error.title404 }}</h1>
    <p>{{ status === 403 ? P.error.body403 : P.error.body404 }}</p>
    <router-link class="btn btn--secondary" to="/app/chat">{{ P.back }}</router-link></main>`,
};
