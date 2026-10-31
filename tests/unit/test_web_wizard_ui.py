"""Structure and offline runtime checks for the notebook chat modules (web/)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from agentic_graphrag.config import ROOT_DIR

WEB = ROOT_DIR / "web"
STATIC = WEB / "static"
JS = STATIC / "js"
NODE = shutil.which("node")
MAX_FILE_LINES = 300
NEW_MODULES = (
    "i18n.js",
    "theme.js",
    "chat/types.js",
    "chat/wizard.js",
    "chat/clarify.js",
    "chat/progress.js",
    "chat/followups.js",
    "chat/subgraph.js",
    "chat/presenters.js",
    "chat/hoprail.js",
    "chat/pages.js",
    "chat/use_composer.js",
    "chat/use_clarify.js",
    "chat/tpl_composer.js",
    "chat/tpl_page.js",
    "chat/tpl_thread.js",
    "store/conversations.js",
    "store/graph_focus.js",
    "store/preferences.js",
    "store/shell.js",
    "shell/use_shell.js",
    "shell/use_palette.js",
    "shell/use_inspector.js",
    "shell/tpl_topbar.js",
    "shell/tpl_notebook.js",
    "shell/tpl_inspector.js",
    "knowledge/ingest_wizard.js",
)
FEATURE_FLAGS = (
    "PLAN_PREVIEW",
    "RERUN_FROM_HOP",
    "EXTRACTION_PREVIEW",
    "GRAPH_NEIGHBORS",
    "RELATION_CATALOG",
)
FEEDBACK_REASONS = ("wrong_entity", "wrong_chain", "missing_answer", "extra_answers")
# Copy may *explain* a missing endpoint; code must never request one.
CODE_MODULES = tuple(m for m in NEW_MODULES if m != "i18n.js") + (
    "api.js",
    "views/chat.js",
    "admin/knowledge.js",
    "admin/graph.js",
    "admin/review.js",
    "admin/ops.js",
    "app.js",
)
I18N_IMPORTERS = (
    "views/chat.js",
    "admin/knowledge.js",
    "admin/review.js",
    "admin/ops.js",
    "chat/use_composer.js",
)
CHAT_SYMBOLS = (
    "useComposer",
    "useInspector",
    "useClarify",
    "suggestFollowups",
    "missingInfoCard",
    "detectEntityAmbiguity",
    "progress.finishProgress",
    "progress.stopProgress",
    "buildPages",
    "logFrame",
    "WELCOME_TEMPLATE",
    "PAGE_TEMPLATE",
    "COMPOSER_TEMPLATE",
    "INSPECTOR_TEMPLATE",
    "WIZARD_TEMPLATE",
)
SHORTCUTS = ("PALETTE", "NEW_QUESTION", "THEME", "INSPECTOR", "PREV_HOP", "NEXT_HOP")
FEATURE_CSS = ("shell.css", "notebook.css", "inspector.css", "responsive.css")


def _js(rel: str) -> str:
    return (JS / rel).read_text(encoding="utf-8")


def _module(rel: str) -> str:
    return (JS / rel).as_uri()


def test_new_modules_exist_and_fit_size_budget():
    missing = [rel for rel in NEW_MODULES if not (JS / rel).is_file()]
    assert not missing, missing
    oversized = [rel for rel in NEW_MODULES if len(_js(rel).splitlines()) > MAX_FILE_LINES]
    assert not oversized, oversized


def test_feature_flags_default_off_and_proposed_endpoints_never_called():
    contracts = _js("contracts.js")
    for flag in FEATURE_FLAGS:
        assert f"{flag}: false" in contracts, flag
    code = "\n".join(_js(rel) for rel in CODE_MODULES)
    assert "ProposedEndpoint" not in code
    assert "/v1/query/plan" not in code and "/v1/docs/preview" not in code


def test_copy_is_centralised_in_i18n():
    i18n = _js("i18n.js")
    assert "export const zh" in i18n and "export const text = zh" in i18n
    for rel in I18N_IMPORTERS:
        assert "from '../i18n.js'" in _js(rel), rel
    assert "from './i18n.js'" in _js("app.js")
    # the brief's copy rules (§9): full-width punctuation, a space between figure and unit
    assert "{n} 条证据" in i18n and "{ms} ms" in i18n and "，" in i18n


def test_chat_wires_composer_inspector_clarify_and_rail():
    chat = _js("views/chat.js")
    for symbol in CHAT_SYMBOLS:
        assert symbol in chat, symbol
    composer = _js("chat/tpl_composer.js")
    assert 'role="dialog"' in composer and 'aria-modal="true"' in composer
    assert 'role="listbox"' in composer and "aria-autocomplete" in composer
    assert "wizardKeydown" in composer
    inspector = _js("shell/tpl_inspector.js")
    assert 'role="tablist"' in inspector and 'role="tabpanel"' in inspector
    keys = _js("chat/use_composer.js")
    assert "KeyboardKey.ESCAPE" in keys and "KeyboardKey.ENTER" in keys
    assert "SlashTrigger" in keys
    app = _js("app.js")
    for shortcut in SHORTCUTS:
        assert f"Shortcut.{shortcut}" in app, shortcut


def test_feedback_reasons_and_feature_css_are_present():
    contracts = _js("contracts.js")
    for reason in FEEDBACK_REASONS:
        assert f"'{reason}'" in contracts, reason
    css = "\n".join((STATIC / "css" / name).read_text(encoding="utf-8") for name in FEATURE_CSS)
    assert "var(--hop" in css and "@media (max-width: 1439px)" in css
    assert "@media (max-width: 767px)" in css
    assert ".hop__node" in css and ".evcard" in css and ".composer__box" in css
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "/web/static/css/inspector.css" in html and "/web/static/css/wizard.css" in html


needs_node = pytest.mark.skipif(NODE is None, reason="Node is only needed for JS verification")


def _run(source: str) -> None:
    program = (
        "import assert from 'node:assert/strict';\n"
        f"import * as wizard from {_module('chat/wizard.js')!r};\n"
        f"import * as progress from {_module('chat/progress.js')!r};\n"
        f"import * as subgraph from {_module('chat/subgraph.js')!r};\n"
        f"import * as clarify from {_module('chat/clarify.js')!r};\n"
        f"import * as rail from {_module('chat/hoprail.js')!r};\n"
        f"import {{buildPages}} from {_module('chat/pages.js')!r};\n"
        f"import {{suggestFollowups}} from {_module('chat/followups.js')!r};\n"
        f"import {{parseAnswer}} from {_module('responses.js')!r};\n" + source
    )
    result = subprocess.run(
        [str(NODE), "--input-type=module"],
        input=program,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@needs_node
def test_wizard_composes_questions_without_mutating_state() -> None:
    _run(r"""
let s = { ...wizard.initialWizard(), entity: { name: 'NovaTech Industries', type: 'Company' } };
const before = s;
s = wizard.pushRelation(s, 'SUBSIDIARY_OF');
s = wizard.pushRelation(s, 'CEO_OF~inv');
s = wizard.pushRelation(s, 'WORKED_AT');
assert.equal(before.chain.length, 0);
assert.equal(wizard.composeQuestion(s), 'What are the companies where the CEO of '
  + 'the parent company of NovaTech Industries previously worked?');
assert.equal(wizard.draftSubQuestions(s).length, 3);
assert.deepEqual(wizard.moveRelation(s, 2, -1).chain,
  ['SUBSIDIARY_OF', 'WORKED_AT', 'CEO_OF~inv']);
assert.deepEqual(wizard.removeRelation(s, 0).chain, ['CEO_OF~inv', 'WORKED_AT']);
""")


@needs_node
def test_progress_model_folds_stream_events_and_final_chain() -> None:
    _run(r"""
let m = progress.emptyProgress(0);
m = progress.applyThinking(m, { stage: 'plan', text: 'plan', detail: '1. a\n2. b' });
m = progress.applySubQuestion(m, { hop: 1, sub_question: 'q1' }, 10);
m = progress.applyHopDone(m, { hop: 1, conclusion: 'c', critic_action: 'next_hop' }, 250);
assert.equal(m.hops[0].elapsedMs, 240);
const step = {
  hop: 1, sub_question: 'q1', evidence_ids: ['a', 'b'], tool_calls: [{ hits: ['b', 'c'] }],
};
const done = progress.finishProgress(m, { steps: [step] });
assert.equal(done.hops[0].hits, 3);
assert.equal(done.phase, 'answer');
const second = progress.applySubQuestion(m, { hop: 2 }, 300);
assert.equal(progress.stopProgress(second).hops[1].state, 'stopped');
assert.deepEqual(progress.planLines(m), ['1. a', '2. b']);
""")


@needs_node
def test_hoprail_derives_hops_counts_and_origins_from_real_fields() -> None:
    _run(r"""
assert.equal(rail.hopClass(1), 'hop-c1');
assert.equal(rail.hopClass(7), 'hop-c1');
assert.equal(rail.hopClass(0), 'hop-c0');
const long = rail.shortLabel('What is the parent company of NovaTech Industries?');
assert.ok(long.startsWith('What is the parent') && long.endsWith('…') && long.length <= 25, long);
assert.equal(rail.shortLabel('张三所在公司的 CEO？'), '张三所在公司的 CEO');
assert.equal(rail.stripQuestionWords('What is the parent company of X?'), 'parent company of X?');
const ceo = 'Who is the CEO of Apex Holdings?';
assert.equal(rail.hopTitle({ sub_question: ceo, conclusion: 'Elena Varga' }), 'Elena Varga');
assert.equal(rail.hopTitle({ sub_question: ceo, conclusion: '' }), 'CEO of Apex Holdings');
const nbr = 'nbr:X:CEO_OF:Y:0';
const answer = {
  answer: 'Meridian', status: 'answered',
  evidence: [{ id: 'a:0' }, { id: nbr }, { id: 'z:1' }],
  steps: [
    { hop: 1, sub_question: 'q1', conclusion: 'c1', critic_action: 'sufficient',
      evidence_ids: ['a:0', nbr] },
    { hop: 2, sub_question: 'q2', conclusion: '', critic_action: 'give_up',
      evidence_ids: ['a:0', 'missing'] },
  ],
};
const items = rail.railFromAnswer(answer, null);
assert.deepEqual(items.map(i => i.kind), ['hop', 'hop', 'answer']);
assert.equal(items[0].evidenceCount, 2);
assert.equal(items[1].state, 'failed');
assert.equal(items[2].state, 'failed');
assert.equal(rail.linkPending(items, 0), false);
assert.equal(rail.hopOfEvidence(answer).get('a:0'), 1);
assert.deepEqual([...rail.hopCounts(answer)], [[1, 2]]);
assert.deepEqual(rail.evidenceOrigin('d94c9f26a3706d25:0'),
  { kind: 'doc', doc: 'd94c9f26', chunk: 1 });
assert.deepEqual(rail.evidenceOrigin('nbr:Apex Holdings:CEO_OF:Elena Varga:0'),
  { kind: 'graph', relation: 'CEO_OF' });
assert.equal(rail.evidenceOrigin('opaque'), null);
assert.equal(rail.stepHop([1, 2, 3], 0, 1), 1);
assert.equal(rail.stepHop([1, 2, 3], 3, 1), 3);
assert.equal(rail.stepHop([1, 2, 3], 2, -1), 1);
const base = { startedAt: 0, planDetail: '', note: '', route: '', cacheHit: false };
const planning = rail.railFromProgress({ ...base, phase: 'plan', state: 'active',
  planText: 'p', hops: [] });
assert.deepEqual(planning.map(i => i.kind), ['plan', 'answer']);
const hop = { hop: 1, subQuestion: 'q', startedAt: 0, elapsedMs: 5, tools: [], hits: 0,
  conclusion: '', criticAction: '', state: 'active' };
const running = rail.railFromProgress({ ...base, phase: 'retrieve', state: 'active',
  planText: '', hops: [hop] });
assert.equal(running[0].evidenceCount, rail.UNKNOWN_COUNT);
const none = rail.railFromAnswer({ ...answer, status: 'no_answer', steps: [answer.steps[0]] },
  null);
assert.equal(none[1].state, 'failed');
const pages = buildPages([
  { role: 'user', text: 'Q1', ts: 1 }, { role: 'assistant', data: { steps: [] }, ts: 2 },
  { role: 'user', text: 'Q2', ts: 3 }, { role: 'notice', text: 'n', ts: 4 },
], true);
assert.equal(pages.length, 2);
assert.equal(pages[0].answer.index, 1);
assert.equal(pages[1].live, true);
assert.equal(pages[1].rows.length, 1);
""")


@needs_node
def test_subgraph_parses_graph_evidence_and_highlights_citations() -> None:
    _run(r"""
const nbr = 'nbr:NovaTech Industries:PARENT_OF:Apex Holdings:0';
const evidence = [
  { id: nbr, content: 'Apex Holdings -[PARENT_OF]-> NovaTech Industries (Company)', score: 0.1 },
  { id: 'abc:0', content: '# NovaTech Industries\n\nPlain text chunk', score: 0.2 },
  { id: 'path:A:C:0', content: 'A -[CEO_OF]-> B <-[WORKED_AT]- C', score: 0.3 },
];
const graph = subgraph.buildSubgraph(evidence);
assert.equal(graph.edges.length, 3);
assert.equal(graph.nodes.find(n => n.name === 'Apex Holdings').type, 'Company');
assert.equal(subgraph.edgesCitedBy(graph, [nbr]).size, 1);
assert.equal(subgraph.isGraphEvidence(evidence[1]), false);
""")


@needs_node
def test_clarify_cards_use_entity_index_and_missing_info() -> None:
    _run(r"""
const one = { id: '1', name: 'Apex Holdings', type: 'Company', aliases: [] };
const two = { id: '2', name: 'Apex Holdings', type: 'Event', aliases: [] };
const q = 'Who leads Apex Holdings?';
const card = await clarify.detectEntityAmbiguity(q, async () => [one, two]);
assert.equal(card.kind, 'entity');
assert.equal(clarify.applyEntityChoice(card, card.options[1]),
  'Who leads Apex Holdings (Event)?');
assert.equal(clarify.detectBroadQuestion('what else?').kind, 'broad');
assert.equal(clarify.detectBroadQuestion('Who leads Apex Holdings?'), null);
const partial = { status: 'partial', missing_info: ['founder of X'] };
const missing = clarify.missingInfoCard(partial, 'X?');
assert.equal(missing.multi, true);
assert.match(clarify.applyMissingChoice(missing, missing.options),
  /Please also cover: founder of X\./);
""")


@needs_node
def test_answer_parser_keeps_real_fields_and_followups_are_bounded() -> None:
    _run(r"""
const ev = 'nbr:Apex Holdings:CEO_OF:Elena Varga:0';
const wire = {
  query_id: 'q1', question: 'Who is the CEO of Apex Holdings?', answer: 'Elena Varga',
  route: 'agentic', status: 'answered',
  claims: [{ text: 'Elena Varga', evidence_ids: [ev] }],
  steps: [{
    hop: 1, sub_question: 'q', evidence_ids: [ev], tool_calls: [{ tool: 'graph_neighbors' }],
  }],
  cost: { latency_ms: 12 }, missing_info: [], explored_paths: ['p'],
  metadata: {
    confidence: { level: 'high', score: 0.85, reasons: ['r'] },
    evidence: [{
      id: ev, content: 'Elena Varga -[CEO_OF]-> Apex Holdings (Person)', score: 0.5,
      source: 'fusion',
    }],
  },
};
const answer = parseAnswer(wire);
assert.equal(answer.confidence.score, 0.85);
assert.equal(answer.evidence[0].source, 'fusion');
const followups = suggestFollowups(answer, wire.question);
assert.ok(followups.length >= 1 && followups.length <= 3);
assert.ok(followups.every(q => q.endsWith('?') && q !== wire.question));
""")


@needs_node
def test_all_vue_templates_compile_under_node() -> None:
    """Template syntax errors only surface at runtime in a browser; compile them here instead."""
    result = subprocess.run(
        [str(NODE), str(ROOT_DIR / "scripts" / "check_web_templates.mjs")],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
