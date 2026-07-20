from agentic_graphrag.eval.scoring import score_pair


def test_score_containment():
    s = score_pair("Elena Varga is the CEO", "Elena Varga")
    assert s["correct"] is True


def test_score_aliases():
    s = score_pair(
        "Orion Systems and Meridian Capital appear in WORKED_AT edges",
        "Orion Systems and Meridian Capital",
    )
    assert s["correct"] is True


def test_score_mismatch():
    s = score_pair("unrelated text about apples", "Elena Varga")
    assert s["correct"] is False


def test_score_yes_no_word_boundary():
    assert score_pair("I know nothing", "no")["correct"] is False
    assert score_pair("unknown", "no")["correct"] is False
    assert score_pair("not found", "no")["correct"] is False
    assert score_pair("the answer is no", "no")["correct"] is True
    assert score_pair("yesterday", "yes")["correct"] is False
    assert score_pair("yes we can", "yes")["correct"] is True
