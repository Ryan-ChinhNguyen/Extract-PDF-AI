"""One page of a PDF: the unit of work the OCR API is called for."""

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
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
    image_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PageStatus.PENDING
    )

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
