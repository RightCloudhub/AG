import assert from 'node:assert/strict';
import { test } from 'node:test';

const root = '../../../web/static/js/';
const { cookieTransport, acceptCsrf, forgetCsrf, onCookieIdentity } = await import(`${root}product/cookie_transport.js`);
const { installTransport, request } = await import(`${root}transport.js`);
const { cancelRequests } = await import(`${root}request_scope.js`);
const { streamQuery } = await import(`${root}api.js`);
const me = { user_id: 'owner', tenant_id: 'first' };
const json = (data, status = 200, code = 'UNAUTHORIZED') => new Response(JSON.stringify({
  success: status < 400, data, error: { code },
}), { status, headers: { 'Content-Type': 'application/json' } });

test('a changed identity rejects the original mutation without replaying it', async () => {
  let writes = 0, changes = 0;
  acceptCsrf({ csrf_token: 'csrf' }); installTransport(cookieTransport);
  onCookieIdentity({ identity: () => assert.fail('unexpected refresh'), expired() {}, expected: () => me, changed: () => changes++ });
  globalThis.fetch = async (path, options) => {
    writes++;
    assert.equal(options.headers.get('X-AGR-User'), me.user_id);
    assert.equal(options.headers.get('X-AGR-Tenant'), me.tenant_id);
    assert.equal(options.headers.has('Authorization'), false);
    assert.equal(options.headers.has('X-API-Key'), false);
    return json(null, 409, 'IDENTITY_CHANGED');
  };
  await assert.rejects(request('/v1/me', { method: 'PATCH', body: '{}', headers: { Authorization: 'stale', 'X-API-Key': 'stale' } }), { name: 'AbortError' });
  assert.equal(writes, 1); assert.equal(changes, 1);
});

test('queued Cookie mutations capture the original account before acquiring the lock', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  let release, current = me, captured;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
    locks: { request: (name, run) => new Promise(resolve => { release = () => resolve(run()); }) },
  } });
  try {
    onCookieIdentity({ identity() {}, expired() {}, expected: () => current });
    globalThis.fetch = async (path, options) => { captured = options.headers; return json({}); };
    const pending = request('/v1/auth/logout', { method: 'POST' });
    current = { user_id: 'another', tenant_id: 'second' };
    release(); await pending;
    assert.equal(captured.get('X-AGR-User'), me.user_id);
    assert.equal(captured.get('X-AGR-Tenant'), me.tenant_id);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

test('session revocation cannot overlap another Cookie mutation', async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  let queue = Promise.resolve(), active = 0, peak = 0;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {
    locks: { request: (name, run) => { const next = queue.then(run); queue = next; return next; } },
  } });
  try {
    globalThis.fetch = async () => {
      peak = Math.max(peak, ++active);
      await new Promise(resolve => setTimeout(resolve, 5));
      active--; return json({});
    };
    await Promise.all([request('/v1/auth/sessions/current', { method: 'DELETE' }), request('/v1/auth/logout', { method: 'POST' })]);
    assert.equal(peak, 1);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'navigator', descriptor);
    else delete globalThis.navigator;
  }
});

test('CSRF rejection gets one fresh token and exactly one retry', async () => {
  acceptCsrf({ csrf_token: 'stale' });
  onCookieIdentity({ identity() {}, expired() {} });
  let tokens = 0, writes = 0;
  globalThis.fetch = async (path, options) => {
    if (path.endsWith('/csrf')) { tokens++; return json({ csrf_token: 'current' }); }
    assert.equal(options.headers.get('X-CSRF-Token'), writes++ ? 'current' : 'stale');
    return json(null, 403, 'CSRF_FAILED');
  };
  await assert.rejects(request('/v1/docs', { method: 'POST', body: '{}' }), { status: 403, code: 'CSRF_FAILED' });
  assert.equal(tokens, 1); assert.equal(writes, 2);
});

test('a second 401 expires the session without looping refresh', async () => {
  forgetCsrf(); let writes = 0, refreshes = 0, expirations = 0;
  onCookieIdentity({ identity() {}, expired: () => expirations++ });
  globalThis.fetch = async path => {
    if (path.endsWith('/csrf')) return json({ csrf_token: 'csrf' });
    if (path === '/v1/me') return json(null, 401);
    if (path.endsWith('/refresh')) { refreshes++; return json({ ...me, csrf_token: 'new-csrf' }); }
    writes++; return json(null, 401);
  };
  await assert.rejects(request('/v1/query', { method: 'POST', body: '{}' }), { status: 401 });
  assert.equal(refreshes, 1); assert.equal(writes, 2); assert.equal(expirations, 1);
});

test('scoped logout refreshes expired access and retains its original identity precondition', async () => {
  let logouts = 0, refreshed = false;
  onCookieIdentity({ identity: value => assert.equal(value.user_id, me.user_id), expired() {}, expected: () => me });
  globalThis.fetch = async (path, options) => {
    if (path.endsWith('/csrf')) return json({ csrf_token: 'csrf' });
    if (path === '/v1/me') return json(null, 401);
    if (path.endsWith('/refresh')) { refreshed = true; return json({ ...me, csrf_token: 'new-csrf' }); }
    logouts++;
    assert.equal(options.headers.get('X-AGR-User'), me.user_id);
    assert.equal(options.headers.get('X-AGR-Tenant'), me.tenant_id);
    return refreshed ? json({ logged_out: true }) : json(null, 401);
  };
  assert.equal((await request('/v1/auth/logout', { method: 'POST' })).data.logged_out, true);
  assert.equal(logouts, 2);
});

test('cancellation discards late SSE events and closes a body that ignored fetch abort', { timeout: 2000 }, async () => {
  let source, cancelled, events = 0;
  const closed = new Promise(resolve => { cancelled = resolve; });
  const body = new ReadableStream({ start(controller) { source = controller; }, cancel() { cancelled(); } });
  installTransport(async () => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
  streamQuery({ question: 'first tenant' }, { answer: () => events++, error: () => events++, done: () => events++ });
  await new Promise(resolve => setImmediate(resolve));
  cancelRequests();
  source.enqueue(new TextEncoder().encode('event: answer\ndata: {"answer":"private"}\n\n'));
  await closed;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(events, 0); assert.equal(body.locked, false);
});
