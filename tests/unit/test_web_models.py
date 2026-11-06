"""The model editor keeps draft, test and publish transitions separate."""

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is needed for owned JavaScript checks")
def test_profile_editor_uses_server_receipts_and_separate_publication():
    root = (ROOT_DIR / "web/static/js").as_uri()
    program = r"""
import assert from 'node:assert/strict';
const root = ROOT_URI;
const types = await import(`${root}/admin/model_types.js`);
const {modelState} = await import(`${root}/admin/model_state.js`);
const {modelActions} = await import(`${root}/admin/model_actions.js`);
const {installTransport} = await import(`${root}/transport.js`);
const calls = [];
let stored = [];
const checks = types.modelRoles.map(role => ({ role, status: 'passed', capability: role,
  model_id: role, error_code: '', latency_ms: 2, tokens: 3 }));
installTransport(async (path, options) => {
  calls.push(path);
  let data = null;
  if (path.endsWith('/catalog')) data = [];
  else if (path.endsWith('/test')) data = {checks};
  else if (options.method === 'POST') {
    const body = JSON.parse(options.body);
    assert.equal(body.tenant_id, undefined);
    assert.equal(body.base_url, undefined);
    data = {...body, revision: 1, active_revision: 0}; stored = [data];
  } else data = stored;
  return new Response(JSON.stringify({success: true, data, meta: {}}), {
    headers: {'Content-Type': 'application/json'},
  });
});
const state = modelState(); const actions = modelActions(state);
state.form.value.id = 'contracts'; state.form.value.name = 'Contracts';
for (const role of types.modelRoles) state.form.value.roles[role] = role;
await actions.save();
assert.equal(state.form.value.revision, 1);
assert.equal(state.form.value.active_revision, 0);
assert.equal(state.form.value.test, null);
await actions.test();
assert.equal(state.form.value.test.passed, true);
assert.equal(state.form.value.active_revision, 0);
assert.ok(calls.every(path => !path.endsWith('/publish')));
actions.markDirty(); assert.equal(state.dirty.value, true);
actions.choose(''); assert.equal(state.form.value.test, null);
assert.equal(types.parseTest({checks: checks.slice(0, 1)}).passed, false);
assert.throws(() => types.parseTest({checks: [{role:'writer', status:'invented'}]}));
assert.deepEqual(types.forRole([
  {id:'a', capabilities:{completion:true}}, {id:'b', capabilities:{embedding:true}},
], 'embedding').map(row => row.id), ['b']);
""".replace("ROOT_URI", repr(root))
    result = subprocess.run(
        [str(NODE), "--input-type=module"],
        input=program,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
