// @ts-check
import { request } from '../../transport.js';
import { P } from '../copy.js';
import { user, reloadSession } from '../session.js';
import { tenantPath } from './data.js';

/** @param {{ask: (value: import('../confirm.js').Confirmation) => void, load: () => Promise<void>}} deps */
export function useMemberActions(deps) {
  /** @param {ReturnType<typeof import('./data.js').parseMember>} member */
  async function refresh(member) { if (member.user_id === user.value?.user_id) await reloadSession(); else await deps.load(); }
  /** @param {ReturnType<typeof import('./data.js').parseMember>} member @param {Event} event */
  function changeRole(member, event) {
    if (!(event.target instanceof HTMLSelectElement)) return;
    const nextRole = event.target.value;
    event.target.value = member.role;
    if (nextRole === member.role) return;
    const labels = /** @type {Record<string, string>} */ (P.roles);
    deps.ask({ title: P.changeRole, subject: `${member.display_name} · ${labels[member.role]} → ${labels[nextRole]}`, body: P.changeRoleBody, action: async () => {
      await request(tenantPath(`/members/${encodeURIComponent(member.user_id)}`), { method: 'PATCH', body: JSON.stringify({ role: nextRole }) });
      await refresh(member);
    } });
  }
  /** @param {ReturnType<typeof import('./data.js').parseMember>} member */
  function remove(member) {
    deps.ask({ title: P.removeMember, subject: `${member.display_name} · ${member.email}`, body: P.removeMemberBody, action: async () => {
      await request(tenantPath(`/members/${encodeURIComponent(member.user_id)}`), { method: 'DELETE' });
      await refresh(member);
    } });
  }
  return { changeRole, remove };
}
