"""Initial schema: documents, pages, extraction_engines, extractions.

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending','converting','extracting','completed','partial_failed','failed')",
            name="status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    # Partial unique index: one live document per content hash. Releasing the
    # hash (setting expired_at) lets the same file be uploaded again, and two
    # concurrent uploads of the same file collide here rather than both
    # spending money on the vendor APIs.
    op.create_index(
        "uq_documents_content_hash_live",
        "documents",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("expired_at IS NULL"),
    )
    op.create_index(
        "ix_documents_created_at",
        "documents",
        [sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "extraction_engines",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("unit_cost", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "unit IN ('page','request','1k_tokens')",
            name="unit_valid",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_extraction_engines"),
    )

    op.create_table(
        "pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_engine_key", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("page_no >= 1", name="page_no_positive"),
        sa.CheckConstraint(
            "status IN ('pending','processing','succeeded','failed')",
            name="status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_pages_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_engine_key"],
            ["extraction_engines.key"],
            name="fk_pages_requested_engine_key_extraction_engines",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pages"),
        sa.UniqueConstraint("document_id", "page_no", name="uq_pages_document_id_page_no"),
    )

    op.create_table(
        "extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine_key", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage_qty", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("engine_version", sa.String(length=32), nullable=True),
        sa.Column("unit_cost_snapshot", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("cost_amount", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        sa.CheckConstraint("status IN ('succeeded','failed')", name="status_valid"),
        sa.ForeignKeyConstraint(
            ["engine_key"],
            ["extraction_engines.key"],
            name="fk_extractions_engine_key_extraction_engines",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["pages.id"],
            name="fk_extractions_page_id_pages",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extractions"),
        sa.UniqueConstraint(
            "page_id",
            "engine_key",
            "attempt_no",
            name="uq_extractions_page_engine_attempt",
        ),
    )
    op.create_index(
        "ix_extractions_page_id_created_at",
        "extractions",
        ["page_id", sa.text("created_at DESC")],
        unique=False,
    )

    # Reference data, not user data: the engine this build actually calls.
    # unit_cost stays NULL -- the sandbox contract publishes no price, and
    # inventing one would make every cost column downstream a lie.
    op.execute(
        """
        INSERT INTO extraction_engines
            (key, provider, model_name, version, endpoint_url, unit,
             unit_cost, currency, is_active, config)
        VALUES
            ('fa_receipt_v1_5', 'FastAccounting', 'receipt', 'v1.5',
             'https://api-gt01-sandbox.fastaccounting.jp/fa/v1.5/receipt',
             'page', NULL, NULL, true, '{}'::jsonb)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_extractions_page_id_created_at", table_name="extractions")
    op.drop_table("extractions")
    # pages references extraction_engines, so it goes first.
    op.drop_table("pages")
    op.drop_table("extraction_engines")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_index("uq_documents_content_hash_live", table_name="documents")
    op.drop_table("documents")
