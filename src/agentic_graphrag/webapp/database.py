"""Identity database lifecycle and transactions (SQLite / PostgreSQL)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agentic_graphrag.config import resolve_path


class Database:
    def __init__(self, url: str) -> None:
        parsed = make_url(url)
        options: dict[str, Any] = {"pool_pre_ping": True}
        if parsed.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if parsed.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
            else:
                path = Path(resolve_path(parsed.database))
                path.parent.mkdir(parents=True, exist_ok=True)
                parsed = parsed.set(database=str(path))
        elif parsed.get_backend_name() == "postgresql":
            parsed = parsed.set(drivername="postgresql+psycopg")
        else:
            raise ValueError("Identity database must use SQLite or PostgreSQL")
        self.engine: Engine = create_engine(parsed, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_foreign_keys)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[Session]:
        with self.sessions() as session:
            if write and self.engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def upgrade(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self) -> None:
        self.engine.dispose()


def _sqlite_foreign_keys(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
