#!/usr/bin/env python3
"""Live-only LLM smoke test and GraphRAG evaluation with two required inputs."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from unittest.mock import patch

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from agentic_graphrag.agent.executor import Executor
from agentic_graphrag.app.query_service import QueryService
from agentic_graphrag.app.wiring import build_executor_for_service
from agentic_graphrag.bootstrap import CAPABILITY_MATRIX, LLMMode, Profile, RuntimeProfile
from agentic_graphrag.cli.cases_run import run_case_row_via_app
from agentic_graphrag.config import (
    AppConfig,
    Settings,
    build_llm_provider,
    get_config,
    resolve_path,
)
from agentic_graphrag.core.ports.llm import Message
from agentic_graphrag.eval.scoring import score_report_file
from agentic_graphrag.governance.budget import BudgetTracker


class TestDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    limit: int = Field(ge=0)
    cases: str
    seed_triples: str
    prompt: str


class TestReply(BaseModel):
    ok: Literal[True]
    answer: Literal["4", 4]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="在线 LLM 测试：只需 API key 和模型名")
    parser.add_argument("--model", help="模型名；同时用于 strong/light 两档")
    parser.add_argument("--base-url", help="覆盖默认网关或 .env 中的 LLM_BASE_URL")
    parser.add_argument("--limit", type=int, help="最多运行的题数；0 表示全部，默认 5")
    parser.add_argument("--cases", help="项目题目 JSONL 路径")
    parser.add_argument("--seed-triples", help="与题目匹配的图谱三元组 JSONL 路径")
    parser.add_argument("--preflight-only", action="store_true", help="只测试普通对话和 JSON 输出")
    parser.add_argument("--skip-offline", action="store_true", help="跳过同一批题的离线对照")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit 必须大于等于 0")
    return args


def configure(args: argparse.Namespace, defaults: TestDefaults) -> tuple[Settings, AppConfig]:
    settings = Settings(_env_file=resolve_path(".env"))
    key = settings.llm_api_key.strip()
    if not key or "your-key" in key:
        if not sys.stdin.isatty():
            raise ValueError("请在 .env 中填写 LLM_API_KEY，或在终端运行以隐藏输入 key")
        key = getpass.getpass("LLM API key（输入隐藏）: ").strip()
    if not key or "your-key" in key:
        raise ValueError("LLM API key 不能为空或为占位值")
    model = args.model or settings.llm_strong_model
    if not model and sys.stdin.isatty():
        model = input("Model name: ").strip()
    if not model or not model.strip():
        raise ValueError("请填写 LLM_STRONG_MODEL，或用 --model 指定模型名")
    base_url = args.base_url or (
        settings.llm_base_url if "llm_base_url" in settings.model_fields_set else defaults.base_url
    )
    url = httpx.URL(base_url.strip().rstrip("/"))
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.query:
        raise ValueError("base URL 必须是无账号、密码、查询参数的 HTTP(S) 接口地址")
    settings = settings.model_copy(update={"llm_api_key": key, "llm_base_url": str(url)})
    cfg = get_config().model_copy(deep=True)
    cfg.llm.strong_model = cfg.llm.light_model = model.strip()
    return settings, cfg


def write_json(path: Path, value: Any, key: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(serialized.replace(key, "[REDACTED]") + "\n", encoding="utf-8")


def error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = {
            401: "API key 无效或已过期",
            403: "API key 没有访问该模型的权限",
            404: "检查 base URL 和模型名",
            429: "额度不足或触发限流",
        }.get(code, "检查模型是否支持 temperature=0 和 response_format=json_object")
        return f"HTTP {code}: {hint}"
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时：检查网络、代理和服务端状态"
    if isinstance(exc, httpx.RequestError):
        return "连接失败：检查网络、代理、TLS 证书和 base URL"
    return f"{type(exc).__name__}: 配置、模型响应格式或运行状态异常"


def preflight(settings: Settings, cfg: AppConfig, prompt_path: Path) -> dict[str, Any]:
    budget = BudgetTracker()
    llm = build_llm_provider(settings=settings, cfg=cfg, budget=budget, cache_dir=None)
    messages = [Message(role="user", content=prompt_path.read_text(encoding="utf-8"))]
    checks = []
    for name, response_format in [("chat", None), ("json", {"type": "json_object"})]:
        start = perf_counter()
        try:
            reply = llm.complete(messages, response_format=response_format)
            if name == "json":
                TestReply.model_validate_json(reply)
            elif not reply.strip():
                raise ValueError("empty response")
            check = {"name": name, "passed": True, "reply": reply}
        except Exception as exc:
            check = {"name": name, "passed": False, "error": error_message(exc)}
        check["latency_ms"] = round((perf_counter() - start) * 1000, 1)
        checks.append(check)
        print(f"  {name}: {'PASS' if check['passed'] else check['error']}", flush=True)
        if not check["passed"]:
            break
    return {
        "passed": len(checks) == 2 and all(c["passed"] for c in checks),
        "checks": checks,
        "usage": budget.snapshot(),
    }


def sample_cases(path: Path, limit: int) -> list[dict[str, Any]]:
    groups: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or not isinstance(case.get("question"), str):
            raise ValueError("每道题必须包含字符串 question 字段")
        if not case["question"].strip() or not isinstance(case.get("gold_answer"), str):
            raise ValueError("题目不能为空，且必须包含字符串 gold_answer 字段")
        groups[str(case.get("category", case.get("hops", "other")))].append(case)
    selected = []
    while groups and (limit == 0 or len(selected) < limit):
        for category in list(groups):
            selected.append(groups[category].popleft())
            if not groups[category]:
                del groups[category]
            if limit and len(selected) >= limit:
                break
    if not selected:
        raise ValueError("题目文件为空")
    return selected


def chat_executor(**kwargs: Any) -> Executor:
    """Use the production executor with the vector channel explicitly disabled."""
    executor = build_executor_for_service(**kwargs)
    executor.vector = None
    return executor


def run_batch(
    cases: list[dict[str, Any]],
    *,
    settings: Settings,
    cfg: AppConfig,
    seed: Path,
    out: Path,
    online: bool,
) -> dict[str, Any]:
    mode = "online" if online else "offline"
    config = cfg.model_copy(deep=True)
    config.paths.cache_dir = str(out / mode / "cache")
    config.paths.processed_dir = str(out / mode / "runtime")
    rp = RuntimeProfile(
        profile=Profile.EVAL,
        caps=CAPABILITY_MATRIX[Profile.EVAL],
        llm_mode=LLMMode.LIVE if online else LLMMode.OFF,
    )
    previous = os.environ.get("AGR_RUNTIME_DIR")
    os.environ["AGR_RUNTIME_DIR"] = config.paths.processed_dir
    app = None
    rows = []
    report_path = out / f"{mode}.jsonl"
    try:
        app = QueryService.create_for_profile(rp, cfg=config, settings=settings, seed_triples=seed)
        app.enable_cache = False
        app.retrieval_cache = None
        with (
            patch("agentic_graphrag.app.query_run.build_executor_for_service", chat_executor),
            report_path.open("w", encoding="utf-8") as report,
        ):
            for index, case in enumerate(cases, 1):
                row = run_case_row_via_app(case, app=app, force_agentic=True)
                text = json.dumps(row, ensure_ascii=False).replace(
                    settings.llm_api_key, "[REDACTED]"
                )
                report.write(text + "\n")
                report.flush()
                rows.append(json.loads(text))
                calls = row.get("cost", {}).get("llm_calls", 0)
                print(
                    f"  {mode} {index}/{len(cases)}: {row['status']}, llm_calls={calls}",
                    flush=True,
                )
    finally:
        if app is not None:
            app.close()
        if previous is None:
            os.environ.pop("AGR_RUNTIME_DIR", None)
        else:
            os.environ["AGR_RUNTIME_DIR"] = previous
    accuracy = score_report_file(report_path).to_dict()
    calls = sum(row.get("cost", {}).get("llm_calls", 0) for row in rows)
    errors = sum(row.get("status") == "error" for row in rows)
    degraded = sum(bool(row.get("metadata", {}).get("llm_degraded")) for row in rows)
    return {
        "accuracy": accuracy,
        "llm_calls": calls,
        "errors": errors,
        "degraded": degraded,
        "tokens": sum(row.get("cost", {}).get("tokens", 0) for row in rows),
        "passed": errors == 0 and degraded == 0 and (not online or calls > 0),
    }


def write_report(out: Path, summary: dict[str, Any], key: str) -> None:
    write_json(out / "summary.json", summary, key)
    lines = [
        "# 在线 LLM 测试报告",
        "",
        f"模型：{summary['model']}",
        f"接口：{summary['base_url']}",
        "",
        f"测试通过：{summary['passed']}",
        "",
        "普通对话和 JSON 输出的预检结果见 preflight.json。",
        "",
        "项目测试使用内存图谱与在线对话模型，关闭向量调用。",
        "",
        "| 模式 | 答对 / 总数 | 准确率 | LLM 调用 | tokens | 运行错误 | LLM 降级 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode in ("online", "offline"):
        if mode not in summary:
            continue
        result = summary[mode]
        acc = result["accuracy"]
        lines.append(
            f"| {mode} | {acc['correct']}/{acc['total']} | {acc['accuracy_pct']}% | "
            f"{result['llm_calls']} | {result['tokens']} | "
            f"{result['errors']} | {result['degraded']} |"
        )
    lines.extend(
        [
            "",
            "通过表示接口与项目调用正常；准确率单独报告，不设通过阈值。",
            "题目为合成语料，小样本结果仅用于冒烟检查。",
            "tokens 依赖服务商返回的 usage，未返回时为 0。",
        ]
    )
    (out / "report.md").write_text(
        "\n".join(lines).replace(key, "[REDACTED]") + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    defaults = TestDefaults.model_validate(
        yaml.safe_load(resolve_path("configs/online_eval.yaml").read_text(encoding="utf-8"))
    )
    try:
        settings, cfg = configure(args, defaults)
        prompt_path = resolve_path(defaults.prompt)
        cases = (
            []
            if args.preflight_only
            else sample_cases(
                resolve_path(args.cases or defaults.cases),
                defaults.limit if args.limit is None else args.limit,
            )
        )
        seed = resolve_path(args.seed_triples or defaults.seed_triples)
        if not args.preflight_only and not seed.is_file():
            raise ValueError("找不到 seed triples 文件，请使用 --seed-triples 指定")
        if not prompt_path.is_file():
            raise ValueError("找不到连通性测试 prompt 文件")
    except (ValueError, OSError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    out = resolve_path("reports/online_eval") / datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    out.mkdir(parents=True)
    summary: dict[str, Any] = {
        "model": cfg.llm.strong_model,
        "base_url": settings.llm_base_url,
        "passed": False,
    }
    print(
        f"报告目录：{out}\n接口：{settings.llm_base_url}\n模型：{cfg.llm.strong_model}",
        flush=True,
    )
    print("1. 在线预检（最多 2 次真实请求，服务商可能计费）", flush=True)
    try:
        result = preflight(settings, cfg, prompt_path)
        write_json(out / "preflight.json", result, settings.llm_api_key)
        summary["preflight"] = result
        if result["passed"]:
            if not args.preflight_only:
                print(f"2. 项目测试：{len(cases)} 道题", flush=True)
                summary["online"] = run_batch(
                    cases, settings=settings, cfg=cfg, seed=seed, out=out, online=True
                )
                if not args.skip_offline:
                    print("3. 同一批题离线对照", flush=True)
                    summary["offline"] = run_batch(
                        cases, settings=settings, cfg=cfg, seed=seed, out=out, online=False
                    )
            summary["passed"] = all(
                summary.get(mode, {"passed": True})["passed"] for mode in ("online", "offline")
            )
    except Exception as exc:
        summary["error"] = error_message(exc)
        print(summary["error"], file=sys.stderr)
    write_report(out, summary, settings.llm_api_key)
    print(f"{'PASS' if summary['passed'] else 'FAIL'}：{out / 'report.md'}", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
