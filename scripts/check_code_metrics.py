#!/usr/bin/env python3
"""Enforce hard code-quality metrics on src/agentic_graphrag (engineering gate)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agentic_graphrag"
MAX_FILE = 300
MAX_FN = 50
MAX_NEST = 3
MAX_POS = 3
MAX_CC = 10


def main() -> int:
    files_bad: list[str] = []
    funcs_bad: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > MAX_FILE:
            files_bad.append(f"{path.relative_to(ROOT)}: {len(lines)} lines")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            files_bad.append(f"{path}: syntax error {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            issue = _fn_issues(node, lines)
            if issue:
                funcs_bad.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name} — {issue}")
    if files_bad or funcs_bad:
        print("CODE METRICS FAILED", file=sys.stderr)
        for x in files_bad:
            print("  FILE", x, file=sys.stderr)
        for x in funcs_bad:
            print("  FUNC", x, file=sys.stderr)
        return 1
    print(f"OK: all files ≤{MAX_FILE}, functions within limits under {SRC}")
    return 0


def _fn_issues(node: ast.AST, lines: list[str]) -> str:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    body = lines[node.lineno - 1 : node.end_lineno]
    flen = sum(1 for ln in body if ln.strip())
    parts: list[str] = []
    if flen > MAX_FN:
        parts.append(f"len={flen}")
    nest = _max_nesting(node)
    if nest > MAX_NEST:
        parts.append(f"nest={nest}")
    cc = _cyclomatic(node)
    if cc > MAX_CC:
        parts.append(f"cc={cc}")
    npos = _pos_params(node)
    if npos > MAX_POS:
        parts.append(f"params={npos}")
    return ", ".join(parts)


def _max_nesting(node: ast.AST, depth: int = 0) -> int:
    nest_types = (
        ast.If,
        ast.For,
        ast.While,
        ast.With,
        ast.Try,
        ast.Match,
        ast.AsyncFor,
        ast.AsyncWith,
    )
    max_d = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, nest_types):
            max_d = max(max_d, _max_nesting(child, depth + 1))
        else:
            max_d = max(max_d, _max_nesting(child, depth))
    return max_d


def _cyclomatic(node: ast.AST) -> int:
    cc = 1
    for n in ast.walk(node):
        if isinstance(
            n,
            (
                ast.If,
                ast.For,
                ast.While,
                ast.ExceptHandler,
                ast.With,
                ast.Assert,
                ast.AsyncFor,
                ast.AsyncWith,
            ),
        ):
            cc += 1
        elif isinstance(n, ast.BoolOp):
            cc += max(0, len(n.values) - 1)
        elif isinstance(n, ast.comprehension):
            cc += 1 + len(n.ifs)
        elif isinstance(n, ast.Match):
            cc += len(n.cases)
    return cc


def _pos_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    params = list(node.args.posonlyargs) + list(node.args.args)
    if params and params[0].arg in {"self", "cls"}:
        params = params[1:]
    return len(params)


if __name__ == "__main__":
    raise SystemExit(main())
