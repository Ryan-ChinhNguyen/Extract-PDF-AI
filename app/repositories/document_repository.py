"""Database access for documents."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.document import Document
from app.db.models.enums import PageStatus
from app.db.models.page import Page


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self.session.get(Document, document_id)

    async def get_with_pages(self, document_id: uuid.UUID) -> Document | None:
        stmt = (
            select(Document)
            .where(Document.id == document_id)
            .options(selectinload(Document.pages).selectinload(Page.extractions))
        )
        return await self.session.scalar(stmt)

    async def get_live_by_hash(self, content_hash: str) -> Document | None:
        """The document currently holding the slot for this hash, if any."""
        stmt = select(Document).where(
            Document.content_hash == content_hash,
            Document.expired_at.is_(None),
        )
        return await self.session.scalar(stmt)

    async def release_lapsed_hash(self, content_hash: str) -> int:
        """Stamp ``expired_at`` on a document whose retention window has passed.

        This is what lets the same file be uploaded again after the TTL. It runs
        lazily on upload because no background sweeper exists yet; the images
        those rows point at stay on disk. See DESIGN_NOTES.md.
        """
        now = datetime.now(UTC)
        stmt = (
            update(Document)
            .where(
                Document.content_hash == content_hash,
                Document.expired_at.is_(None),
                Document.expires_at <= now,
            )
            .values(expired_at=now)
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def add(self, document: Document) -> Document:
        self.session.add(document)
        # Flush rather than commit: the caller needs the unique-index violation
        # to surface here so it can be turned into a 409.
        await self.session.flush()
        return document

    def _summary_stmt(self) -> Select:
        """Documents with per-status page counts, in one pass."""
        return (
            select(
                Document,
                func.count(Page.id)
                .filter(Page.status == PageStatus.SUCCEEDED)
                .label("pages_succeeded"),
                func.count(Page.id).filter(Page.status == PageStatus.FAILED).label("pages_failed"),
            )
            .outerjoin(Page, Page.document_id == Document.id)
            .group_by(Document.id)
        )

    async def list_summaries(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[tuple[Document, int, int]], int]:
        stmt = self._summary_stmt().order_by(Document.created_at.desc())
        count_stmt = select(func.count()).select_from(Document)
        if status is not None:
            stmt = stmt.where(Document.status == status)
            count_stmt = count_stmt.where(Document.status == status)

        rows = (await self.session.execute(stmt.limit(limit).offset(offset))).all()
        total = await self.session.scalar(count_stmt) or 0
        return [(row[0], row[1], row[2]) for row in rows], total

    async def get_summary(self, document_id: uuid.UUID) -> tuple[Document, int, int] | None:
        stmt = self._summary_stmt().where(Document.id == document_id)
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1], row[2]) if row else None
