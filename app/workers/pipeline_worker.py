"""Background worker: claims pending work and runs the pipeline.

Work is claimed from the database rather than handed over in memory. That is
what makes the two retry endpoints work at all -- they only set a row back to
``pending`` -- and it means a restart resumes instead of dropping jobs.

Claiming uses ``FOR UPDATE ... SKIP LOCKED``, so several workers (or several
API replicas with the worker enabled) never take the same row.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.fastaccounting.client import FastAccountingClient
from app.core.config import Settings
from app.db.models.document import Document
from app.db.models.enums import DocumentStatus, PageStatus
from app.db.models.page import Page
from app.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)


class PipelineWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        # Injectable so tests can drive the pipeline with a stub and force the
        # failure cases (a page erroring, a timeout) that are the whole point of
        # the retry paths. Production passes nothing and gets the real client.
        self.client_factory = client_factory or (lambda: FastAccountingClient(settings))
        self._stopping = asyncio.Event()

    # --- lifecycle -------------------------------------------------------

    async def run_forever(self) -> None:
        logger.info(
            "pipeline worker started (poll=%.1fs, batch=%d)",
            self.settings.worker_poll_interval_seconds,
            self.settings.worker_batch_size,
        )
        while not self._stopping.is_set():
            try:
                did_work = await self.tick()
            except Exception:
                # A failing tick must never end the loop: the next one may hit
                # a different row, and the alternative is silent death.
                logger.exception("pipeline tick failed")
                did_work = False

            if did_work:
                continue  # drain the queue before sleeping again
            # Sleep, but wake immediately on shutdown instead of holding the
            # process open for a full poll interval.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.worker_poll_interval_seconds,
                )
        logger.info("pipeline worker stopped")

    def stop(self) -> None:
        self._stopping.set()

    # --- one pass --------------------------------------------------------

    async def tick(self) -> bool:
        """Do one round of work. Returns whether anything was picked up."""
        await self._release_abandoned_claims()

        document_ids = await self._claim_documents()
        page_ids = await self._claim_pages()

        for document_id in document_ids:
            await self._run(lambda svc, i=document_id: svc.convert_document(i))
        for page_id in page_ids:
            await self._run(lambda svc, i=page_id: svc.extract_page(i))

        return bool(document_ids or page_ids)

    async def _run(self, action) -> None:
        """Run one pipeline step in its own session and transaction."""
        async with self.session_factory() as session:
            try:
                async with self.client_factory() as client:
                    await action(ExtractionService(session, self.settings, client))
                await session.commit()
            except Exception:
                await session.rollback()
                # The row keeps its claim; _release_abandoned_claims puts it
                # back once the claim ages out, so nothing is lost silently.
                logger.exception("pipeline step failed")

    # --- claiming --------------------------------------------------------

    async def _claim_documents(self) -> list[uuid.UUID]:
        """Take up to batch_size documents waiting to be converted."""
        async with self.session_factory() as session:
            stmt = (
                select(Document.id)
                .where(Document.status == DocumentStatus.PENDING)
                .order_by(Document.created_at)
                .limit(self.settings.worker_batch_size)
                .with_for_update(skip_locked=True)
            )
            ids = list(await session.scalars(stmt))
            if ids:
                await session.execute(
                    update(Document)
                    .where(Document.id.in_(ids))
                    .values(status=DocumentStatus.CONVERTING, claimed_at=datetime.now(UTC))
                )
            await session.commit()
            return ids

    async def _claim_pages(self) -> list[uuid.UUID]:
        """Take up to batch_size pages waiting for OCR."""
        async with self.session_factory() as session:
            stmt = (
                select(Page.id)
                .where(Page.status == PageStatus.PENDING)
                .order_by(Page.created_at)
                .limit(self.settings.worker_batch_size)
                .with_for_update(skip_locked=True)
            )
            ids = list(await session.scalars(stmt))
            if ids:
                await session.execute(
                    update(Page)
                    .where(Page.id.in_(ids))
                    .values(status=PageStatus.PROCESSING, claimed_at=datetime.now(UTC))
                )
            await session.commit()
            return ids

    async def _release_abandoned_claims(self) -> None:
        """Return rows whose worker never came back.

        The claim is held by a column, not by a lock, precisely because the
        vendor call happens outside the transaction -- so a crashed worker
        leaves a stale `claimed_at` rather than a stuck lock.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.worker_claim_timeout_seconds)
        async with self.session_factory() as session:
            pages = await session.execute(
                update(Page)
                .where(Page.status == PageStatus.PROCESSING, Page.claimed_at < cutoff)
                .values(status=PageStatus.PENDING, claimed_at=None)
            )
            documents = await session.execute(
                update(Document)
                .where(
                    Document.status == DocumentStatus.CONVERTING,
                    Document.claimed_at < cutoff,
                )
                .values(status=DocumentStatus.PENDING, claimed_at=None)
            )
            await session.commit()
            released = (pages.rowcount or 0) + (documents.rowcount or 0)
            if released:
                logger.warning("released %d abandoned claim(s)", released)
