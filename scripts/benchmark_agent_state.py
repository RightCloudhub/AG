#!/usr/bin/env python3
"""Measure Pydantic round-trips in a real four-hop offline agent query.

Run with ``PYTHONPATH=src .venv/bin/python scripts/benchmark_agent_state.py``.
Store construction and warmup are excluded. Timed methods include checkpoint
worker threads; wall time is measured separately without instrumentation.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from pydantic import BaseModel

from agentic_graphrag.agent.guardrails import GuardrailConfig
from agentic_graphrag.agent.loop import run_agentic_query
from agentic_graphrag.agent.state import STATE_SCHEMA_VERSION
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.core.reasoning import ReasoningChain

QUESTION = (
    "Who is the CEO of the parent company of the competitor of the producer of Frost Lakehouse?"
)
HOPS = 4
DEFAULT_ITERATIONS = 50
DEFAULT_WARMUP = 5
NANOSECONDS_PER_MILLISECOND = 1_000_000


@dataclass
class MethodTimings:
    calls: dict[str, int] = field(default_factory=dict)
    nanoseconds: dict[str, int] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def record(self, method: str, started: int) -> None:
        elapsed = time.perf_counter_ns() - started
        with self.lock:
            self.calls[method] = self.calls.get(method, 0) + 1
            self.nanoseconds[method] = self.nanoseconds.get(method, 0) + elapsed


def measure_methods(run: Callable[[], ReasoningChain], iterations: int) -> MethodTimings:
    """Time both public methods without changing their validation/serialization."""
    timings = MethodTimings()
    original_validate = BaseModel.model_validate.__func__
    original_dump = BaseModel.model_dump

    def validate(cls: type[BaseModel], value: object, **kwargs: object) -> BaseModel:
        started = time.perf_counter_ns()
        try:
            return original_validate(cls, value, **kwargs)
        finally:
            timings.record("model_validate", started)

    def dump(instance: BaseModel, **kwargs: object) -> dict[str, object]:
        started = time.perf_counter_ns()
        try:
            return original_dump(instance, **kwargs)
        finally:
            timings.record("model_dump", started)

    with (
        patch.object(BaseModel, "model_validate", classmethod(validate)),
        patch.object(BaseModel, "model_dump", dump),
    ):
        for _ in range(iterations):
            check_hops(run())
    return timings


def check_hops(chain: ReasoningChain) -> None:
    hops = [step.hop for step in chain.steps]
    if hops != list(range(1, HOPS + 1)):
        raise RuntimeError(f"Benchmark requires exactly four executed hops; got {hops}")


def benchmark(iterations: int, warmup: int) -> dict[str, object]:
    service = QueryService.create_offline(seed_triples="data/processed/pilot_triples.jsonl")
    service.enable_cache = False
    service.retrieval_cache = None
    executor = service._build_executor()
    guards = GuardrailConfig.from_app_config(service.cfg, max_hops=HOPS)

    def run() -> ReasoningChain:
        return run_agentic_query(
            QUESTION, executor, None, guard_cfg=guards, allow_llm=False, tenant_id="default"
        )

    try:
        for _ in range(warmup):
            check_hops(run())
        durations = []
        for _ in range(iterations):
            started = time.perf_counter_ns()
            check_hops(run())
            durations.append((time.perf_counter_ns() - started) / NANOSECONDS_PER_MILLISECOND)
        timings = measure_methods(run, iterations)
        methods = {
            name: {
                "calls_per_query": timings.calls.get(name, 0) / iterations,
                "milliseconds_per_query": timings.nanoseconds.get(name, 0)
                / iterations
                / NANOSECONDS_PER_MILLISECOND,
            }
            for name in ("model_validate", "model_dump")
        }
        return {
            "state_version": STATE_SCHEMA_VERSION,
            "question": QUESTION,
            "hops": HOPS,
            "iterations": iterations,
            "warmup": warmup,
            "wall_median_ms": statistics.median(durations),
            "methods": methods,
            "validate_dump_ms_per_query": sum(timings.nanoseconds.values())
            / iterations
            / NANOSECONDS_PER_MILLISECOND,
        }
    finally:
        service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0:
        parser.error("iterations must be positive and warmup nonnegative")
    result = json.dumps(benchmark(args.iterations, args.warmup), indent=2)
    print(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
