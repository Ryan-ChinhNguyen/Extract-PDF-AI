"""Read models for a single OCR attempt."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.clients.fastaccounting.schemas import normalize_receipt
from app.db.models.enums import ExtractionStatus
from app.db.models.extraction import Extraction


class ExtractedFieldRead(BaseModel):
    """One extracted value and how sure the engine was of it."""

    name: str
    value: Any = None
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="The engine's own score, 0-1, or null when it reported none. "
        "A field the engine did not find carries no score rather than a zero.",
        examples=[0.946],
    )


class ExtractionRead(BaseModel):
    """One call to one engine for one page.

    The extracted values are not columns and are not fixed: ``fields`` is the
    stored payload interpreted at read time, carrying only the keys the vendor
    actually returned. Callers that need everything -- the `options` block, an
    engine-specific shape -- read ``raw_response`` from ``ExtractionDetail``.
    """

    id: uuid.UUID
    attempt_no: int = Field(description="1 for the first run, incremented by every retry.")
    engine_key: str = Field(description="Which engine produced this, e.g. 'fa_receipt_v1_5'.")
    engine_version: str | None = None
    status: ExtractionStatus

    fields: list[ExtractedFieldRead] = Field(
        default_factory=list,
        description="Extracted values in display order, each with the engine's "
        "confidence when it reported a usable one. Only fields the payload carries.",
    )

    error_code: int | None = Field(
        default=None,
        description="Vendor error code. The API reports failures in the body with HTTP 400.",
    )
    error_message: str | None = None

    latency_ms: int | None = None
    cost_amount: Decimal | None = Field(
        default=None, description="Cost priced at the engine's rate when the call was made."
    )
    currency: str | None = None
    created_at: datetime

    @classmethod
    def from_model(cls, extraction: Extraction) -> "ExtractionRead":
        return cls(**cls._common(extraction))

    @staticmethod
    def _common(extraction: Extraction) -> dict[str, Any]:
        return {
            "id": extraction.id,
            "attempt_no": extraction.attempt_no,
            "engine_key": extraction.engine_key,
            "engine_version": extraction.engine_version,
            "status": ExtractionStatus(extraction.status),
            # Failures carry no extracted values, only an error.
            "fields": (
                [
                    ExtractedFieldRead(name=f.name, value=f.value, confidence=f.confidence)
                    for f in normalize_receipt(extraction.raw_response)
                ]
                if extraction.status == ExtractionStatus.SUCCEEDED
                else []
            ),
            "error_code": extraction.error_code,
            "error_message": extraction.error_message,
            "latency_ms": extraction.latency_ms,
            "cost_amount": extraction.cost_amount,
            "currency": extraction.currency,
            "created_at": extraction.created_at,
        }


class ExtractionDetail(ExtractionRead):
    """As above, plus the untouched payload and the arithmetic behind the cost."""

    raw_response: dict[str, Any] | None = None
    usage_qty: Decimal | None = Field(
        default=None, description="Units consumed: 1 page, 1 request, or a count of 1k tokens."
    )
    unit_cost_snapshot: Decimal | None = Field(
        default=None,
        description="The engine's price per unit at the moment of the call, not its price now.",
    )

    @classmethod
    def from_model(cls, extraction: Extraction) -> "ExtractionDetail":
        return cls(
            **cls._common(extraction),
            raw_response=extraction.raw_response,
            usage_qty=extraction.usage_qty,
            unit_cost_snapshot=extraction.unit_cost_snapshot,
        )
