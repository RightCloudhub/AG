// @ts-check
export const MEMBERS_TEMPLATE = `<div>
  <header class="settings-title"><h2>{{ P.settingsNav.members }}</h2><p>{{ P.membersIntro }}</p></header>
  <p v-if="message" class="form-message form-message--error" role="alert">{{ message }} <button type="button" class="btn btn--ghost" @click="run(load)">{{ P.retry }}</button></p>
  <p v-if="notice" class="form-message" role="status">{{ notice }}</p>
  <section class="settings-block"><div><h3>{{ P.inviteMember }}</h3><p>{{ P.roleHelp[role] }}</p></div>
    <form class="product-form" novalidate @submit.prevent="invite" :aria-busy="busy"><div class="form-field"><label for="invite-email">{{ P.email }}</label>
      <input id="invite-email" v-model="email" type="email" autocomplete="off" maxlength="254" required :placeholder="P.emailHint" :disabled="busy" :aria-invalid="!!errors.email" aria-describedby="invite-email-error">
      <span id="invite-email-error" class="field-error">{{ errors.email }}</span></div>
      <div class="form-field"><label for="invite-role">{{ P.role }}</label><select id="invite-role" v-model="role" :disabled="busy"><option v-for="item in roles" :key="item.value" :value="item.value">{{ item.label }}</option></select></div>
      <button type="submit" class="btn btn--primary settings-save" :disabled="busy">{{ busy ? P.saving : P.sendInvite }}</button></form>
  </section>
  <section class="settings-list"><div class="settings-list__heading"><h3>{{ P.member }}</h3><span class="dim">{{ members.length }}</span></div>
    <p v-if="loading" class="settings-empty" role="status">{{ P.loading }}</p>
    <div v-else class="table-scroll"><table class="product-table"><thead><tr><th>{{ P.member }}</th><th>{{ P.role }}</th><th>{{ P.joined }}</th><th>{{ P.actions }}</th></tr></thead>
      <tbody><tr v-for="member in members" :key="member.user_id"><td><div class="member-identity"><span class="member-avatar">{{ initial(member.display_name) }}</span><div><strong>{{ member.display_name }}</strong><span v-if="member.user_id === user?.user_id" class="status-label">{{ P.current }}</span><small>{{ member.email }}</small></div></div></td>
        <td :data-label="P.role"><select class="select" :value="member.role" :aria-label="member.display_name + ' · ' + P.role" :disabled="busy" @change="changeRole(member, $event)"><option v-for="item in roles" :key="item.value" :value="item.value">{{ item.label }}</option></select></td>
        <td :data-label="P.joined">{{ dateTime(member.joined_at) }}</td><td><button type="button" class="btn btn--ghost" @click="remove(member)" :disabled="busy">{{ P.remove }}</button></td></tr></tbody></table></div>
  </section>
  <section class="settings-list"><div class="settings-list__heading"><h3>{{ P.invitations }}</h3><span class="dim">{{ invitations.length }}</span></div>
    <div v-if="invitations.length" class="table-scroll"><table class="product-table"><thead><tr><th>{{ P.email }}</th><th>{{ P.role }}</th><th>{{ P.keyStatus }}</th><th>{{ P.expiresAt }}</th><th>{{ P.actions }}</th></tr></thead>
      <tbody><tr v-for="invitation in invitations" :key="invitation.id"><td>{{ invitation.email }}</td><td :data-label="P.role">{{ P.roles[invitation.role] }}</td><td :data-label="P.keyStatus"><span class="status-label" :data-state="invitation.status">{{ P[invitation.status] }}</span></td><td :data-label="P.expiresAt">{{ dateTime(invitation.expires_at) }}</td>
        <td><div v-if="['pending', 'expired'].includes(invitation.status)" class="row-actions"><button type="button" class="btn btn--ghost" @click="resend(invitation)" :disabled="busy">{{ P.resend }}</button><button type="button" class="btn btn--ghost" @click="revokeInvite(invitation)" :disabled="busy">{{ P.revoke }}</button></div><span v-else>{{ P.unknown }}</span></td></tr></tbody></table></div>
    <div v-else-if="!loading" class="settings-empty"><h4>{{ P.noInvites }}</h4><p>{{ P.noInvitesHint }}</p></div>
  </section><confirm-dialog v-if="confirmation" :request="confirmation" :close="dismiss"></confirm-dialog>
</div>`;
