import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRenderer, reactive, nextTick } from '../../web/static/vendor/vue.esm-browser.prod.js';

globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
const root = '../../web/static/js/';
const { installTransport } = await import(`${root}transport.js`);
const scope = await import(`${root}store/knowledge.js`);
const { newKnowledgeBase } = await import(`${root}knowledge/config_types.js`);
const { summaryState } = await import(`${root}knowledge/summary_state.js`);
const summaryApi = await import(`${root}knowledge/summary_api.js`);
const { default: EvidenceSource } = await import(`${root}knowledge/evidence_source.js`);
const { parseAnswer } = await import(`${root}responses.js`);
const { emptyProgress, applyGlobalProgress, finishProgress } = await import(`${root}chat/progress.js`);
const { streamQuery } = await import(`${root}api.js`);
const { text: T, fmt } = await import(`${root}i18n.js`);
const json = data => Response.json({ success: true, data });
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); };
const index = (id = 'one', jobs = {}) => ({ kb_id: id, generation: '1', enabled: true,
  status: 'ready', document_count: 3, ready_documents: 3, jobs });
const original = (content, offset = 0) => ({ content, offset, status: 'available', total_chars: 5000,
  source_refs: [{ doc_id: 'contract', doc_version: 2, chunk_id: 'chunk' }] });
function reset() {
  scope.resetKnowledgeScope();
  for (const id of ['one', 'two']) scope.acceptKnowledgeBase({ ...newKnowledgeBase(), kb_id: id, name: id });
  scope.selectKnowledgeBase('one'); scope.knowledgeRole.value = 'admin';
}
function mount(setup) {
  const noop = () => {};
  const renderer = createRenderer({ insert: noop, remove: noop, createElement: () => ({}),
    createText: () => ({}), createComment: () => ({}), setText: noop, setElementText: noop,
    parentNode: () => null, nextSibling: () => null, patchProp: noop });
  let state;
  const app = renderer.createApp({ setup() { state = setup(); return () => null; } });
  app.mount({}); return { state, close: () => app.unmount() };
}

test('summary jobs use scoped filtering and explicit build/retry actions', async () => {
  reset(); const calls = [];
  installTransport(async (path, options) => {
    calls.push([path, options]);
    return json(path.includes('?') ? [{ job_id: 'failed', kind: 'document', attempts: 2 }] : index());
  });
  assert.equal((await summaryApi.loadSummary('one')).total, 3);
  assert.equal((await summaryApi.loadFailedJobs('one'))[0].id, 'failed');
  assert.equal(calls[1][0], '/v1/knowledge-bases/one/summary-jobs?status=failed&limit=100');
  await summaryApi.buildSummary('one'); await summaryApi.retrySummary('one', 'job/id');
  assert.equal(calls[2][1].method, 'POST'); assert.equal(calls[2][1].body, '{}');
  assert.equal(calls[3][0], '/v1/knowledge-bases/one/summary-jobs/job%2Fid/retry');
});

test('successful summary refresh clears a prior error', async () => {
  reset(); let broken = true;
  installTransport(async () => { if (broken) throw new Error('temporarily offline'); return json(index()); });
  const view = mount(summaryState);
  try {
    await flush(); assert.equal(view.state.error.value, 'temporarily offline');
    broken = false; await view.state.reload();
    assert.equal(view.state.status.value.ready, 3); assert.equal(view.state.error.value, '');
  } finally { view.close(); }
});

test('unmount during a summary action prevents late refresh and polling', async () => {
  reset(); const calls = []; let release;
  installTransport(async (path, options) => {
    calls.push(path);
    if (options.method === 'POST') return new Promise(resolve => { release = resolve; });
    return json(index());
  });
  const view = mount(summaryState); await flush();
  const action = view.state.run(); view.close();
  release(json(index())); await action;
  assert.equal(calls.length, 2);
});

test('switching knowledge bases invalidates an in-flight summary action', async () => {
  reset(); const releases = [];
  installTransport(async (path, options) => options.method === 'POST'
    ? new Promise(resolve => releases.push(resolve)) : json(index(path.includes('/two/') ? 'two' : 'one')));
  const view = mount(summaryState);
  try {
    await flush(); const old = view.state.run();
    scope.selectKnowledgeBase('two'); await flush();
    assert.equal(view.state.status.value.kb_id, 'two'); assert.equal(view.state.busy.value, false);
    const current = view.state.run(); assert.equal(view.state.busy.value, true);
    releases[0](json(index('one'))); await old; assert.equal(view.state.busy.value, true);
    releases[1](json(index('two'))); await current; assert.equal(view.state.busy.value, false);
  } finally { view.close(); }
});

test('original evidence pages count Unicode codepoints and clear deleted contents', async () => {
  reset(); const paths = [];
  installTransport(async path => {
    paths.push(path);
    if (paths.length === 3) return json({ ...original('', 4096), status: 'deleted' });
    return json(original(paths.length === 1 ? '𠮷'.repeat(4096) : '剩余原文', paths.length === 1 ? 0 : 4096));
  });
  const view = mount(() => EvidenceSource.setup(reactive({ evidenceId: 'evidence/id', kbId: 'one' })));
  try {
    await flush(); assert.equal(view.state.nextOffset(), 4096);
    assert.equal(view.state.original.value.sources[0].version, 2);
    await view.state.load(view.state.nextOffset());
    assert.equal(new URL(paths[1], 'http://local').searchParams.get('offset'), '4096');
    assert.ok(paths[1].startsWith('/v1/evidence/evidence%2Fid?kb_id=one&'));
    await view.state.load(4096);
    assert.equal(view.state.original.value.status, 'deleted'); assert.equal(view.state.original.value.content, '');
  } finally { view.close(); }
});

test('late original evidence cannot overwrite a new scope or an unmounted view', async () => {
  reset(); const releases = [];
  installTransport(() => new Promise(resolve => releases.push(resolve)));
  const props = reactive({ evidenceId: 'same-id', kbId: 'one' });
  const view = mount(() => EvidenceSource.setup(props));
  props.kbId = 'two'; await nextTick();
  releases[1](json(original('new scope'))); await flush();
  releases[0](json(original('old scope'))); await flush();
  assert.equal(view.state.original.value.content, 'new scope');
  const pending = view.state.load(); view.close(); releases[2](json(original('after unmount')));
  await pending; assert.equal(view.state.original.value.content, 'new scope');
});

test('global SSE progress and terminal coverage survive parsing and unknown events', async () => {
  reset(); let progress = emptyProgress(0); let answer;
  const coverage = { processed_documents: 2, total_documents: 3, failed_map_units: 1, partial: true };
  const chain = { query_id: 'global', answer: 'Partial findings', route: 'agentic', cost: {},
    metadata: { kb_id: 'one', coverage, evidence: [{ id: 'E1', evidence_id: 'source', source_status: 'available' }] } };
  installTransport(async () => new Response(`event: future_event\ndata: {}\n\nevent: global_progress\ndata: ${JSON.stringify(coverage)}\n\nevent: answer\ndata: ${JSON.stringify(chain)}\n\n`, {
    headers: { 'Content-Type': 'text/event-stream' },
  }));
  await new Promise((resolve, reject) => streamQuery({ question: 'Overview' }, {
    global_progress: data => { progress = applyGlobalProgress(progress, data); },
    answer: data => { answer = parseAnswer(data); progress = finishProgress(progress, answer); },
    error: reject, done: resolve,
  }));
  assert.deepEqual(answer.coverage, { processed: 2, total: 3, partial: true, failed: 1 });
  assert.equal(answer.kb_id, 'one'); assert.equal(answer.evidence[0].evidence_id, 'source');
  assert.equal(progress.coverage.processed, 2);
  assert.equal(fmt(T.answer.globalCoverage, answer.coverage), '已覆盖 2/3 份文档');
});

test('a withdrawn final answer clears previously streamed conclusions', () => {
  const answer = parseAnswer({ query_id: 'withdrawn', answer: 'Source deleted', route: 'agentic', cost: {},
    metadata: { source_retracted: true, evidence: [] } });
  const progress = { ...emptyProgress(0), note: 'Withdrawn private content', planText: 'Private plan',
    hops: [{ hop: 1, conclusion: 'Private conclusion' }] };
  const done = finishProgress(progress, answer);
  assert.equal(done.hops.length, 0); assert.equal(done.note, ''); assert.equal(done.planText, '');
});
