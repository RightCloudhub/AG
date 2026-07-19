"""Wall-clock query timeout guardrail."""

from __future__ import annotations

import time

from agentic_graphrag.agent.guardrails import GuardrailConfig, Guardrails


def test_timeout_trips():
    cfg = GuardrailConfig(max_hops=10, query_timeout_seconds=0)
    # 0 means disabled in our check (query_timeout_seconds > 0)
    g = Guardrails(cfg)
    g.on_hop_start()
    assert not g.state.tripped or "timeout" not in g.state.reason

    cfg2 = GuardrailConfig(max_hops=10, query_timeout_seconds=1)
    g2 = Guardrails(cfg2)
    g2.state.started_at = time.monotonic() - 2.0
    g2.on_hop_start()
    assert g2.state.tripped
    assert "timeout" in g2.state.reason
