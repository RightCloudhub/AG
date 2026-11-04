// @ts-check
import { request } from '../transport.js';
import { rows } from '../responses.js';
import { parseModel, parseProfile, parseTest } from './model_types.js';

const endpoint = '/v1/model-profiles';
/** @param {string} id */
const profileUrl = id => `${endpoint}/${encodeURIComponent(id)}`;

export async function loadModels() {
  const result = await request(`${endpoint}/catalog`);
  return rows(result.data, parseModel);
}
export async function loadProfiles() {
  const result = await request(endpoint);
  return rows(result.data, parseProfile);
}
/** @param {import('./model_types.js').Profile} profile */
export async function saveProfile(profile) {
  const result = await request(endpoint, { method: 'POST', body: JSON.stringify({
    id: profile.id, name: profile.name, roles: profile.roles, expected_revision: profile.revision,
  }) });
  return parseProfile(result.data);
}
/** @param {import('./model_types.js').Profile} profile */
export async function testProfile(profile) {
  const result = await request(`${profileUrl(profile.id)}/test`, {
    method: 'POST', body: JSON.stringify({revision: profile.revision}),
  });
  return parseTest(result.data);
}
/** @param {import('./model_types.js').Profile} profile @param {number} revision @param {'publish'|'rollback'} action */
export async function publishProfile(profile, revision, action) {
  return request(`${profileUrl(profile.id)}/${action}`, { method: 'POST', body: JSON.stringify({
    revision, expected_active_revision: profile.active_revision,
  }) });
}
