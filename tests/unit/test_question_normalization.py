"""Conservative language normalization: positive cases and false-link boundaries."""

import pytest

from agentic_graphrag.agent.entities import extract_entity_mentions
from agentic_graphrag.agent.entity_linking import match_entity_spelling, repair_entity_mentions
from agentic_graphrag.agent.planner import plan_offline
from agentic_graphrag.agent.planner_chain import parse_chain
from agentic_graphrag.agent.question_normalization import normalize_question
from agentic_graphrag.agent.triage import Route, triage
from agentic_graphrag.config_matching import question_matching_config

NAMES = ["Example Systems", "Sample Device"]


@pytest.mark.parametrize(
    "question",
    [
        "ＷＨＯ　ＩＳ　ＴＨＥ　ＣＥＯ　ＯＦ　ＴＨＥ　ＰＡＲＥＮＴ　"
        "ＣＯＭＰＡＮＹ　ＯＦ　ＥＸＡＭＰＬＥ　ＳＹＳＴＥＭＳ？",
        "Who is the chief executive of Example Systems’s holding company?",
        "Who is the CEO of the parent company of example systmes?",
        "Example Systems的母公司的首席执行官是谁？",
        "Example Systems 的母公司由谁担任首席执行官？",
    ],
)
def test_planning_and_triage_share_normalization(question):
    plan = plan_offline(question, known_entities=NAMES)
    assert [s.text for s in plan] == [
        "What is the parent company of Example Systems?",
        "Who is the CEO of {from:sq1}?",
    ]
    result = triage(question, allow_llm=False, known_entities=NAMES)
    assert result.route == Route.AGENTIC
    assert result.estimated_hops == 2


def test_normalization_does_not_rewrite_aliases_inside_entity_names():
    name = "Chief Executive Devices"
    assert normalize_question(f"Who is the chief executive of {name}?", [name]) == (
        f"Who is the ceo of {name}?"
    )
    assert normalize_question("Who owns Manufacturer Lab?", ["Manufacturer Lab"]) == (
        "Who owns Manufacturer Lab?"
    )


def test_fuzzy_link_requires_full_name_and_clear_margin():
    assert match_entity_spelling("Example Systmes", NAMES) == "Example Systems"
    assert match_entity_spelling("example", NAMES) is None
    assert match_entity_spelling("Unrelated Corporation", NAMES) is None
    assert match_entity_spelling("Example Systemz", ["Example Systema", "Example Systemb"]) is None
    assert (
        repair_entity_mentions("Who owns Example Systema?", ("Example Systema", "Example Systemb"))
        == "Who owns Example Systema?"
    )


def test_unknown_names_and_token_boundaries_are_preserved():
    query = "Who owns Phantom Holdings Group?"
    assert normalize_question(query, NAMES) == query
    assert repair_entity_mentions("XExample SystemsZ", tuple(NAMES)) == "XExample SystemsZ"
    assert extract_entity_mentions("Example Systems的母公司是谁？", NAMES)[0] == NAMES[0]
    assert normalize_question("Who owns Sample, Device?", NAMES) == "Who owns Sample, Device?"


@pytest.mark.parametrize("key", list(question_matching_config().chinese_frames))
def test_chinese_frame_directions_match_existing_english_frames(key):
    cue = question_matching_config().chinese_frames[key][0]
    chain = parse_chain(f"Sample Device 的{cue}的母公司是谁？", NAMES)
    assert chain is not None
    assert [fr.key for fr in chain.frames] == [key, "parent"]


def test_chinese_chain_rejects_unconsumed_language():
    assert parse_chain("Sample Device 的制造商如果倒闭了其母公司是谁？", NAMES) is None
    assert parse_chain("Sample Device 的制造商和母公司是谁？", NAMES) is None
