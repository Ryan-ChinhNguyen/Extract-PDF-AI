"""Envelopes and error bodies shared across endpoints."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Offset pagination. Keyset paging would scale better, but the history
    screen is browsed, not crawled, so offset keeps the query readable."""

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring limit/offset.")
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Body returned for every handled error (see app/core/exceptions.py)."""

    code: str = Field(description="Stable machine-readable error code.")
    message: str

    model_config = {
        "json_schema_extra": {
            "example": {"code": "document_not_found", "message": "No document with that id."}
        }
    }


class DuplicateDocumentResponse(ErrorResponse):
    """409 body: the file is already uploaded and still inside its TTL window.

    The client is meant to open ``existing_document_id`` rather than retry the
    upload -- a failed page is re-run from there.
    """

    existing_document_id: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "code": "duplicate_document",
                "message": "This file was already uploaded and is still retained.",
                "existing_document_id": "8f14e45f-ceea-4e2b-9a1b-2b7c9a1f0d33",
            }
        }
    }
