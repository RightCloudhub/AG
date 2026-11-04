#!/usr/bin/env python3
"""External-baseline driver for the public benchmark runner (protocol agr-bench-jsonl/1).

Runs INSIDE an isolated, locked environment (``evals/public/envs/<system>/.venv``) and
talks to ``agentic_graphrag.eval.public.adapter_external`` over stdin/stdout, one JSON
object per line. Standard library only, so it never needs the AgenticGraphRAG package.

Backends:
  stub       deterministic token-overlap retriever for offline contract tests
  lightrag   LightRAG native query mode        (API binding UNVERIFIED — Not Executed)
  hipporag2  HippoRAG 2 ``rag_qa``             (API binding UNVERIFIED — Not Executed)

The LightRAG/HippoRAG bindings follow their public READMEs as of planning time and have
never been run here; an operator must confirm them against the pinned revision during
the live smoke run and record any change in the manifest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL = "agr-bench-jsonl/1"
_TOKEN = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


def _load_corpus(path: str) -> list[dict[str, str]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


class StubBackend:
    """Offline stand-in: ranks passages by token overlap; answers with the top title."""

    name = "stub"
    version = "stub-1"

    def __init__(self) -> None:
        self.passages: list[dict[str, str]] = []
        self.config: dict[str, Any] = {}

    def prepare(self, req: dict[str, Any]) -> dict[str, Any]:
        self.passages = _load_corpus(req["corpus_path"])
        self.config = dict(req.get("config") or {})
        usage = None if req.get("load_only") else {"llm_calls": 0, "embed_calls": 0}
        return {"passages": len(self.passages), "usage": usage}

    def answer(self, req: dict[str, Any]) -> dict[str, Any]:
        qid = req["question_id"]
        if qid in self.config.get("fail_question_ids", []):
            raise RuntimeError("429 Too Many Requests (stub)")
        if qid in self.config.get("sleep_question_ids", []):
            time.sleep(float(self.config.get("sleep_seconds", 5)))
        q = _tokens(req["question"])
        ranked = sorted(
            self.passages,
            key=lambda p: (-len(q & _tokens(p["title"] + " " + p["text"])), p["passage_id"]),
        )[: int(req.get("top_k", 5))]
        top = ranked[0]["title"] if ranked else "cannot answer"
        retrieved = [{"passage_id": p["passage_id"]} for p in ranked]
        context = [{"text": p["text"]} for p in ranked]  # text-only, like native contexts
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
        return {
            "prediction": f"Answer: {top}",
            "retrieved": retrieved,
            "context": context,
            "usage": usage,
            "score": None,
            "seed_control": "not_supported",
        }


class LightRAGBackend:  # pragma: no cover - requires the isolated LightRAG env (UNVERIFIED)
    name = "lightrag"

    def __init__(self) -> None:
        import lightrag  # noqa: F401 — only importable inside evals/public/envs/lightrag

        self.version = getattr(lightrag, "__version__", "UNVERIFIED")
        self.rag: Any = None
        self.mode = "hybrid"

    def prepare(self, req: dict[str, Any]) -> dict[str, Any]:
        from lightrag import LightRAG

        cfg = dict(req.get("config") or {})
        self.mode = cfg.get("mode", self.mode)
        self.rag = LightRAG(working_dir=req["workdir"], **cfg.get("init", {}))
        passages = _load_corpus(req["corpus_path"])
        if not req.get("load_only"):
            self.rag.insert([p["title"] + "\n" + p["text"] for p in passages])
        return {"passages": len(passages), "usage": None}

    def answer(self, req: dict[str, Any]) -> dict[str, Any]:
        from lightrag import QueryParam

        param = QueryParam(mode=self.mode, top_k=int(req.get("top_k", 5)))
        prediction = self.rag.query(req["question"], param=param)
        ctx_param = QueryParam(
            mode=self.mode, top_k=int(req.get("top_k", 5)), only_need_context=True
        )
        context = self.rag.query(req["question"], param=ctx_param)
        return {
            "prediction": str(prediction),
            "retrieved": None,
            "context": [str(context)],
            "usage": None,
            "score": None,
            "seed_control": "not_supported",
        }


class HippoRAG2Backend:  # pragma: no cover - requires the isolated HippoRAG env (UNVERIFIED)
    name = "hipporag2"

    def __init__(self) -> None:
        import hipporag  # noqa: F401 — only importable inside evals/public/envs/hipporag2

        self.version = getattr(hipporag, "__version__", "UNVERIFIED")
        self.rag: Any = None
        self.passages: list[dict[str, str]] = []

    def prepare(self, req: dict[str, Any]) -> dict[str, Any]:
        from hipporag import HippoRAG

        cfg = dict(req.get("config") or {})
        self.rag = HippoRAG(save_dir=req["workdir"], **cfg.get("init", {}))
        self.passages = _load_corpus(req["corpus_path"])
        if not req.get("load_only"):
            self.rag.index(docs=[p["title"] + "\n" + p["text"] for p in self.passages])
        return {"passages": len(self.passages), "usage": None}

    def answer(self, req: dict[str, Any]) -> dict[str, Any]:
        solutions = self.rag.rag_qa(queries=[req["question"]])[0]
        sol = solutions[0]
        docs = list(getattr(sol, "docs", []) or [])
        scores = list(getattr(sol, "doc_scores", []) or [])
        top_k = int(req.get("top_k", 5))
        return {
            "prediction": str(getattr(sol, "answer", "")),
            "retrieved": [{"text": d} for d in docs],
            "context": [{"text": d} for d in docs[:top_k]],
            "usage": None,
            "score": float(scores[0]) if scores else None,
            "seed_control": "not_supported",
        }


BACKENDS = {"stub": StubBackend, "lightrag": LightRAGBackend, "hipporag2": HippoRAG2Backend}


def handle(backend: Any, req: dict[str, Any]) -> dict[str, Any]:
    op = req.get("op")
    if op == "hello":
        return {"protocol": PROTOCOL, "system": backend.name, "version": backend.version}
    if op == "prepare":
        Path(req["workdir"]).mkdir(parents=True, exist_ok=True)
        return backend.prepare(req)
    if op == "answer":
        return backend.answer(req)
    if op == "shutdown":
        return {}
    raise ValueError(f"unknown op {op!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=sorted(BACKENDS), required=True)
    args = parser.parse_args(argv)
    backend = BACKENDS[args.backend]()
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        try:
            reply = {"ok": True, **handle(backend, req)}
        except Exception as exc:  # noqa: BLE001 — reported to the runner, never swallowed
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if req.get("op") == "shutdown":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
