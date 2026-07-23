"""Reasoning-chain schema export."""

from __future__ import annotations

import argparse

from agentic_graphrag.config import resolve_path


def export_reasoning_schema_main(argv: list[str] | None = None) -> None:
    """P2-AG-06 — write reasoning_chain JSON Schema to configs/schema/."""
    parser = argparse.ArgumentParser(description="Export ReasoningChain JSON Schema")
    parser.add_argument(
        "--out",
        default="configs/schema/reasoning_chain_v1.json",
        help="Output path",
    )
    args = parser.parse_args(argv)
    from agentic_graphrag.generation.trace import export_reasoning_chain_schema

    path = export_reasoning_chain_schema(resolve_path(args.out))
    print(f"Wrote reasoning chain schema → {path}")
