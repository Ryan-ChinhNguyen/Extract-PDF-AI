"""Database access for the engine registry."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.extraction_engine import ExtractionEngine


class EngineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> ExtractionEngine | None:
        return await self.session.get(ExtractionEngine, key)

    async def list_all(self, *, active_only: bool = False) -> list[ExtractionEngine]:
        stmt = select(ExtractionEngine).order_by(ExtractionEngine.key)
        if active_only:
            stmt = stmt.where(ExtractionEngine.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def get_active(self) -> ExtractionEngine | None:
        """The engine new work runs on.

        More than one row may be active (that is how two engines get compared);
        the lowest key wins as the default so the choice is deterministic.
        """
        stmt = (
            select(ExtractionEngine)
            .where(ExtractionEngine.is_active.is_(True))
            .order_by(ExtractionEngine.key)
            .limit(1)
        )
        return await self.session.scalar(stmt)
