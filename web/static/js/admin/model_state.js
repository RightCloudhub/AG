// @ts-check
import { ref } from '../../vendor/vue.esm-browser.prod.js';
import { emptyRoles } from './model_types.js';

/** @returns {import('./model_types.js').Profile} */
export function newProfile() {
  return { id: '', name: '', revision: 0, active_revision: 0, roles: emptyRoles(), test: null };
}

export function modelState() {
  return {
    catalog: ref(/** @type {import('./model_types.js').CatalogModel[]} */ ([])),
    profiles: ref(/** @type {import('./model_types.js').Profile[]} */ ([])),
    form: ref(newProfile()), selected: ref(''), busy: ref(''), error: ref(''), notice: ref(''),
    dirty: ref(false), targetRevision: ref(1),
  };
}
/** @typedef {ReturnType<typeof modelState>} ModelState */
