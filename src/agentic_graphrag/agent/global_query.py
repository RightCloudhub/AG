"""Corpus-wide map/reduce with explicit document coverage and source verification."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agentic_graphrag.agent.global_findings import MappedFinding, map_report, reduce_findings
from agentic_graphrag.agent.global_units import MapUnit, report_units
from agentic_graphrag.config_summary import SummaryConfig
from agentic_graphrag.core.knowledge import KnowledgeScope, SummaryRecord
from agentic_graphrag.core.ports.llm import LLMClient
from agentic_graphrag.core.ports.stores import DocStore
from agentic_graphrag.core.reasoning import Claim, QueryStatus, ReasoningChain
from agentic_graphrag.generation.evidence_catalog import _catalog_entry
from agentic_graphrag.generation.global_verifier import original_excerpt, verify_finding
from agentic_graphrag.governance.budget import BudgetTracker
from agentic_graphrag.governance.execution import check_execution
from agentic_graphrag.observability.degradation import record_degradation

_VERIFY_RESERVE = 8


@dataclass
class GlobalContext:
    scope: KnowledgeScope
    docs: DocStore
    summaries: list[SummaryRecord]
    llm: LLMClient
    budget: BudgetTracker
    config: SummaryConfig
    query_id: str
    coverage: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)


def global_query(question: str, ctx: GlobalContext) -> Iterator[tuple[str, Any]]:
    doc_ids = set(ctx.docs.list_ids(tenant_id=ctx.scope.tenant_id))
    ctx.coverage = dict.fromkeys(sorted(doc_ids), "pending")
    reports = _map_units(ctx, doc_ids)
    mapped: list[MappedFinding] = []
    reserve = min(_VERIFY_RESERVE, max(1, ctx.budget.max_llm_calls // 3))
    for index, unit in enumerate(reports):
        check_execution()
        report = unit.report
        if (
            index >= ctx.config.max_map_units
            or ctx.budget.llm_calls >= ctx.budget.max_llm_calls - reserve
        ):
            break
        try:
            mapped.extend(map_report(question, report, llm=ctx.llm))
            ctx.units[report.summary_id] = "processed"
        except Exception as exc:
            record_degradation("global_map", "map_failed", exc)
            ctx.units[report.summary_id] = "failed"
        _mark_unit(ctx, unit, reports)
        yield (
            "global_progress",
            {
                "query_id": ctx.query_id,
                "stage": "map",
                "unit": report.summary_id,
                "processed_documents": list(ctx.coverage.values()).count("processed"),
                "total_documents": len(doc_ids),
            },
        )
    partial = any(status != "processed" for status in ctx.coverage.values())
    try:
        reduced = _reduce(question, mapped, ctx=ctx, partial=partial)
    except Exception as exc:
        record_degradation("global_reduce", "reduce_failed", exc)
        reduced, partial = mapped, True
    serious = [
        finding for finding in mapped if finding.importance >= ctx.config.serious_finding_threshold
    ]
    reduced = list({(item.text, item.sources): item for item in [*reduced, *serious]}.values())
    chain = _verified_chain(question, reduced, ctx=ctx, partial=partial)
    yield "__final_chain__", chain


def _reports(ctx: GlobalContext, selected: set[str]) -> list[SummaryRecord]:
    ready = [
        report
        for report in ctx.summaries
        if report.status in {"ready", "partial"}
        and set(report.document_ids) <= selected
        and report.scope == ctx.scope
        and all(
            source.scope == ctx.scope and source.doc_id in selected
            for finding in report.findings
            for source in finding.sources
        )
        and _originals_available(report, ctx)
    ]
    return sorted(ready, key=lambda report: (report.kind != "document", report.summary_id))


def _originals_available(report: SummaryRecord, ctx: GlobalContext) -> bool:
    try:
        for finding in report.findings:
            for source in finding.sources:
                original_excerpt(source, ctx.docs, scope=ctx.scope)
    except ValueError:
        return False
    return True


def _map_units(ctx: GlobalContext, selected: set[str]) -> list[MapUnit]:
    result = []
    for report in _reports(ctx, selected):
        try:
            result.extend(report_units(report, token_limit=ctx.config.source_tokens_per_call))
        except ValueError as exc:
            record_degradation("global_map", "input_budget_exceeded", exc)
    ctx.units = {unit.report.summary_id: "pending" for unit in result}
    return result


def _mark_unit(ctx: GlobalContext, unit: MapUnit, units: list[MapUnit]) -> None:
    if unit.report.kind != "document":
        return
    statuses = {
        ctx.units[row.report.summary_id] for row in units if row.original_id == unit.original_id
    }
    status = (
        "processed" if statuses == {"processed"} and unit.report.status == "ready" else "partial"
    )
    if statuses == {"failed"}:
        status = "failed"
    ctx.coverage.update(dict.fromkeys(unit.report.document_ids, status))


def _reduce(
    question: str, mapped: list[MappedFinding], *, ctx: GlobalContext, partial: bool
) -> list[MappedFinding]:
    check_execution()
    if ctx.budget.max_llm_calls - ctx.budget.llm_calls <= len(mapped):
        return mapped
    return reduce_findings(
        question,
        mapped,
        llm=ctx.llm,
        partial=partial,
        batch_size=ctx.config.reduce_batch_size,
        token_limit=ctx.config.source_tokens_per_call,
    )


def _verified_chain(
    question: str, findings: list[MappedFinding], *, ctx: GlobalContext, partial: bool
) -> ReasoningChain:
    chain = ReasoningChain(question=question, query_id=ctx.query_id)
    evidence = {}
    for finding in sorted(findings, key=lambda item: (-item.importance, item.id)):
        check_execution()
        try:
            candidates = verify_finding(
                finding,
                ctx.docs,
                scope=ctx.scope,
                llm=ctx.llm,
                token_limit=ctx.config.source_tokens_per_call,
            )
        except Exception as exc:
            record_degradation("global_verify", "verification_failed", exc)
            partial = True
            continue
        if not candidates:
            partial = True
            continue
        evidence.update({item.id: item for item in candidates})
        chain.claims.append(Claim(text=finding.text, evidence_ids=[item.id for item in candidates]))
    _describe_coverage(chain, ctx=ctx, partial=partial)
    chain.metadata["evidence"] = [_catalog_entry(item) for item in evidence.values()]
    _answer(chain, partial=partial)
    chain.cost.llm_calls = ctx.budget.llm_calls
    chain.cost.tokens = ctx.budget.total_tokens
    chain.cost.prompt_tokens = ctx.budget.prompt_tokens
    chain.cost.completion_tokens = ctx.budget.completion_tokens
    return chain


def _describe_coverage(chain: ReasoningChain, *, ctx: GlobalContext, partial: bool) -> None:
    processed = [doc for doc, status in ctx.coverage.items() if status == "processed"]
    chain.metadata.update(
        strategy="global",
        intent="global_summary",
        llm_only=True,
        coverage={
            "total_documents": len(ctx.coverage),
            "processed_documents": len(processed),
            "partial": partial,
            "documents": ctx.coverage,
            "total_map_units": len(ctx.units),
            "processed_map_units": list(ctx.units.values()).count("processed"),
            "failed_map_units": list(ctx.units.values()).count("failed"),
        },
    )
    chain.missing_info = [doc for doc, status in ctx.coverage.items() if status != "processed"]


def _answer(chain: ReasoningChain, *, partial: bool) -> None:
    if chain.claims:
        chain.status = QueryStatus.PARTIAL if partial else QueryStatus.ANSWERED
        chain.answer = "\n".join(
            f"- {claim.text} [{']['.join(claim.evidence_ids)}]" for claim in chain.claims
        )
        if partial:
            chain.answer = "概括覆盖不完整，以下仅包含已验证的结论。\n" + chain.answer
    else:
        chain.honest_fallback("No supported global findings are available")
