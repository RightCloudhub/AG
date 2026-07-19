"""CLI package — thin re-exports for console_scripts entry points.

One module per command group; shared helpers live in ``_common``.
"""

from __future__ import annotations

from agentic_graphrag.cli._common import _open_graph_store
from agentic_graphrag.cli.baseline import run_baseline_main
from agentic_graphrag.cli.cases import eval_main, run_cases_main, score_main
from agentic_graphrag.cli.eval_cmd import badcase_main, gen_cases_main, pilot_triples_main
from agentic_graphrag.cli.graph_cmd import build_graph_main
from agentic_graphrag.cli.index_cmd import index_main
from agentic_graphrag.cli.ingest import ingest_main
from agentic_graphrag.cli.query import query_main
from agentic_graphrag.cli.schema_cmd import export_reasoning_schema_main
from agentic_graphrag.cli.spotcheck import score_spotcheck_main, spotcheck_main

__all__ = [
    "_open_graph_store",
    "ingest_main",
    "build_graph_main",
    "index_main",
    "run_cases_main",
    "score_main",
    "eval_main",
    "run_baseline_main",
    "export_reasoning_schema_main",
    "spotcheck_main",
    "score_spotcheck_main",
    "query_main",
    "gen_cases_main",
    "badcase_main",
    "pilot_triples_main",
]
