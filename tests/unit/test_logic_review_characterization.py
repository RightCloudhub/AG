"""Regression tests for logic issues found in code review (formerly characterization)."""

from __future__ import annotations

import math

from agentic_graphrag.api.service_query import MS_PER_SECOND
from agentic_graphrag.eval.scoring import score_pair


def _timeout_seconds(timeout_ms: int) -> int:
    """Mirror service_query conversion (ceil, min 1)."""
    return max(1, math.ceil(timeout_ms / MS_PER_SECOND))


def test_score_pair_no_inside_know_is_not_correct() -> None:
    """B8 fix: gold 'no' must not match inside 'know'."""
    row = score_pair("I know nothing about it", "no")
    assert row["correct"] is False


def test_score_pair_yes_inside_yesterday_is_not_correct() -> None:
    row = score_pair("yesterday's report", "yes")
    assert row["correct"] is False


def test_score_pair_explicit_yes_no_still_works() -> None:
    assert score_pair("the answer is no", "no")["correct"] is True
    assert score_pair("yes", "yes")["correct"] is True


def test_timeout_ms_subsecond_not_disabled() -> None:
    """B4 fix: sub-1s timeouts become 1s, never 0 (which disabled wall-clock)."""
    for ms in (100, 500, 999):
        assert _timeout_seconds(ms) == 1
    assert _timeout_seconds(1000) == 1
    assert _timeout_seconds(1500) == 2
