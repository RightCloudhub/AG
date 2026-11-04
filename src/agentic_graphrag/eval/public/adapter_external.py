"""External baselines (LightRAG, HippoRAG 2) behind an isolated subprocess (B-05).

The external package runs in its own locked environment (``evals/public/envs/<system>``)
and speaks a line-delimited JSON protocol on stdin/stdout, so it never enters core or
offline imports. Each request has a deadline; a timed-out driver is killed and
restarted (its prepared index is reloaded, not rebuilt — ``load_only``).
"""

from __future__ import annotations

import json
import selectors
import subprocess
import time
from pathlib import Path
from typing import IO, Any

from agentic_graphrag.core.constants import MILLISECONDS_PER_SECOND
from agentic_graphrag.eval.public.accounting import ledger_from_payload
from agentic_graphrag.eval.public.adapters_base import AdapterResult, PrepareReport, error_result
from agentic_graphrag.eval.public.alignment import EvidenceRef, PassageResolver, align
from agentic_graphrag.eval.public.enums import ArmName, TrialStatus
from agentic_graphrag.eval.public.records import Passage, QuestionInput

PROTOCOL = "agr-bench-jsonl/1"
HELLO_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 5.0


class DriverError(RuntimeError):
    """The external driver answered ``ok: false`` or broke the protocol."""


class SubprocessAdapter:
    def __init__(
        self,
        arm: ArmName,
        command: list[str],
        *,
        workdir: Path,
        request_timeout: float,
        native_config: dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> None:
        self.arm = arm
        self.command = list(command)
        self.workdir = workdir
        self.request_timeout = request_timeout
        self.native_config = dict(native_config or {})
        self.top_k = top_k
        self.proc: subprocess.Popen[str] | None = None
        self.resolver: PassageResolver | None = None
        self._prepare_request: dict[str, Any] | None = None
        self.info: dict[str, Any] = {}

    # -- process management -------------------------------------------------
    def _start(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        stderr = (self.workdir / "driver.stderr.log").open("a", encoding="utf-8")
        self.proc = subprocess.Popen(  # noqa: S603 — fixed argv from the manifest
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            bufsize=1,
        )
        self.info = self._request({"op": "hello"}, HELLO_TIMEOUT_SECONDS)
        if self.info.get("protocol") != PROTOCOL:
            raise DriverError(f"driver protocol mismatch: {self.info.get('protocol')!r}")

    def _ensure(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self._start()
            if self._prepare_request is not None:
                reload = {**self._prepare_request, "load_only": True}
                self._request(reload, self.request_timeout)

    def _kill(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def _request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = _read_line(self.proc.stdout, timeout)
        if line is None:
            self._kill()
            raise TimeoutError(f"external driver timed out after {timeout:.0f}s")
        if not line:
            self._kill()
            raise DriverError("external driver exited without a response")
        reply: dict[str, Any] = json.loads(line)
        if not reply.get("ok"):
            raise DriverError(str(reply.get("error") or "driver error"))
        return reply

    # -- adapter protocol ---------------------------------------------------
    def prepare(self, passages: list[Passage], corpus_sha256: str) -> PrepareReport:
        t0 = time.perf_counter()
        corpus = self.workdir / "corpus.jsonl"
        self.workdir.mkdir(parents=True, exist_ok=True)
        with corpus.open("w", encoding="utf-8") as fh:
            for p in passages:  # permitted corpus text only — no answers or labels
                row = {"passage_id": p.passage_id, "title": p.title, "text": p.text}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        request = {
            "op": "prepare",
            "corpus_path": str(corpus),
            "corpus_sha256": corpus_sha256,
            "workdir": str(self.workdir / "index"),
            "config": self.native_config,
            "native_query_cache": False,
        }
        self._ensure()
        reply = self._request({**request, "load_only": False}, self.request_timeout)
        self._prepare_request = request
        if int(reply.get("passages", -1)) != len(passages):
            raise DriverError(f"driver indexed {reply.get('passages')} of {len(passages)} passages")
        self.resolver = PassageResolver(passages)
        elapsed = (time.perf_counter() - t0) * MILLISECONDS_PER_SECOND
        usage = ledger_from_payload(reply.get("usage"), elapsed_ms=elapsed)
        return PrepareReport(
            arm=self.arm,
            passages=len(passages),
            corpus_sha256=corpus_sha256,
            usage=usage.model_copy(update={"scope": "indexing"}),
            stats={"driver": self.info},
        )

    def answer(self, question: QuestionInput, *, run_seed: int) -> AdapterResult:
        t0 = time.perf_counter()
        try:
            self._ensure()
            reply = self._request(
                {
                    "op": "answer",
                    "question_id": question.question_id,
                    "question": question.question,
                    "run_seed": run_seed,
                    "top_k": self.top_k,
                },
                self.request_timeout,
            )
            return self._result(reply, t0)
        except Exception as exc:  # noqa: BLE001 — failures stay in the denominator
            return error_result(
                exc, elapsed_ms=(time.perf_counter() - t0) * MILLISECONDS_PER_SECOND
            )

    def _result(self, reply: dict[str, Any], t0: float) -> AdapterResult:
        if self.resolver is None:
            raise RuntimeError("prepare() must run before answer()")
        retrieved, r_report = align(self.resolver, _refs(reply.get("retrieved")))
        context, c_report = align(self.resolver, _refs(reply.get("context")))
        elapsed = (time.perf_counter() - t0) * MILLISECONDS_PER_SECOND
        score = reply.get("score")
        return AdapterResult(
            prediction=str(reply.get("prediction") or ""),
            status=TrialStatus.ANSWERED,
            confidence=None if score is None else float(score),
            retrieved_passage_ids=retrieved if reply.get("retrieved") is not None else None,
            context_passage_ids=context if reply.get("context") is not None else None,
            cited_passage_ids=None,
            alignment={"retrieved": r_report, "context": c_report},
            usage=ledger_from_payload(reply.get("usage"), elapsed_ms=elapsed),
            timings_ms={"total": elapsed},
            seed_control=str(reply.get("seed_control") or "not_supported"),
        )

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self._request({"op": "shutdown"}, SHUTDOWN_TIMEOUT_SECONDS)
        except (DriverError, TimeoutError, OSError):
            pass
        self._kill()


def _read_line(stream: IO[str] | None, timeout: float) -> str | None:
    """One stdout line, ``""`` on EOF, ``None`` on timeout."""
    assert stream is not None
    with selectors.DefaultSelector() as sel:
        sel.register(stream, selectors.EVENT_READ)
        if not sel.select(timeout):
            return None
    return stream.readline()


def _refs(items: Any) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for item in items or []:
        if isinstance(item, dict):
            refs.append(
                EvidenceRef(
                    ref_id=str(item.get("passage_id") or ""), content=str(item.get("text") or "")
                )
            )
        else:
            refs.append(EvidenceRef(content=str(item)))
    return refs
