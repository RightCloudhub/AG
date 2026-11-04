import assert from 'node:assert/strict';
import { test } from 'node:test';

const saved = new Map();
globalThis.localStorage = {
  get length() { return saved.size; }, key: i => [...saved.keys()][i] ?? null,
  getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value),
  removeItem: key => saved.delete(key),
};
const root = '../../web/static/js/';
const api = await import(`${root}api.js`);
const scope = await import(`${root}store/knowledge.js`);
const history = await import(`${root}store/conversations.js`);
const inspector = await import(`${root}shell/use_inspector.js`);
const graph = await import(`${root}store/graph_focus.js`);
const { installTransport, request } = await import(`${root}transport.js`);
const { newKnowledgeBase } = await import(`${root}knowledge/config_types.js`);
const { parseKnowledgeBase } = await import(`${root}knowledge/config_types.js`);
const { saveKnowledgeBase, listKnowledgeBases } = await import(`${root}knowledge/config_api.js`);
const json = (data, total = 0) => Response.json({ success: true, data, meta: { total } });
const kb = id => ({ ...newKnowledgeBase(), kb_id: id, name: id, config_revision: 1, active_generation: '1' });
function reset() {
  history.selectHistoryOwner(null); scope.resetKnowledgeScope();
  for (const id of ['default', 'contracts', 'supply']) scope.acceptKnowledgeBase(kb(id));
}

test('query, stream, graph, import, review and audit share the chosen knowledge base', async () => {
  reset(); scope.selectKnowledgeBase('contracts');
  const calls = [];
  installTransport(async (path, options) => {
    calls.push([path, options]);
    if (path === '/v1/query/stream') return new Response('', { headers: { 'Content-Type': 'text/event-stream' } });
    if (path === '/v1/query' || path.startsWith('/v1/budget')) return json({});
    return json([]);
  });
  await api.postQuery({ question: 'Risks?' });
  await new Promise(resolve => api.streamQuery({ question: 'Risks?' }, { done: resolve }));
  await api.fetchEntities({ query: 'A & B' }); await api.fetchIngestTasks();
  await api.fetchIngestTask('same/id'); await api.uploadDocs([new File(['content'], 'same.txt')]);
  await api.fetchReviewQueue(); await api.decideReview('same/id', { decision: 'approve' });
  await api.fetchAuditQuery('query/id'); await api.fetchTrace('query/id');
  await api.postFeedback({ query_id: 'query', accurate: true });
  for (const [path, options] of calls) {
    if (path === '/v1/query' || path === '/v1/query/stream') {
      const body = JSON.parse(options.body); assert.equal(body.kb_id, 'contracts');
      assert.equal(body.tenant_id, undefined);
    } else {
      const url = new URL(path, 'http://local');
      assert.equal(url.searchParams.get('kb_id'), 'contracts');
      assert.equal(url.searchParams.has('tenant_id'), false);
    }
  }
  await api.fetchBudgetSnapshot(); assert.equal(calls.at(-1)[0], '/v1/budget/snapshot');
  scope.selectKnowledgeBase('default');
  await api.postQuery({ question: 'legacy' });
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), { question: 'legacy' });
  await api.uploadDocs([]); assert.equal(calls.at(-1)[0], '/v1/docs');
});

test('switch invalidates ignored aborts, clears the inspector and partitions same-ID history', async () => {
  reset(); history.selectHistoryOwner('one', 'tenant');
  scope.selectKnowledgeBase('contracts');
  const first = history.createConversation('Contracts only');
  inspector.inspQuestion.value = 'Private question'; graph.setGraphFocus('Private entity');
  let release;
  installTransport(() => new Promise(resolve => { release = resolve; }));
  const flight = request('/v1/audit/queries/old');
  scope.selectKnowledgeBase('supply');
  const second = history.createConversation('Supply only');
  release(json({ secret: 'old evidence' }));
  await assert.rejects(flight, { name: 'AbortError' });
  assert.equal(inspector.inspQuestion.value, '');
  assert.equal(history.findConversation(first.id), null);
  assert.equal(history.findConversation(second.id).title, 'Supply only');
  scope.selectKnowledgeBase('contracts');
  assert.equal(history.findConversation(first.id).title, 'Contracts only');
  assert.equal(history.findConversation(second.id), null);
  assert.equal(history.activeConversationId.value, null);
  scope.selectKnowledgeBase('foreign'); assert.equal(scope.activeKnowledgeId.value, 'contracts');
});

test('late stream frames cannot enter the new scope', async () => {
  reset(); scope.selectKnowledgeBase('contracts');
  let controller;
  installTransport(async () => new Response(new ReadableStream({ start(value) { controller = value; } }), {
    headers: { 'Content-Type': 'text/event-stream' },
  }));
  const seen = [];
  api.streamQuery({ question: 'old' }, { answer: data => seen.push(data), error: data => seen.push(data), done: () => seen.push('done') });
  await new Promise(resolve => setImmediate(resolve));
  scope.selectKnowledgeBase('supply');
  controller.enqueue(new TextEncoder().encode('event: answer\ndata: {"answer":"old secret"}\n\n'));
  controller.close();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(seen, []);
});

test('configuration writes omit tenancy and cannot patch mode or generation', async () => {
  reset(); const calls = [];
  installTransport(async (path, options) => {
    const body = JSON.parse(options.body); calls.push([path, options.method, body]);
    return json({ ...kb('contracts'), ...body, config_revision: 2 });
  });
  const form = { ...kb('contracts'), build_mode: 'open', tenant_id: 'untrusted', schema_id: 'stale' };
  await saveKnowledgeBase(form, true);
  assert.equal(calls[0][1], 'POST');
  assert.equal(calls[0][2].build_mode, 'open');
  assert.equal(calls[0][2].schema_id, null);
  await saveKnowledgeBase(form, false);
  assert.equal(calls[1][0], '/v1/knowledge-bases/contracts');
  assert.equal(calls[1][1], 'PATCH'); assert.equal(calls[1][2].expected_revision, 1);
  for (const body of calls.map(row => row[2])) {
    assert.equal(body.tenant_id, undefined); assert.equal(body.active_generation, undefined);
  }
  assert.equal(calls[1][2].build_mode, undefined);
});

test('knowledge selection catalog loads subsequent pages', async () => {
  reset(); const paths = [];
  installTransport(async path => { paths.push(path); return json(paths.length === 1 ? [kb('default'), kb('contracts')] : [kb('supply')], 3); });
  assert.deepEqual((await listKnowledgeBases()).map(row => row.kb_id), ['default', 'contracts', 'supply']);
  assert.equal(paths[1], '/v1/knowledge-bases?page=2&limit=100');
});

test('renaming a knowledge base preserves its pinned profile revision', async () => {
  reset(); const calls = [];
  installTransport(async (path, options) => {
    const body = JSON.parse(options.body); calls.push(body);
    return json({ ...kb('contracts'), ...body, config_revision: 2 });
  });
  const form = parseKnowledgeBase({ ...kb('contracts'), model_profile_id: 'models', model_profile_revision: 1 });
  form.name = 'Renamed';
  await saveKnowledgeBase(form, false);
  assert.equal(calls[0].model_profile_revision, 1);
});

test('mode and model changes reset only their dependent selections', async () => {
  const { knowledgeConfigState } = await import(`${root}knowledge/config_state.js`);
  const state = knowledgeConfigState();
  state.form.value.review_policy = 'required';
  state.form.value.build_mode = 'open';
  state.changeMode();
  assert.equal(state.form.value.review_policy, 'confidence');
  state.form.value.model_profile_revision = 1;
  state.form.value.model_profile_id = 'new-models';
  state.changeProfile();
  assert.equal(state.form.value.model_profile_revision, null);
});

test('identity change immediately resets knowledge selection and permissions', async () => {
  reset();
  const session = await import(`${root}product/session.js`);
  const { parseUser } = await import(`${root}product/contracts.js`);
  const me = parseUser({ user_id: 'one', tenant_id: 't1', role: 'admin', capabilities: {}, tenants: [] });
  session.applyUser(me); scope.selectKnowledgeBase('contracts');
  const note = history.createConversation('One / t1 / contracts');
  session.applyUser(me); assert.equal(scope.activeKnowledgeId.value, 'contracts');
  session.applyUser({ ...me, tenant_id: 't2', role: 'reader' });
  assert.equal(scope.activeKnowledgeId.value, 'default');
  assert.equal(scope.knowledgeRole.value, 'reader');
  assert.equal(scope.knowledgeBases.value.length, 0);
  assert.equal(history.findConversation(note.id), null);
  session.clearSession();
});
