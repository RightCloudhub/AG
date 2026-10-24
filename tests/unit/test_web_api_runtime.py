"""Execute the browser API client with offline HTTP and storage fixtures."""

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is only needed for JS verification")
API_MODULE = (ROOT_DIR / "web/static/js/api.js").as_uri()
RESPONSES_MODULE = (ROOT_DIR / "web/static/js/responses.js").as_uri()


def _run(source: str) -> None:
    program = (
        "import assert from 'node:assert/strict';\n"
        f"import * as api from {API_MODULE!r};\n"
        f"import {{parseAnswer}} from {RESPONSES_MODULE!r};\n"
        "globalThis.localStorage = {getItem: () => 'test-key'};\n" + source
    )
    result = subprocess.run(
        [NODE, "--input-type=module"],
        input=program,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_upload_sends_auth_without_overriding_multipart_boundary() -> None:
    _run(r"""
let options;
globalThis.fetch = async (_url, init) => {
  options = init;
  return Response.json({success: true, data: {task_id: 'task'}});
};
await api.uploadDocs([new File(['document'], 'doc.txt')]);
const headers = new Headers(options.headers);
assert.equal(headers.get('Authorization'), 'Bearer test-key');
assert.equal(headers.get('Content-Type'), null);
assert.equal(options.body.getAll('files').length, 1);
""")


def test_stream_http_error_is_reported_once_with_backend_code() -> None:
    _run(r"""
globalThis.fetch = async () => Response.json(
  {success: false, error: {code: 'UNAUTHORIZED', message: 'Denied'}}, {status: 401}
);
const errors = [];
await new Promise(resolve => api.streamQuery({question: 'q'}, {
  error: payload => {errors.push(payload); resolve();}, done: resolve,
}));
assert.equal(errors.length, 1);
assert.equal(errors[0].code, 'UNAUTHORIZED');
""")


def test_multiline_stream_frame_is_combined_before_dispatch() -> None:
    _run(r"""
globalThis.fetch = async () => new Response(
  'event: thinking\r\ndata: {"text":\r\ndata: "ready"}\r\n\r\n',
  {headers: {'Content-Type': 'text/event-stream'}}
);
const seen = [];
await new Promise(resolve => api.streamQuery({question: 'q'}, {
  thinking: payload => seen.push(payload), done: resolve,
}));
assert.deepEqual(seen, [{text: 'ready'}]);
""")


def test_unknown_stream_events_are_ignored() -> None:
    _run(r"""
globalThis.fetch = async () => new Response(
  'event: future_event\ndata: {"text":"extension"}\n\n',
  {headers: {'Content-Type': 'text/event-stream'}}
);
let unknownCalls = 0;
await new Promise(resolve => api.streamQuery({question: 'q'}, {
  unknown: () => unknownCalls++, done: resolve,
}));
assert.equal(unknownCalls, 0);
""")


def test_answer_resolves_evidence_from_metadata_catalog() -> None:
    _run(r"""
const wire = {
  query_id: 'query', answer: 'Supported answer', route: 'agentic', steps: [], cost: {},
  metadata: {evidence: [{id: 'edge', content: 'Supporting fact', score: 0.75}]},
};
assert.deepEqual(parseAnswer(wire).evidence, wire.metadata.evidence);
delete wire.metadata;
assert.deepEqual(parseAnswer(wire).evidence, []);
wire.metadata = {evidence: 'malformed'};
assert.throws(() => parseAnswer(wire), TypeError);
""")
