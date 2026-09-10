"""Read model for the engine registry."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import BillingUnit


class EngineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str = Field(description="Stable slug used by extractions.engine_key.")
    provider: str
    model_name: str
    version: str
    endpoint_url: str | None = None

    unit: BillingUnit = Field(
        description="What one unit of usage is: a page for OCR, 1k tokens for an LLM engine."
    )
    unit_cost: Decimal | None = Field(
        default=None,
        description="Current price per unit, or null when the contract publishes none. "
        "Past runs keep their own snapshot and are unaffected when this changes.",
    )
    currency: str | None = None
    is_active: bool
    config: dict[str, Any] | None = None
    created_at: datetime
