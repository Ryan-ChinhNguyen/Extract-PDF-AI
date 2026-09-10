"""Read models for an uploaded PDF."""

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.document import Document as DocumentModel
from app.db.models.enums import DocumentStatus
from app.schemas.page import PageRead


class DocumentSummary(BaseModel):
    """One row of the history screen."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    size_bytes: int
    page_count: int | None = Field(
        default=None, description="Known once the PDF has been converted."
    )
    status: DocumentStatus
    pages_succeeded: int = 0
    pages_failed: int = 0
    error_message: str | None = None

    created_at: datetime
    expires_at: datetime = Field(
        description="End of the retention window. Past this the file can be uploaded again, "
        "and re-running its pages is no longer allowed."
    )
    is_expired: bool = False

    @classmethod
    def from_model(
        cls, doc: DocumentModel, *, pages_succeeded: int = 0, pages_failed: int = 0
    ) -> "DocumentSummary":
        return cls(
            id=doc.id,
            original_filename=doc.original_filename,
            size_bytes=doc.size_bytes,
            page_count=doc.page_count,
            status=DocumentStatus(doc.status),
            pages_succeeded=pages_succeeded,
            pages_failed=pages_failed,
            error_message=doc.error_message,
            created_at=doc.created_at,
            expires_at=doc.expires_at,
            is_expired=doc.expires_at <= datetime.now(UTC),
        )


class DocumentDetail(DocumentSummary):
    """History row plus every page and its latest result."""

    pages: list[PageRead] = []

    @classmethod
    def from_model_with_pages(cls, doc: DocumentModel) -> "DocumentDetail":
        pages = [PageRead.from_model(p) for p in doc.pages]
        summary = DocumentSummary.from_model(
            doc,
            pages_succeeded=sum(1 for p in pages if p.status == "succeeded"),
            pages_failed=sum(1 for p in pages if p.status == "failed"),
        )
        return cls(**summary.model_dump(), pages=pages)


class RetryRequest(BaseModel):
    """Optional body for a re-run.

    Passing a different ``engine_key`` is how the same page gets compared
    across engines: the old attempt is kept, the new one is appended.
    """

    engine_key: str | None = Field(
        default=None,
        description="Engine to run with. Defaults to the active engine in extraction_engines.",
        examples=["fa_receipt_v1_5"],
    )
