"""Database access for pages."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.enums import PageStatus
from app.db.models.page import Page


class PageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_number(self, document_id: uuid.UUID, page_no: int) -> Page | None:
        stmt = (
            select(Page)
            .where(Page.document_id == document_id, Page.page_no == page_no)
            .options(selectinload(Page.extractions))
        )
        return await self.session.scalar(stmt)

    async def list_failed(self, document_id: uuid.UUID) -> list[Page]:
        stmt = (
            select(Page)
            .where(Page.document_id == document_id, Page.status == PageStatus.FAILED)
            .order_by(Page.page_no)
        )
        return list(await self.session.scalars(stmt))

    async def list_statuses(self, document_id: uuid.UUID) -> list[str]:
        """Just the statuses, for deriving the document's own.

        Deliberately not `get_with_pages`: that eager-loads every extraction of
        every page, and this runs each time a page finishes.
        """
        stmt = select(Page.status).where(Page.document_id == document_id)
        return list(await self.session.scalars(stmt))

    async def set_status(self, page: Page, status: PageStatus) -> Page:
        page.status = status
        await self.session.flush()
        return page
