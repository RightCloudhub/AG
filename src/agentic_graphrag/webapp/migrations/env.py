"""Alembic environment for the optional identity database."""

from alembic import context

from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import Base
from agentic_graphrag.webapp.settings import WebSettings


def run_migrations() -> None:
    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        context.configure(connection=supplied, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    database = Database(WebSettings().database_url)
    try:
        with database.engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        database.close()


run_migrations()
