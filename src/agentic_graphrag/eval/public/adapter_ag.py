"""AG single-tier / AG registry arms on the production ``QueryService`` (B-05).

Both arms force the full agentic loop (``force_agentic=True``) on the same shared
prepared index; they differ only in the per-arm config's register flag. Answer and
retrieval caches are off; the LLM response cache is trial-scoped (no replay).
"""

from __future__ import annotations

import dataclasses
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agentic_graphrag.app.contracts import QueryRequest
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.core.reasoning import ReasoningChain
from agentic_graphrag.eval.public.accounting import ledger_from_cost
from agentic_graphrag.eval.public.adapters_base import AdapterResult, PrepareReport, error_result
from agentic_graphrag.eval.public.alignment import EvidenceRef, PassageResolver, align
from agentic_graphrag.eval.public.composition import BENCH_TENANT, check_prepared, trial_config
from agentic_graphrag.eval.public.enums import ArmName, TrialStatus
from agentic_graphrag.eval.public.indexing import PreparedIndex
from agentic_graphrag.eval.public.records import Passage, QuestionInput

_STATUS = {
    "answered": TrialStatus.ANSWERED,
    "partial": TrialStatus.PARTIAL,
    "no_answer": TrialStatus.NO_ANSWER,
}


class AGArmAdapter:
    def __init__(
        self,
        arm: ArmName,
        service: QueryService,
        index: PreparedIndex,
        *,
        trial_cache_root: Path,
        timeout_ms: int | None = None,
    ) -> None:
        self.arm = arm
        self.service = service
        self.index = index
        self.trial_cache_root = trial_cache_root
        self.timeout_ms = timeout_ms
        self.resolver: PassageResolver | None = None

    def prepare(self, passages: list[Passage], corpus_sha256: str) -> PrepareReport:
        """Validate (not rebuild) the shared index against the frozen corpus."""
        check_prepared(
            self.index.passage_ids,
            self.index.corpus_sha256,
            expected_ids={p.passage_id for p in passages},
            expected_sha256=corpus_sha256,
        )
        self.resolver = PassageResolver(passages, relation_sources=self.index.relation_sources)
        return PrepareReport(
            arm=self.arm,
            passages=len(passages),
            corpus_sha256=corpus_sha256,
            usage=self.index.usage,
            stats={"shared_index": True, **self.index.stats},
        )

    def answer(self, question: QuestionInput, *, run_seed: int) -> AdapterResult:
        t0 = time.perf_counter()
        cache_dir = (
            self.trial_cache_root / f"{question.dataset.value}-{run_seed}-{question.question_id}"
        )
        svc = dataclasses.replace(self.service, cfg=trial_config(self.service.cfg, cache_dir))
        request = QueryRequest(
            question=question.question, force_agentic=True, timeout_ms=self.timeout_ms
        )
        try:
            chain = svc.run_query_chain(
                request, tenant_id=BENCH_TENANT, user_id=f"bench:{self.arm}"
            )
            return self._result(chain, t0)
        except Exception as exc:  # noqa: BLE001 — failures stay in the denominator
            return error_result(exc, elapsed_ms=_ms(t0))
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

    def _result(self, chain: ReasoningChain, t0: float) -> AdapterResult:
        if self.resolver is None:
            raise RuntimeError("prepare() must run before answer()")
        payload = chain.model_dump(mode="json")
        catalog = _catalog(payload)
        retrieved, r_report = align(self.resolver, _refs(_retrieved_ids(payload), catalog))
        context, c_report = align(self.resolver, _refs(list(catalog), catalog))
        cited, k_report = align(self.resolver, _refs(_cited_ids(payload), catalog))
        elapsed = _ms(t0)
        return AdapterResult(
            prediction=chain.answer,
            status=_STATUS.get(chain.status.value, TrialStatus.NO_ANSWER),
            native_status=chain.status.value,
            retrieved_passage_ids=retrieved,
            context_passage_ids=context,
            cited_passage_ids=cited,
            alignment={"retrieved": r_report, "context": c_report, "cited": k_report},
            usage=ledger_from_cost(payload.get("cost") or {}, elapsed_ms=elapsed),
            timings_ms={"total": elapsed, "service_latency": float(chain.cost.latency_ms)},
            extra={"route": str(chain.route), "steps": len(chain.steps)},
        )

    def close(self) -> None:
        """The prepared index is shared by both AG arms; it is closed by its owner."""


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * MILLISECONDS_PER_SECOND


def _catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    meta = payload.get("metadata") or {}
    rows = meta.get("evidence") or []
    return {str(r["id"]): r for r in rows if isinstance(r, dict) and r.get("id")}


def _retrieved_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for step in payload.get("steps") or []:
        for tool in step.get("tool_calls") or []:
            ids.extend(str(h) for h in tool.get("hits") or [])
        ids.extend(str(e) for e in step.get("evidence_ids") or [])
    meta = payload.get("metadata") or {}
    ids.extend(str(e) for e in meta.get("register_retrieved_ids") or [])
    return list(dict.fromkeys(ids))


def _cited_ids(payload: dict[str, Any]) -> list[str]:
    ids = [str(e) for c in payload.get("claims") or [] for e in c.get("evidence_ids") or []]
    return list(dict.fromkeys(ids))


def _refs(ids: Iterable[str], catalog: dict[str, dict[str, Any]]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for rid in ids:
        row = catalog.get(rid, {})
        chunk_ids = tuple(
            str(c["chunk_id"]) for c in row.get("citations") or [] if c.get("chunk_id")
        )
        refs.append(
            EvidenceRef(ref_id=rid, content=str(row.get("content") or ""), chunk_ids=chunk_ids)
        )
    return refs
