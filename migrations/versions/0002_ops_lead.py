"""S3 Ops Lead: approval queue columns, alerts, daily plans.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("approvals", sa.Column("urgent", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("approvals", sa.Column("released_at", sa.DateTime(timezone=True)))

    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("dedupe_key", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alerts_severity", "alerts", ["severity"])
    op.create_index(
        "ix_alerts_undelivered", "alerts", ["created_at"], postgresql_where=sa.text("delivered_at IS NULL")
    )

    op.create_table(
        "daily_plans",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("items", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_plans")
    op.drop_table("alerts")
    op.drop_column("approvals", "released_at")
    op.drop_column("approvals", "urgent")
