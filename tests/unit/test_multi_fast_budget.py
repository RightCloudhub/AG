"""Fast-path caps must not constrain a later multi-hop escalation."""

from test_multi_runtime import setup_run

from agentic_graphrag.agent.options import QueryOptions
from agentic_graphrag.agent.pipeline import EVENT_FINAL_CHAIN, run_pipeline
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.governance.budget import BudgetTracker


def test_fast_budget_is_restored_before_escalation(monkeypatch):
    deps, config, _, _ = setup_run(monkeypatch)
    budget = BudgetTracker(max_llm_calls=20, max_tokens=50000)

    def fast(question, *_args, **kwargs):
        assert kwargs["budget"].max_tokens == 12000
        assert kwargs["budget"].max_llm_calls == 4
        kwargs["budget"].record_call(100, 50)
        result = ReasoningChain(question=question)
        result.honest_fallback("insufficient evidence")
        return result, []

    def deep(ctx, **_kwargs):
        assert ctx.run_opts.budget.max_tokens == 50000
        assert ctx.run_opts.budget.max_llm_calls == 20
        assert ctx.run_opts.budget.total_tokens == 150
        assert ctx.entry_usage[0].role == "writer"
        yield EVENT_FINAL_CHAIN, ReasoningChain(question=ctx.question, answer="result")

    monkeypatch.setattr("agentic_graphrag.agent.pipeline.run_fast_path_with_evidence", fast)
    monkeypatch.setattr("agentic_graphrag.agent.pipeline._iter_agentic", deep)
    chain = run_pipeline(
        "Who leads Acme?",
        deps.executor,
        deps.llm,
        options=QueryOptions(allow_llm=True, agent_config=config, budget=budget),
    )
    assert chain.metadata["escalated_from_fast_path"] is True
