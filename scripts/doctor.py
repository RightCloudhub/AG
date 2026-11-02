#!/usr/bin/env python3
"""Environment health check for a fresh or half-configured checkout.

Reads the repository layout, ``pyproject.toml``, ``.env`` and the running
interpreter. No third-party import at module level, so the report still works
when the environment is broken. TCP probes run only for live/hybrid profiles.

Usage:
    python scripts/doctor.py              # human-readable report
    python scripts/doctor.py --json       # machine-readable report
    python scripts/doctor.py --strict     # warnings also fail the exit code
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

OK = "ok"
INFO = "info"
WARN = "warn"
ERROR = "error"
_RANK = {OK: 0, INFO: 1, WARN: 2, ERROR: 3}
_STATUS_ORDER = (ERROR, WARN, INFO, OK)

# Distribution name -> import name, listed only where the two differ.
_IMPORT_NAMES = {
    "python-multipart": "multipart",
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
    "pydantic-settings": "pydantic_settings",
    "qdrant-client": "qdrant_client",
    "rank-bm25": "rank_bm25",
    "argon2-cffi": "argon2",
    "pyjwt": "jwt",
    "opentelemetry-sdk": "opentelemetry.sdk",
    "opentelemetry-exporter-otlp-proto-grpc": "opentelemetry.exporter.otlp.proto.grpc",
    "opentelemetry-instrumentation-fastapi": "opentelemetry.instrumentation.fastapi",
    "langgraph-checkpoint-sqlite": "langgraph.checkpoint.sqlite",
}

# Optional dependency group -> (severity when missing, remedy hint).
_OPTIONAL_GROUPS: dict[str, tuple[str, str]] = {
    "dev": (WARN, "./scripts/setup.sh --extras dev"),
    "webapp": (WARN, "./scripts/setup.sh --extras dev,webapp"),
    "otel": (INFO, "./scripts/setup.sh --extras dev,otel"),
    "sqlite-checkpoint": (INFO, "./scripts/setup.sh --extras dev,sqlite-checkpoint"),
}

# Written into .env these do nothing: they are read from the process environment.
_PROCESS_ONLY = (
    "AGR_PROFILE",
    "AGR_ALLOW_LLM",
    "AGR_USE_LIVE_STORES",
    "AGR_REQUIRE_AUTH",
    "AGR_API_KEYS",
    "AGR_RATE_LIMIT_QPS",
    "AGR_RATE_LIMIT_CONCURRENT",
    "AGR_TRUST_X_USER_ID",
    "AGR_LOG_LEVEL",
    "AGR_LOG_FILE",
    "AGR_REDACTION_ENABLED",
    "AGR_REDACTION_PATTERNS",
    "AGR_OTEL_ENABLED",
    "AGR_OTEL_ENDPOINT",
    "AGR_OTEL_SAMPLE_RATE",
    "AGR_API_HOST",
    "AGR_API_PORT",
    "AGR_API_RELOAD",
    "AGR_INGEST_INLINE",
    "AGR_INGEST_POLL_SECONDS",
    "AGR_RUNTIME_DIR",
)
_PROFILES = ("offline", "live", "hybrid", "eval")
_PLACEHOLDER_KEY_MARKERS = ("xxx", "changeme", "change-me", "replace", "your-", "todo", "<")
_SEED_FILES = ("seed_triples.jsonl", "pilot_triples.jsonl")
_DEFAULT_PORT = 8000
_SPEC_RE = re.compile(r">=\s*(\d+)\.(\d+)")
_NAME_RE = re.compile(r"[<>=\[\s;]")
_PROBE_TIMEOUT = 2.0


@dataclass(frozen=True)
class Check:
    """One environment check and its outcome."""

    id: str
    group: str
    status: str
    message: str
    hint: str = ""


def _check(check_id: str, group: str, ok: bool, message: str, hint: str = "") -> Check:
    return Check(check_id, group, OK if ok else WARN, message, hint)


def _read_pyproject(root: Path) -> tuple[dict, Check | None]:
    path = root / "pyproject.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")), None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, Check("ENV-00", "环境", ERROR, f"无法读取 pyproject.toml: {exc}")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse simple ``KEY=value`` lines; comments and quotes are stripped."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if not text or "=" not in text:
            continue
        key, _, value = text.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _project_table(pyproject: dict, key: str) -> object:
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return None
    return project.get(key)


def _dependency_names(requirements: object) -> list[str]:
    if not isinstance(requirements, list):
        return []
    names: list[str] = []
    for raw in requirements:
        if isinstance(raw, str) and (name := _NAME_RE.split(raw, maxsplit=1)[0].strip()):
            names.append(name)
    return names


def _importable(distribution: str) -> bool:
    """Installed-distribution check, with an import probe as the fallback.

    Metadata comes first because it is case-insensitive and covers packages that
    ship no importable module of the same name (e.g. ``types-PyYAML``).
    """
    lowered = distribution.lower()
    try:
        importlib.metadata.distribution(distribution)
        return True
    except importlib.metadata.PackageNotFoundError:
        module = _IMPORT_NAMES.get(lowered, lowered.replace("-", "_"))
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False


def _dependency_check(check_id: str, label: str, names: list[str], level: str, hint: str) -> Check:
    missing = [name for name in names if not _importable(name)]
    if not missing:
        return Check(check_id, "依赖", OK, f"{label} 就绪（{len(names)} 个包）", hint)
    shown = ", ".join(missing[:6]) + (" …" if len(missing) > 6 else "")
    message = f"{label} 缺失 {len(missing)}/{len(names)}: {shown}"
    return Check(check_id, "依赖", level, message, hint)


def _dependency_checks(pyproject: dict) -> list[Check]:
    checks = [
        _dependency_check(
            "DEP-01",
            "核心依赖",
            _dependency_names(_project_table(pyproject, "dependencies")),
            ERROR,
            "./scripts/setup.sh",
        )
    ]
    optional = _project_table(pyproject, "optional-dependencies")
    optional = optional if isinstance(optional, dict) else {}
    for index, (group, (level, hint)) in enumerate(_OPTIONAL_GROUPS.items(), start=2):
        names = _dependency_names(optional.get(group))
        checks.append(_dependency_check(f"DEP-0{index}", f"{group} 组", names, level, hint))
    return checks


def _requires_python(pyproject: dict) -> tuple[int, int] | None:
    spec = _project_table(pyproject, "requires-python")
    match = _SPEC_RE.search(spec) if isinstance(spec, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


def _env_checks(root: Path, pyproject: dict) -> list[Check]:
    version = sys.version_info[:2]
    floor = _requires_python(pyproject)
    message = f"解释器 {version[0]}.{version[1]}"
    if floor:
        message += f"（pyproject 要求 >= {floor[0]}.{floor[1]}）"
    checks: list[Check] = [
        Check(
            "ENV-01",
            "环境",
            OK if floor is None or version >= floor else ERROR,
            message,
            "" if floor is None or version >= floor else "./scripts/setup.sh",
        )
    ]
    seen = root / ".venv"
    pin_path = root / ".python-version"
    pin = pin_path.read_text(encoding="utf-8").strip() if pin_path.is_file() else ""
    if pin:
        checks.append(
            _check(
                "ENV-02",
                "环境",
                pin.split(".")[:2] == [str(version[0]), str(version[1])],
                f"解释器与 .python-version（{pin}）一致",
            )
        )
    checks.append(
        _check(
            "ENV-03",
            "环境",
            Path(sys.prefix).resolve() == seen.resolve(),
            "使用仓库 .venv（推荐）",
            "运行 ./scripts/setup.sh，然后 source .venv/bin/activate",
        )
    )
    origin = _package_origin()
    checks.append(
        Check(
            "ENV-04",
            "环境",
            OK if origin and origin.startswith(str(root / "src")) else ERROR,
            f"agentic_graphrag 来自 {origin or '未安装'}",
            "" if origin and origin.startswith(str(root / "src")) else "./scripts/setup.sh",
        )
    )
    return checks


def _package_origin() -> str | None:
    try:
        spec = importlib.util.find_spec("agentic_graphrag")
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    return str(spec.origin) if spec and spec.origin else None


def _is_placeholder_key(value: str) -> bool:
    low = value.strip().lower()
    return not low or any(low.startswith(marker) for marker in _PLACEHOLDER_KEY_MARKERS)


def _llm_check(values: Mapping[str, str]) -> Check:
    allow = values.get("AGR_ALLOW_LLM", "").strip() == "1"
    placeholder = _is_placeholder_key(values.get("LLM_API_KEY", ""))
    if allow and placeholder:
        message = "AGR_ALLOW_LLM=1 但 LLM_API_KEY 为空或占位符（启动期会拒绝启动）"
        return Check(
            "CFG-02", "配置", ERROR, message, "填写 .env 的 LLM_API_KEY，或不导出 AGR_ALLOW_LLM"
        )
    if allow:
        return Check("CFG-02", "配置", OK, "在线 LLM 已启用且密钥已填写")
    message = "在线 LLM 未启用（AGR_ALLOW_LLM != 1），离线路径不需要密钥"
    return Check("CFG-02", "配置", INFO, message)


def _config_checks(
    root: Path, values: Mapping[str, str], env_file: Mapping[str, str]
) -> list[Check]:
    checks = [
        _check(
            "CFG-01",
            "配置",
            (root / ".env").is_file(),
            ".env 存在",
            "./scripts/setup.sh 会自动从 .env.example 生成",
        )
    ]
    profile = values.get("AGR_PROFILE", "offline").strip() or "offline"
    checks.append(
        Check(
            "CFG-03",
            "配置",
            OK if profile in _PROFILES else ERROR,
            f"AGR_PROFILE={profile}",
            "取值：offline | live | hybrid | eval",
        )
    )
    checks.append(_llm_check(values))
    misplaced = [key for key in _PROCESS_ONLY if env_file.get(key, "").strip()]
    if misplaced:
        checks.append(
            Check(
                "CFG-04",
                "配置",
                INFO,
                f".env 中的进程级开关不会生效: {', '.join(misplaced)}",
                "export 后再启动，或 set -a; . ./.env; set +a",
            )
        )
    return checks


def _data_checks(root: Path) -> list[Check]:
    processed = root / "data" / "processed"
    missing = [name for name in _SEED_FILES if not (processed / name).is_file()]
    message = f"seed 数据缺失: {', '.join(missing)}" if missing else "seed / pilot 三元组就绪"
    hint = "agr-build-graph --triples data/processed/seed_triples.jsonl --no-llm"
    checks = [Check("DATA-01", "数据", WARN if missing else OK, message, hint)]
    raw = list((root / "data" / "raw").glob("*.md"))
    checks.append(Check("DATA-02", "数据", INFO, f"data/raw 语料 {len(raw)} 篇"))
    return checks


def _host_port(url: str, default_port: int) -> tuple[str, int]:
    parsed = urlparse(url if "//" in url else f"//{url}")
    return parsed.hostname or "localhost", parsed.port or default_port


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _live_backend_checks(values: Mapping[str, str], *, probe_network: bool) -> list[Check]:
    profile = values.get("AGR_PROFILE", "offline").strip() or "offline"
    if profile not in ("live", "hybrid"):
        return [Check("LIVE-01", "在线后端", INFO, f"已跳过（AGR_PROFILE={profile} 使用内存后端）")]
    if not probe_network:
        return [Check("LIVE-01", "在线后端", INFO, "已跳过（--no-probe）")]
    targets = (
        ("Neo4j", values.get("NEO4J_URI", "bolt://localhost:7687"), 7687),
        ("Qdrant", values.get("QDRANT_URL", "http://localhost:6333"), 6333),
    )
    checks: list[Check] = []
    for index, (label, url, default_port) in enumerate(targets, start=1):
        host, port = _host_port(url, default_port)
        reachable = _reachable(host, port)
        state = "可达" if reachable else "不可达（启动期会失败）"
        checks.append(
            Check(
                f"LIVE-0{index}",
                "在线后端",
                OK if reachable else ERROR,
                f"{label} {host}:{port} {state}",
                "docker compose up -d",
            )
        )
    return checks


def _web_checks(*, probe_network: bool) -> list[Check]:
    node = shutil.which("node")
    version = ""
    if node:
        try:
            result = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=5, check=False
            )
            version = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            version = ""
    label = f"node {version}" if node else "node 未安装"
    checks = [Check("WEB-01", "前端工具", INFO, label, "web/ 零构建；node 只用于校验")]
    if probe_network:
        busy = _reachable("127.0.0.1", _DEFAULT_PORT)
        state = "已被占用（agr-api / agr-webapp 可能在运行）" if busy else "空闲"
        checks.append(Check("WEB-02", "前端工具", INFO, f"端口 {_DEFAULT_PORT} {state}"))
    return checks


def collect(root: Path, env: Mapping[str, str], *, probe_network: bool = True) -> list[Check]:
    """Run every check against ``root`` and return the report rows."""
    pyproject, failure = _read_pyproject(root)
    env_file = _read_env_file(root / ".env")
    values = {**env_file, **{k: v for k, v in env.items() if v}}
    checks = [] if failure is None else [failure]
    checks += _env_checks(root, pyproject)
    checks += _dependency_checks(pyproject)
    checks += _config_checks(root, values, env_file)
    checks += _data_checks(root)
    checks += _live_backend_checks(values, probe_network=probe_network)
    checks += _web_checks(probe_network=probe_network)
    return checks


def worst_status(checks: Iterable[Check]) -> str:
    return max((check.status for check in checks), key=lambda status: _RANK[status], default=OK)


def exit_code(checks: Iterable[Check], *, strict: bool) -> int:
    worst = worst_status(checks)
    if worst == ERROR or (strict and worst == WARN):
        return 1
    return 0


def render_text(checks: Sequence[Check]) -> str:
    lines: list[str] = []
    current = ""
    for check in checks:
        if check.group != current:
            current = check.group
            lines.append(f"\n[{current}]")
        lines.append(f"  {check.status:<5} {check.id}  {check.message}")
        if check.hint and check.status in (WARN, ERROR):
            lines.append(f"        ↳ {check.hint}")
    counts = {status: sum(1 for c in checks if c.status == status) for status in _STATUS_ORDER}
    lines.append(
        f"\n结论：{worst_status(checks)}  error={counts[ERROR]} warn={counts[WARN]} "
        f"info={counts[INFO]} ok={counts[OK]}"
    )
    return "\n".join(lines)


def render_json(checks: Sequence[Check]) -> str:
    return json.dumps(
        {
            "summary": {
                "status": worst_status(checks),
                **{status: sum(1 for c in checks if c.status == status) for status in _RANK},
            },
            "checks": [asdict(check) for check in checks],
        },
        ensure_ascii=False,
        indent=2,
    )


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--strict", action="store_true", help="warn 也返回退出码 1")
    parser.add_argument("--no-probe", action="store_true", help="不做 TCP 探测（离线 / CI）")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    checks = collect(args.root, os.environ, probe_network=not args.no_probe)
    print(render_json(checks) if args.json else render_text(checks))
    return exit_code(checks, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
