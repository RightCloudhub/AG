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
