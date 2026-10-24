"""Verify the optional SQLite adapter with a real offline connection."""

import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agentic_graphrag.agent.checkpointer import make_checkpointer

SQLITE_MODULE = "langgraph.checkpoint.sqlite"


class ConnectionSaver(MemorySaver):
    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__()
        self.connection = connection
        connection.execute("CREATE TABLE contract_check (value INTEGER)")


def test_sqlite_factory_passes_an_open_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = ModuleType(SQLITE_MODULE)
    module.SqliteSaver = ConnectionSaver
    monkeypatch.setitem(sys.modules, SQLITE_MODULE, module)
    saver = make_checkpointer("sqlite", path=tmp_path / "nested/checkpoints.sqlite")
    assert isinstance(saver, ConnectionSaver)
    try:
        assert saver.connection.execute("SELECT 1").fetchone() == (1,)
        assert (tmp_path / "nested/checkpoints.sqlite").is_file()
    finally:
        saver.connection.close()


def test_sqlite_factory_closes_connection_if_saver_creation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connections: list[sqlite3.Connection] = []

    def fail(connection: sqlite3.Connection) -> None:
        connections.append(connection)
        raise RuntimeError("saver unavailable")

    module = ModuleType(SQLITE_MODULE)
    module.SqliteSaver = fail
    monkeypatch.setitem(sys.modules, SQLITE_MODULE, module)
    with pytest.raises(RuntimeError, match="saver unavailable"):
        make_checkpointer("sqlite", path=tmp_path / "checkpoints.sqlite")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")
