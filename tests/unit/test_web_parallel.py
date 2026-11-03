"""Interleaved SSE branches and compact HopRail groups use stable identities."""

from test_web_wizard_ui import _run, needs_node


@needs_node
def test_parallel_progress_and_group_states():
    _run(r"""
let m = progress.emptyProgress(0);
for (const branch_id of ['a', 'b', 'c']) {
  m = progress.applySubQuestion(m, { hop: 1, branch_id, branch_count: 3,
    sub_question: branch_id }, 10);
}
m = progress.applyHopDone(m, { hop: 1, branch_id: 'b', conclusion: 'B' }, 30);
assert.equal(m.hops[0].state, 'active');
assert.equal(m.hops[1].conclusion, 'B');
m = progress.applyThinking(m, { stage: 'retrieve', branch_id: 'a', text: 'tools: graph' });
assert.deepEqual(m.hops[0].tools, ['graph']);
assert.deepEqual(m.hops[2].tools, []);
const small = rail.railFromProgress(m);
assert.equal(small.length, 4);
assert.equal(new Set(small.map(i => i.key)).size, 4);
assert.ok(small.slice(0, 3).every(i => i.parallelLabel));
assert.deepEqual(small.slice(0, 3).map(i => i.parallelNext), [true, true, false]);
m = progress.applySubQuestion(m, { hop: 1, branch_id: 'd', branch_count: 4 }, 35);
assert.equal(rail.railFromProgress(m).length, 2);
const stopped = rail.railFromProgress(progress.stopProgress(m));
assert.equal(stopped[0].state, 'stopped');
const failed = rail.railFromProgress(progress.failProgress(m));
assert.equal(failed[0].state, 'failed');
m = progress.applyHopDone(m, { hop: 1, branch_id: 'a', outcome: 'rejected' }, 50);
assert.equal(m.hops[0].state, 'failed');
const answer = parseAnswer({ query_id: 'q', question: 'q', answer: 'answer',
  status: 'answered', route: 'agentic',
  cost: {}, metadata: { agent_mode: 'multi' }, steps:
  ['a','b','c'].map(sub_question => ({ hop: 1, sub_question, evidence_ids: [] })) });
const final = rail.railFromAnswer(answer);
assert.equal(final.length, 4);
assert.equal(new Set(final.map(i => i.key)).size, 4);
assert.ok(final.slice(0, 3).every(i => i.parallelLabel));
""")
