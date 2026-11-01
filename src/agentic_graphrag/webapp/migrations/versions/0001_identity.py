"""Initial independent identity schema.

Revision ID: 0001_identity
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_identity"
down_revision = None
branch_labels = None
depends_on = None


def _id() -> sa.Column[str]:
    return sa.Column("id", sa.String(36), primary_key=True)


def _time(name: str, *, nullable: bool = False) -> sa.Column[float]:
    return sa.Column(name, sa.Float(), nullable=nullable)


def _user(name: str = "user_id", *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(36), sa.ForeignKey("users.id"), nullable=nullable)


def _tenant() -> sa.Column[str]:
    return sa.Column(
        "tenant_id",
        sa.String(100),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _role(table: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("role IN ('admin', 'operator', 'reader')", name=f"{table}_role")


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("email_normalized", sa.String(254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _time("created_at"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        _user("created_by"),
        _time("created_at"),
        sa.CheckConstraint("kind IN ('personal', 'team')", name="tenant_kind"),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")),
        _tenant(),
        sa.Column("role", sa.String(20), nullable=False),
        _time("joined_at"),
        sa.PrimaryKeyConstraint("user_id", "tenant_id"),
        _role("membership"),
    )
    _sessions()
    _tenant_credentials()
    op.create_table(
        "auth_rate_limit_buckets",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window", sa.Integer(), primary_key=True),
        sa.Column("hits", sa.Integer(), nullable=False),
    )


def _sessions() -> None:
    op.create_table(
        "auth_sessions",
        _id(),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        _tenant(),
        sa.Column("version", sa.Integer(), nullable=False),
        _time("created_at"),
        _time("expires_at"),
        _time("revoked_at", nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _time("expires_at"),
        _time("used_at", nullable=True),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])
    op.create_table(
        "password_reset_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        _time("expires_at"),
        _time("used_at", nullable=True),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])


def _tenant_credentials() -> None:
    op.create_table(
        "invites",
        _id(),
        _tenant(),
        sa.Column("email_normalized", sa.String(254), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        _role("invite"),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _user("invited_by", nullable=True),
        _time("created_at"),
        _time("expires_at"),
        _time("accepted_at", nullable=True),
        _time("revoked_at", nullable=True),
    )
    op.create_index("ix_invites_tenant_id", "invites", ["tenant_id"])
    op.create_table(
        "api_keys",
        _id(),
        _tenant(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(20), nullable=False),
        _role("api_key"),
        _user("created_by", nullable=True),
        _time("created_at"),
        _time("expires_at", nullable=True),
        _time("last_used_at", nullable=True),
        _time("revoked_at", nullable=True),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])


def downgrade() -> None:
    for table in (
        "auth_rate_limit_buckets",
        "api_keys",
        "invites",
        "password_reset_tokens",
        "refresh_tokens",
        "auth_sessions",
        "memberships",
        "tenants",
        "users",
    ):
        op.drop_table(table)
