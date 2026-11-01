// @ts-check
import { ref, onMounted } from '../../../vendor/vue.esm-browser.prod.js';
import { request } from '../../transport.js';
import { rows } from '../../responses.js';
import { P } from '../copy.js';
import { user } from '../session.js';
import { useForm } from '../forms.js';
import { useConfirmation } from '../confirm.js';
import { parseMember, parseInvite, roles, tenantPath, dateTime, initial } from './data.js';
import { MEMBERS_TEMPLATE } from './members_template.js';
import { useMemberActions } from './member_actions.js';

export default {
  setup() {
    const form = useForm(), confirm = useConfirmation();
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseMember>[]>} */
    const members = ref([]);
    /** @type {import('../../../vendor/vue.esm-browser.prod.js').Ref<ReturnType<typeof parseInvite>[]>} */
    const invitations = ref([]);
    const loading = ref(true), email = ref(''), role = ref('reader');
    async function load() {
      const [memberResult, inviteResult] = await Promise.all([request(tenantPath('/members')), request(tenantPath('/invites'))]);
      members.value = rows(memberResult.data, parseMember);
      invitations.value = rows(inviteResult.data, parseInvite);
    }
    async function invite() {
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) { form.errors.value = { email: P.fields.invalid_email }; form.focusError(); return; }
      await form.run(async () => {
        await request(tenantPath('/invites'), { method: 'POST', body: JSON.stringify({ email: email.value.trim(), role: role.value }) });
        email.value = ''; await load(); form.notice.value = P.inviteSent;
      });
    }
    /** @param {ReturnType<typeof parseInvite>} invitation */
    async function resend(invitation) {
      await form.run(async () => {
        await request(tenantPath(`/invites/${encodeURIComponent(invitation.id)}/resend`), { method: 'POST' });
        await load(); form.notice.value = P.inviteResent;
      });
    }
    /** @param {ReturnType<typeof parseInvite>} invitation */
    function revokeInvite(invitation) {
      confirm.ask({ title: P.revokeInvite, subject: invitation.email, body: P.revokeInviteBody, action: async () => {
        await request(tenantPath(`/invites/${encodeURIComponent(invitation.id)}`), { method: 'DELETE' });
        await load();
      } });
    }
    onMounted(() => form.run(load).finally(() => { loading.value = false; }));
    return { P, user, members, invitations, email, role, roles, loading, ...form, ...confirm, ...useMemberActions({ ask: confirm.ask, load }), load, invite, resend, revokeInvite, dateTime, initial };
  },
  template: MEMBERS_TEMPLATE,
};
