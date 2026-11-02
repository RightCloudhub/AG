"""Setup-entry consistency: scripts, docs and pyproject must agree.

Guards the single setup entry point designed in ``docs/design/SETUP_DESIGN.md``:
one bootstrap script, one pinned interpreter, one recommended install path.
The checks are offline and independent of the developer's environment.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "setup.sh"
BOOTSTRAP = ROOT / "scripts" / "bootstrap_poc.sh"
DOCTOR = ROOT / "scripts" / "doctor.py"
# Docs that tell contributors how to install. docs/reviews/ is a historical log.
SETUP_DOCS = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/ops/RUNBOOK.md",
    "docs/ops/WEBAPP.md",
    "docs/product/WEBAPP_GUIDE.md",
)
DEFAULT_EXTRAS_RE = re.compile(r'^EXTRAS="([^"]+)"', re.MULTILINE)
REQUIRES_PYTHON_RE = re.compile(r">=\s*(\d+)\.(\d+)")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _doctor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agr_setup_doctor", DOCTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations via sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_python_pin_matches_requires_python() -> None:
    pin = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    floor = REQUIRES_PYTHON_RE.search(_pyproject()["project"]["requires-python"])
    assert floor is not None, "requires-python 缺少 >= 下限"
    assert tuple(pin.split(".")[:2]) == (floor.group(1), floor.group(2))


def test_setup_scripts_are_executable_and_parse() -> None:
    for script in (SETUP, BOOTSTRAP):
        assert script.is_file(), f"{script} 缺失"
        assert script.stat().st_mode & 0o111, f"{script} 必须可执行"
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_setup_default_extras_exist_in_pyproject() -> None:
    match = DEFAULT_EXTRAS_RE.search(SETUP.read_text(encoding="utf-8"))
    assert match is not None, "setup.sh 中找不到 EXTRAS 默认值"
    groups = _pyproject()["project"]["optional-dependencies"]
    for extra in (name.strip() for name in match.group(1).split(",")):
        assert extra in groups, f"可选依赖组 {extra} 不在 pyproject.toml 中"


@pytest.mark.parametrize("relative", SETUP_DOCS)
def test_docs_point_at_the_setup_entry_point(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "uv pip install -e" not in text, f"{relative} 仍使用未读取锁文件的安装命令"
    assert "scripts/setup.sh" in text, f"{relative} 未指向 scripts/setup.sh"


def test_bootstrap_script_does_not_rely_on_bare_python() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert ".venv/bin/python" in text or "agr-" in text


def _ci_uv_commands() -> list[str]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    runs = [
        str(step["run"])
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "run" in step
    ]
    return [
        line.strip()
        for command in runs
        for line in command.splitlines()
        if line.strip().startswith("uv ")
    ]


def test_ci_asserts_the_lockfile_is_current() -> None:
    """``uv --frozen`` does not check lock freshness; only ``--locked`` does."""
    commands = _ci_uv_commands()
    assert commands, "CI 中找不到 uv 调用"
    stale = [command for command in commands if "--frozen" in command]
    assert not stale, f"uv --frozen 不校验锁文件时效性: {stale}"
    assert all("--locked" in command for command in commands), commands


def test_setup_script_rejects_a_stale_lock() -> None:
    text = SETUP.read_text(encoding="utf-8")
    assert "--frozen" not in text
    assert "SYNC_ARGS+=(--locked)" in text


def test_doctor_flags_an_empty_checkout(tmp_path: Path) -> None:
    doctor = _doctor()
    checks = doctor.collect(tmp_path, {}, probe_network=False)
    assert doctor.worst_status(checks) == doctor.ERROR
    assert doctor.exit_code(checks, strict=False) == 1
    json.loads(doctor.render_json(checks))
    assert "结论" in doctor.render_text(checks)


def test_doctor_exit_code_semantics() -> None:
    doctor = _doctor()
    warn = doctor.Check("X-01", "环境", doctor.WARN, "示例警告")
    ok = doctor.Check("X-02", "环境", doctor.OK, "示例通过")
    assert doctor.exit_code([ok], strict=False) == 0
    assert doctor.exit_code([warn], strict=False) == 0
    assert doctor.exit_code([warn], strict=True) == 1
    assert doctor.exit_code([ok, warn], strict=False) == 0
