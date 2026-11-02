"""Offline regression checks for degradation fields in persisted notebook answers."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR

JS = ROOT_DIR / "web" / "static" / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is only needed for JS verification")


def _run(source: str) -> None:
    modules = {
        "* as history": "store/conversations.js",
        "{parseAnswer}": "responses.js",
        "{buildPages}": "chat/pages.js",
        "{degradedTag, degradedClass}": "chat/presenters.js",
        "{StorageKey}": "contracts.js",
        "{text as T, fmt}": "i18n.js",
    }
    program = "import assert from 'node:assert/strict';\n" + "\n".join(
        f"import {names} from {(JS / path).as_uri()!r};" for names, path in modules.items()
    )
    result = subprocess.run(
        [str(NODE), "--input-type=module"],
        input=program + "\n" + _FIXTURE + "\n" + source,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


_FIXTURE = r"""
const saved = new Map();
globalThis.localStorage = {
  getItem: key => saved.get(key) ?? null,
  setItem: (key, value) => saved.set(key, value),
};
const answer = parseAnswer({
  query_id: 'saved-query', question: 'Who leads Apex?', answer: 'Elena',
  route: 'agentic', status: 'answered',
  claims: [{ text: 'Elena', evidence_ids: ['e1'] }],
  steps: [{ hop: 1, sub_question: 'Who leads Apex?', conclusion: 'Elena',
    evidence_ids: ['e1'], tool_calls: [{ tool: 'graph_neighbors', hits: ['e1'] }] }],
  cost: { latency_ms: 120, tokens: 25, llm_calls: 1 },
  metadata: {
    evidence: [{ id: 'e1', content: 'Elena leads Apex.', score: 0.9, source: 'graph' }],
    confidence: { level: 'high', score: 0.9, reasons: ['cited'] },
    triage: { route: 'agentic', rationale: 'graph', estimated_hops: 1,
      confidence: 0.8, rule_hit: 'entity' },
  },
});
function conversation(data) {
  return {
    id: 'saved-conversation', title: 'Who leads Apex?', createdAt: 1, updatedAt: 2,
    messages: [
      { role: 'user', text: 'Who leads Apex?', ts: 1 },
      { role: 'assistant', data, ts: 2, feedbackSent: true, feedbackValue: true,
        followups: ['Where is Apex?'], log: [{ at: '120ms', name: 'answer', summary: 'Elena' }] },
    ],
  };
}
"""


@pytest.mark.parametrize("owned", [False, True], ids=["core-history", "tenant-history"])
def test_history_read_migrates_old_answers_and_preserves_current_fields(owned: bool) -> None:
    loader = (
        "history.selectHistoryOwner('user id', 'tenant/id')"
        if owned
        else "history.initializeLegacyHistory()"
    )
    key = "'agr_notebook_v2:user%20id:tenant%2Fid'" if owned else "StorageKey.CONVERSATIONS"
    _run(
        f"const key = {key};\nconst load = () => {loader};\n"
        + r"""
const oldAnswer = { ...answer };
delete oldAnswer.degraded;
delete oldAnswer.low_confidence;
const old = conversation(oldAnswer);
const current = conversation({ ...answer,
  degraded: [{ channel: 'vector', reason: 'vector_store_unavailable', status: 'failed' }],
  low_confidence: true,
});
current.id = 'current-conversation';
saved.set(key, JSON.stringify([old, current]));
load();
const migrated = history.conversations.value[0];
assert.deepEqual(migrated, {
  ...old,
  messages: [old.messages[0], { ...old.messages[1], data: {
    ...oldAnswer, degraded: [], low_confidence: false,
  } }],
});
assert.deepEqual(history.conversations.value[1], current);
const pages = buildPages(migrated.messages, false);
assert.equal(degradedTag(pages[0].answer.msg.data), '');
assert.equal(degradedTag(history.conversations.value[1].messages[1].data), T.answer.lowConfidence);

// The next normal history write persists the migrated shape; loading it again is stable.
history.renameConversation(old.id, old.title);
const persisted = JSON.parse(saved.get(key));
assert.deepEqual(persisted[0].messages, migrated.messages);
load();
assert.deepEqual(history.conversations.value, persisted);
"""
    )


def test_history_with_malformed_degradation_still_loads_and_renders() -> None:
    _run(r"""
for (const degraded of [null, {}, 'vector', 42, [null, 'vector', { channel: 42 }]]) {
  saved.set(StorageKey.CONVERSATIONS, JSON.stringify([conversation({ ...answer, degraded })]));
  history.initializeLegacyHistory();
  assert.equal(history.conversations.value.length, 1);
  const restored = buildPages(history.conversations.value[0].messages, false)[0].answer.msg.data;
  assert.ok(Array.isArray(restored.degraded));
  assert.deepEqual(restored.evidence, answer.evidence);
  assert.equal(degradedTag(restored), '');
}
""")


def test_degradation_presenter_guards_answers_that_bypass_parsing() -> None:
    _run(r"""
assert.equal(degradedTag({}), '');
for (const degraded of [undefined, null, {}, 'vector', 42, [null, 'vector', { channel: 42 }]]) {
  assert.equal(degradedTag({ degraded }), '');
}
const channels = [{ channel: 'vector' }, { channel: 'custom' }];
assert.equal(degradedTag({ degraded: channels }),
  fmt(T.answer.degraded, { channels: `${T.answer.degradedChannels.vector}、custom` }));
assert.equal(degradedTag({ degraded: [null, ...channels] }),
  fmt(T.answer.degraded, { channels: `${T.answer.degradedChannels.vector}、custom` }));
assert.equal(degradedTag({ low_confidence: true }), T.answer.lowConfidence);
assert.equal(degradedClass({ low_confidence: true }), 'tag--err');
assert.equal(degradedClass({}), 'tag--warn');
""")
