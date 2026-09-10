"""One uploaded PDF."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, uuid_pk
from app.db.models.enums import DocumentStatus


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()

    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of the raw bytes. Named for its role, not its algorithm, so the
    # hash function can change without a column rename.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.PENDING
    )
    # Logging id returned by convert_to_jpg, kept to trace a run back to the
    # vendor's own logs when a support question comes up.
    convert_lid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retention window. `expires_at` is the policy (created_at + TTL);
    # `expired_at` records the moment the hash was actually released. They are
    # separate because nothing sweeps expired rows yet -- see docs/DESIGN_NOTES.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    pages: Mapped[list["Page"]] = relationship(  # noqa: F821
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Page.page_no",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','converting','extracting','completed',"
            "'partial_failed','failed')",
            name="status_valid",
        ),
        # A file may occupy the "live" slot for its hash only once. Partial
        # index, so a released (expired) row stops blocking re-upload. This is
        # also what makes two simultaneous uploads of the same file collide in
        # the database rather than both paying for the APIs.
        Index(
            "uq_documents_content_hash_live",
            "content_hash",
            unique=True,
            postgresql_where=text("expired_at IS NULL"),
        ),
        Index("ix_documents_created_at", text("created_at DESC")),
    )
