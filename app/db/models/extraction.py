"""One call to an extraction engine for one page -- success or failure."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk


class Extraction(Base, TimestampMixin):
    """Append-only: a retry adds a row, it never overwrites the failed one.

    Failed attempts are kept deliberately -- they are the raw material for the
    "5% of pages are erroring" question, and for comparing engines.
    """

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    engine_key: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("extraction_engines.key", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    # The extracted result, verbatim, and the only place it lives. Kept even
    # on failure.
    #
    # No field is promoted to its own column on purpose: what a receipt
    # yields is not knowable in advance. The API returns `options`
    # (confidences, positions, amount_detail, ...) only for contracts that
    # enable them, and a different engine would return a different shape
    # entirely. Columns would have to be guessed now and migrated later.
    #
    # The cost: filtering or sorting on a field means an expression over
    # JSONB, e.g. (raw_response->>'amount')::numeric, and an index to match
    # if it ever gets hot. Nothing queries these values today.
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # The API signals failure in the body (HTTP 400 + {"result":"FAILED"}),
    # so the vendor error code is worth its own column.
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cost snapshot: copied from extraction_engines at call time so that
    # repricing an engine never rewrites what past runs cost.
    usage_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)

    page: Mapped["Page"] = relationship(back_populates="extractions")  # noqa: F821

    __table_args__ = (
        UniqueConstraint(
            "page_id", "engine_key", "attempt_no", name="uq_extractions_page_engine_attempt"
        ),
        CheckConstraint("status IN ('succeeded','failed')", name="status_valid"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        # Serves "latest attempt for this page" on the detail screen.
        Index("ix_extractions_page_id_created_at", "page_id", text("created_at DESC")),
    )
