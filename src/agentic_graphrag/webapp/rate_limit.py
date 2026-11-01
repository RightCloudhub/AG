"""Database-backed fixed-window authentication limits shared by Web workers."""

from __future__ import annotations

import time

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agentic_graphrag.app.errors import ApiError
from agentic_graphrag.webapp.database import Database
from agentic_graphrag.webapp.models import AuthRateLimitBucket as Bucket
from agentic_graphrag.webapp.security import token_digest
from agentic_graphrag.webapp.settings import WebSettings


def check_auth_limit(
    db: Database, settings: WebSettings, *, action: str, ip: str, account: str = ""
) -> None:
    window = int(time.time()) // settings.auth_window_seconds
    identities = [f"{action}:ip:{ip}"]
    if account:
        identities.append(f"{action}:account:{account.strip().casefold()}")
    insert = sqlite_insert if db.engine.dialect.name == "sqlite" else pg_insert
    limited = False
    with db.transaction(write=True) as session:
        session.execute(delete(Bucket).where(Bucket.window < window - 2))
        for identity in identities:
            statement = insert(Bucket).values(key=token_digest(identity), window=window, hits=1)
            upsert = statement.on_conflict_do_update(
                index_elements=[Bucket.key, Bucket.window], set_={"hits": Bucket.hits + 1}
            ).returning(Bucket.hits)
            hits = session.execute(upsert).scalar_one()
            limited = limited or hits > settings.auth_attempts
    if limited:
        raise ApiError("RATE_LIMITED", "Too many attempts; try again later", status_code=429)
