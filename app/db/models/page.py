"""One page of a PDF: the unit of work the OCR API is called for."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.models.enums import PageStatus


class Page(Base, TimestampMixin):
    """A page exists as soon as the PDF is converted, before any OCR runs.

    Kept separate from ``extractions`` because a page is a fact and an
    extraction is an event: one page can be run several times (a retry, or a
    second engine for comparison) and each run gets its own row.
    """

    __tablename__ = "pages"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based

    # Path to the converted JPEG. Images live on disk, not in the database --
    # a 3-page scan is already megabytes. NULL once the file is gone.
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default=PageStatus.PENDING)

    # Which engine the next run should use. Set when a retry asks for a
    # specific engine; NULL means "whichever engine is active". The pipeline
    # runs out of band, so the choice has to outlive the request that made it.
    requested_engine_key: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("extraction_engines.key", ondelete="RESTRICT"),
        nullable=True,
    )

    # Set when a worker takes this row. A claim older than
    # WORKER_CLAIM_TIMEOUT_SECONDS is treated as abandoned and reset, so a
    # worker that dies mid-call does not strand the row forever.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped["Document"] = relationship(back_populates="pages")  # noqa: F821
    extractions: Mapped[list["Extraction"]] = relationship(  # noqa: F821
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="Extraction.created_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_pages_document_id_page_no"),
        CheckConstraint(
            "status IN ('pending','processing','succeeded','failed')",
            name="status_valid",
        ),
        CheckConstraint("page_no >= 1", name="page_no_positive"),
    )
