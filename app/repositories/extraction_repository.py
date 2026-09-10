"""Database access for extraction attempts."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.extraction import Extraction


class ExtractionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_page(self, page_id: uuid.UUID) -> list[Extraction]:
        """Every attempt, newest first -- retries and other engines included."""
        stmt = (
            select(Extraction)
            .where(Extraction.page_id == page_id)
            .order_by(Extraction.created_at.desc())
        )
        return list(await self.session.scalars(stmt))

    async def next_attempt_no(self, page_id: uuid.UUID, engine_key: str) -> int:
        """Attempt numbers are per (page, engine) so a second engine starts at 1."""
        stmt = select(func.coalesce(func.max(Extraction.attempt_no), 0)).where(
            Extraction.page_id == page_id,
            Extraction.engine_key == engine_key,
        )
        return (await self.session.scalar(stmt) or 0) + 1

    async def add(self, extraction: Extraction) -> Extraction:
        self.session.add(extraction)
        await self.session.flush()
        return extraction
