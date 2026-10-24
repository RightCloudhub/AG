"""Prove that refactoring-sensitive contract mistakes are rejected by mypy."""

import subprocess
import sys
from pathlib import Path

from agentic_graphrag.config import ROOT_DIR

VALID = """
from agentic_graphrag.agent.checkpointer import CheckpointerBackend, make_checkpointer
from agentic_graphrag.agent.tools.registry import ToolRegistry, ToolSpec
from agentic_graphrag.config import load_prompt
from agentic_graphrag.core.enums import MessageRole, PromptName, RetrievalTool
from agentic_graphrag.core.evidence import Candidate
from agentic_graphrag.core.events import EventBus, GraphCommitted, IndexUpdated
from agentic_graphrag.core.ports.llm import Message

message = Message(role=MessageRole.USER, content="question")
tool = RetrievalTool.GRAPH_NEIGHBORS
registry: ToolRegistry[list[Candidate]] = ToolRegistry()
load_prompt(PromptName.ANSWER)
make_checkpointer(CheckpointerBackend.MEMORY)

def on_commit(event: GraphCommitted) -> None:
    pass

def register(bus: EventBus) -> None:
    bus.subscribe(GraphCommitted, on_commit)
"""
INVALID = """
Message(role="usr", content="question")
misspelled = RetrievalTool.GRAPH_NEIGHBOURS
registry.invoke("graph_neighbours")
load_prompt("answr")
make_checkpointer("memroy")

def wrong_handler() -> str:
    return "wrong result type"

wrong_spec: ToolSpec[list[Candidate]] = ToolSpec(
    name=RetrievalTool.GRAPH_NEIGHBORS, description="invalid", handler=wrong_handler
)

def on_index(event: IndexUpdated) -> None:
    pass

def wrong_registration(bus: EventBus) -> None:
    bus.subscribe(GraphCommitted, on_index)
"""


def _check(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(ROOT_DIR / "pyproject.toml"),
            str(path),
        ],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def test_contract_refactors_fail_static_checks(tmp_path: Path) -> None:
    fixture = tmp_path / "contract_fixture.py"
    fixture.write_text(VALID, encoding="utf-8")
    good = _check(fixture)
    assert good.returncode == 0, good.stdout + good.stderr
    fixture.write_text(VALID + INVALID, encoding="utf-8")
    bad = _check(fixture)
    assert bad.returncode != 0
    assert 'Argument "role"' in bad.stdout
    assert "GRAPH_NEIGHBOURS" in bad.stdout and "[attr-defined]" in bad.stdout
    assert 'Argument 2 to "subscribe"' in bad.stdout
    assert 'Argument 1 to "invoke"' in bad.stdout
    assert 'Argument 1 to "load_prompt"' in bad.stdout
    assert 'Argument 1 to "make_checkpointer"' in bad.stdout
    assert 'Argument "handler"' in bad.stdout
