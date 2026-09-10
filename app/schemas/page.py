"""Read models for a page of a document."""

import uuid

from pydantic import BaseModel, Field

from app.db.models.enums import PageStatus
from app.db.models.page import Page as PageModel
from app.schemas.extraction import ExtractionRead


class PageRead(BaseModel):
    id: uuid.UUID
    page_no: int = Field(description="1-based, matching the page order in the PDF.")
    status: PageStatus
    image_url: str | None = Field(
        default=None, description="Where to fetch the converted JPEG for this page."
    )
    latest_extraction: ExtractionRead | None = Field(
        default=None,
        description="Most recent attempt. Earlier attempts stay available at "
        "/documents/{document_id}/pages/{page_no}/extractions.",
    )
    attempt_count: int = 0

    @classmethod
    def from_model(cls, page: PageModel) -> "PageRead":
        # `page.extractions` is ordered newest-first by the relationship.
        attempts = list(page.extractions)
        return cls(
            id=page.id,
            page_no=page.page_no,
            status=PageStatus(page.status),
            image_url=(
                f"/api/v1/documents/{page.document_id}/pages/{page.page_no}/image"
                if page.image_path
                else None
            ),
            latest_extraction=(ExtractionRead.from_model(attempts[0]) if attempts else None),
            attempt_count=len(attempts),
        )
