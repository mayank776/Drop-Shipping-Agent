"""S2 foundation: audit log, approvals, knowledge chunks.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False, server_default=""),
        sa.Column("details", JSONB, nullable=False, server_default="{}"),
        sa.Column("correlation_id", sa.String(128)),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_correlation_id", "audit_log", ["correlation_id"])
    op.execute(
        """
        CREATE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only()"
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("lines", JSONB, nullable=False, server_default="[]"),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("telegram_message_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(64)),
    )
    op.create_index("ix_approvals_status", "approvals", ["status"])

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source"])
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("approvals")
    op.execute("DROP TABLE audit_log")
    op.execute("DROP FUNCTION audit_log_is_append_only()")
