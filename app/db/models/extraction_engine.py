"""Registry of the engines that can produce an extraction."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.enums import BillingUnit


class ExtractionEngine(Base):
    """One row per (provider, model, version) that results can come from.

    The point of this table is comparability: when the OCR backend is swapped,
    old results stay attributable to the engine that produced them, and cost /
    latency can be compared across engines on the same page.

    Note that price and version here are *current* values. Extractions snapshot
    them at call time, so changing a price does not rewrite historic cost.
    """

    __tablename__ = "extraction_engines"

    # Slug primary key ("fa_receipt_v1_5") rather than a surrogate id: it keeps
    # raw SQL over the extractions table readable during an incident.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    unit: Mapped[str] = mapped_column(String(16), nullable=False, default=BillingUnit.PAGE)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Engine-specific knobs (requested OCR options, model params, ...).
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("unit IN ('page','request','1k_tokens')", name="unit_valid"),
    )
