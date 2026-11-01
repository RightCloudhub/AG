import assert from 'node:assert/strict';
import { test } from 'node:test';
const stored = new Map();
globalThis.localStorage = {
  get length() { return stored.size; }, key: i => [...stored.keys()][i] ?? null,
  getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key),
};
const root = '../../../web/static/js/';
const { safeNext, parseUser } = await import(`${root}product/contracts.js`);
const { passwordStrength, validateAuth } = await import(`${root}product/forms.js`);
const scope = await import(`${root}request_scope.js`);
const history = await import(`${root}store/conversations.js`);
const { cookieTransport, forgetCsrf, onCookieIdentity } = await import(`${root}product/cookie_transport.js`);
const { installTransport, request } = await import(`${root}transport.js`);
const { streamQuery } = await import(`${root}api.js`);
const json = (data, status = 200) => new Response(JSON.stringify({ success: status < 400, data, error: { code: 'UNAUTHORIZED' } }), { status, headers: { 'Content-Type': 'application/json' } });
const me = { user_id: 'u1', tenant_id: 't1', role: 'admin', authenticated: true, capabilities: { chat: true, settings_members: true }, tenants: [{ id: 't1', name: 'Team', role: 'admin', kind: 'team' }], email: 'one@example.com', display_name: 'One' };

test('safe local return URL and typed identity contract', () => {
  for (const value of ['//evil.test', 'https://evil.test', '/\\evil.test', '/%2f%2fevil.test', '/app/../../login', '/app/%0aevil', '/login', null]) assert.equal(safeNext(value), '/app/chat');
  assert.equal(safeNext('/app/settings/security?tab=sessions'), '/app/settings/security?tab=sessions');
  assert.equal(safeNext('/invite/token'), '/invite/token');
  assert.equal(parseUser(me).capabilities.settings_members, true);
  assert.equal(parseUser(me).capabilities.settings_api_keys, false);
});
test('password policy and form errors agree with the server', () => {
  assert.equal(passwordStrength('simple'), 1);
  assert.equal(passwordStrength('Correct-horse-42!'), 2);
  assert.equal(passwordStrength('a sufficiently long phrase'), 3);
  assert.equal(passwordStrength('x'.repeat(129)), 1);
  const errors = validateAuth('register', { email: 'bad', password: 'short', confirmation: 'different', display_name: ' ' });
  assert.deepEqual(Object.keys(errors).sort(), ['confirmation', 'display_name', 'email', 'password']);
});
test('history starts empty and remains partitioned by account and tenant', () => {
  stored.set('agr_conversations', JSON.stringify([{ id: 'legacy', title: 'Unowned', messages: [] }]));
  history.selectHistoryOwner('one', 'a:b');
  assert.equal(history.conversations.value.length, 0);
  const first = history.createConversation('Only one/a:b').id;
  history.selectHistoryOwner('one:a', 'b');
  assert.equal(history.conversations.value.length, 0);
  history.createConversation('Other owner');
  history.selectHistoryOwner('one', 'a:b');
  assert.equal(history.conversations.value[0].id, first);
  history.selectHistoryOwner('one', 'second');
  history.createConversation('Second tenant');
  history.clearAccountHistory('one');
  history.selectHistoryOwner('one', 'a:b');
  assert.equal(history.conversations.value.length, 0);
  history.selectHistoryOwner('one:a', 'b');
  assert.equal(history.conversations.value.length, 1);
  assert.ok(stored.has('agr_conversations'));
});
test('scope invalidation rejects a late decoded response even when fetch ignores abort', async () => {
  let release;
  const pending = scope.scopedRequest({}, () => new Promise(resolve => { release = resolve; }));
  scope.cancelRequests(); release('old tenant');
  await assert.rejects(pending, { name: 'AbortError' });
});
test('401s share one refresh and carry Cookie/CSRF without the legacy key', async () => {
  forgetCsrf(); installTransport(cookieTransport);
  let refreshed = false, refreshes = 0, attempts = 0;
  onCookieIdentity({ identity: value => assert.equal(value.user_id, 'u1'), expired: () => assert.fail('unexpected expiry') });
  stored.set('agr_api_key', 'legacy-key-must-not-be-sent');
  globalThis.fetch = async (path, options) => {
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers.has('Authorization'), false);
    if (path.endsWith('/csrf')) return json({ csrf_token: 'test-csrf' });
    if (path === '/v1/me') return refreshed ? json(me) : json(null, 401);
    if (path.endsWith('/refresh')) {
      refreshes++; assert.equal(options.headers.get('X-CSRF-Token'), 'test-csrf');
      await new Promise(resolve => setTimeout(resolve, 5)); refreshed = true;
      return json({ ...me, csrf_token: 'rotated-csrf' });
    }
    attempts++;
    if (!refreshed) return json(null, 401);
    assert.equal(options.headers.get('X-CSRF-Token'), 'rotated-csrf');
    return json({ accepted: true });
  };
  await Promise.all([request('/v1/query', { method: 'POST', body: '{}' }), request('/v1/query', { method: 'POST', body: '{}' })]);
  assert.equal(refreshes, 1); assert.equal(attempts, 4);
});
test('stream errors after the first event never replay the query', async () => {
  let requests = 0, errors = 0, answers = 0;
  globalThis.fetch = async () => {
    requests++;
    let phase = 0;
    const body = new ReadableStream({ pull(controller) {
      if (!phase++) controller.enqueue(new TextEncoder().encode('event: thinking\ndata: {"message":"started"}\n\n'));
      else controller.error(new Error('stream interrupted'));
    } });
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
  };
  await new Promise(resolve => { streamQuery({ question: 'query' }, { thinking: () => answers++, error: () => { errors++; resolve(); } }); });
  assert.equal(requests, 1); assert.equal(answers, 1); assert.equal(errors, 1);
});
