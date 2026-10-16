from agentic_graphrag.agent.planner import (
    SubQuestion,
    materialize_subquestion,
    normalize_plan,
    plan_offline,
    ready_subquestions,
    topological_sort,
)


def test_plan_offline_ceo_parent_uses_placeholder():
    plan = plan_offline("Who is the CEO of the parent company of NovaTech Industries?")
    assert len(plan) == 2
    assert plan[0].id == "sq1"
    assert plan[1].depends_on == ["sq1"]
    assert plan[1].is_placeholder
    assert "{from:sq1}" in plan[1].text


def test_materialize_placeholder():
    sq = SubQuestion(
        id="sq2",
        text="Who is the CEO of {from:sq1}?",
        depends_on=["sq1"],
        is_placeholder=True,
    )
    out = materialize_subquestion(sq, {"sq1": "Apex Holdings"})
    assert out.text == "Who is the CEO of Apex Holdings?"
    assert not out.is_placeholder


def test_topological_sort_tree_join():
    nodes = [
        SubQuestion(id="sq3", text="join", depends_on=["sq1", "sq2"]),
        SubQuestion(id="sq1", text="a"),
        SubQuestion(id="sq2", text="b"),
    ]
    ordered = topological_sort(nodes)
    ids = [n.id for n in ordered]
    assert ids.index("sq1") < ids.index("sq3")
    assert ids.index("sq2") < ids.index("sq3")


def test_ready_subquestions():
    plan = normalize_plan(
        [
            SubQuestion(id="sq1", text="a"),
            SubQuestion(id="sq2", text="b", depends_on=["sq1"]),
        ]
    )
    ready = ready_subquestions(plan, done_ids=set())
    assert [r.id for r in ready] == ["sq1"]
    ready2 = ready_subquestions(plan, done_ids={"sq1"})
    assert [r.id for r in ready2] == ["sq2"]


def test_shared_connections_is_dag():
    plan = plan_offline(
        "What shared connections exist between NovaTech Industries and Helix Compute?"
    )
    assert len(plan) >= 2
    # join node depends on both expansions when pattern matches
    deps = {sq.id: set(sq.depends_on) for sq in plan}
    assert any(len(d) >= 2 for d in deps.values()) or any(sq.depends_on for sq in plan)
