"""Evaluation helpers: cases, gold gen, scoring, baseline, reports."""

from agentic_graphrag.eval.baseline_rag import (
    BaselineVectorRAG,
    build_baseline_pipeline,
    run_baseline_cases,
)
from agentic_graphrag.eval.cases import EvalCase, StratificationSpec, validate_stratification
from agentic_graphrag.eval.scoring import score_pair, score_report_file, write_accuracy_summary

__all__ = [
    "BaselineVectorRAG",
    "EvalCase",
    "StratificationSpec",
    "build_baseline_pipeline",
    "run_baseline_cases",
    "score_pair",
    "score_report_file",
    "validate_stratification",
    "write_accuracy_summary",
]
