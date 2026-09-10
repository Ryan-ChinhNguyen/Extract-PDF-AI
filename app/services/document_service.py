"""Upload, retention and read paths for documents.

This module owns the rules that were decided up front:

* one live document per content hash, for the length of the retention window;
* re-uploading a retained file is refused -- the user re-runs it from the run
  that already exists;
* once the window lapses the hash is released and the file may be uploaded
  again as a fresh document.

Running the OCR pipeline is not done here: this service records the intent
(rows move to ``pending``) and ``ExtractionService`` performs the vendor calls.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    DocumentExpired,
    DocumentNotFound,
    DuplicateDocument,
    EngineNotFound,
    PageBusy,
    PageNotFound,
    UnsupportedFileType,
)
from app.db.models.document import Document
from app.db.models.enums import DocumentStatus, PageStatus
from app.db.models.page import Page
from app.repositories.document_repository import DocumentRepository
from app.repositories.engine_repository import EngineRepository
from app.repositories.extraction_repository import ExtractionRepository
from app.repositories.page_repository import PageRepository
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class DocumentService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.documents = DocumentRepository(session)
        self.pages = PageRepository(session)
        self.extractions = ExtractionRepository(session)
        self.engines = EngineRepository(session)
        self.storage = FileStorage(settings)

    # --- write path -----------------------------------------------------

    async def upload(self, filename: str, data: bytes) -> Document:
        """Accept a new PDF, or refuse it as a duplicate.

        Raises:
            UnsupportedFileType: the bytes are not a PDF.
            DuplicateDocument: an identical file is still inside its TTL.
        """
        if not data.startswith(PDF_MAGIC):
            # Checked on content, not on the filename or the browser-supplied
            # content type, both of which the client controls.
            raise UnsupportedFileType("Only PDF files are accepted.")

        content_hash = hashlib.sha256(data).hexdigest()

        # Give back the slot first if the previous holder has lapsed.
        released = await self.documents.release_lapsed_hash(content_hash)
        if released:
            logger.info("released lapsed hash %s (%d row)", content_hash[:12], released)

        existing = await self.documents.get_live_by_hash(content_hash)
        if existing is not None:
            raise DuplicateDocument(
                "This file was already uploaded and is still retained. "
                "Open the existing document to view or re-run it.",
                str(existing.id),
            )

        document = Document(
            id=uuid.uuid4(),
            original_filename=filename,
            content_hash=content_hash,
            size_bytes=len(data),
            status=DocumentStatus.PENDING,
            expires_at=datetime.now(UTC) + timedelta(days=self.settings.document_ttl_days),
        )

        try:
            # A savepoint, so losing the race does not poison the outer
            # transaction and the 409 can still be built from a live session.
            async with self.session.begin_nested():
                await self.documents.add(document)
        except IntegrityError:
            # Two identical uploads arrived together; the partial unique index
            # picked a winner. The loser reports the winner.
            winner = await self.documents.get_live_by_hash(content_hash)
            if winner is None:  # pragma: no cover - only on a concurrent release
                raise
            raise DuplicateDocument(
                "This file is already being processed.", str(winner.id)
            ) from None

        # Written after the insert, so the common failure -- losing the unique
        # index race -- leaves no file behind. A rollback after this point
        # still can, which is accepted: a stray PDF is inert, whereas a row
        # pointing at a file that was never written is not.
        document.source_path = self.storage.save_pdf(document.id, data)
        await self.session.flush()
        return document

    async def queue_retry(
        self,
        document_id: uuid.UUID,
        page_no: int | None = None,
        engine_key: str | None = None,
    ) -> Document:
        """Mark failed pages for another run.

        ``page_no`` targets one page; omitting it re-runs every failed page of
        the document. Nothing is overwritten -- the pipeline appends a new
        attempt and the failed one stays readable.

        ``engine_key`` pins the next run to a specific engine, which is how the
        same page gets compared across engines. It is recorded on the page
        because the pipeline runs after this request has returned.
        """
        document = await self._require_live(document_id)

        if engine_key is not None and await self.engines.get(engine_key) is None:
            raise EngineNotFound(f"No engine registered under key {engine_key!r}.")

        if page_no is None:
            targets = await self.pages.list_failed(document_id)
            if not targets:
                # No pages to re-run. Either everything already succeeded, or
                # the document never got past converting -- and the second case
                # must be recoverable here, because the content hash is held for
                # the whole retention window and blocks a fresh upload. Without
                # this the document would be a dead end until its TTL lapsed.
                if document.status == DocumentStatus.FAILED:
                    document.error_message = None
                    document.status = DocumentStatus.PENDING
                    document.claimed_at = None
                    await self.session.flush()
                return document
        else:
            page = await self.pages.get_by_number(document_id, page_no)
            if page is None:
                raise PageNotFound(f"Document has no page {page_no}.")
            if page.status == PageStatus.PROCESSING:
                # Resetting it would be silently pointless: the in-flight
                # attempt finishes and writes the status anyway, and this
                # request would leave no trace.
                raise PageBusy(
                    f"Page {page_no} is being processed right now. "
                    "Wait for the current attempt to finish."
                )
            if page.status == PageStatus.PENDING:
                return document  # a run is already queued for it
            targets = [page]

        for page in targets:
            page.requested_engine_key = engine_key
            await self.pages.set_status(page, PageStatus.PENDING)

        document.status = DocumentStatus.EXTRACTING
        await self.session.flush()
        return document

    # --- read path ------------------------------------------------------

    async def list_documents(
        self, *, limit: int, offset: int, status: str | None = None
    ) -> tuple[list[tuple[Document, int, int]], int]:
        return await self.documents.list_summaries(limit=limit, offset=offset, status=status)

    async def get_summary(self, document_id: uuid.UUID) -> tuple[Document, int, int]:
        row = await self.documents.get_summary(document_id)
        if row is None:
            raise DocumentNotFound(f"No document with id {document_id}.")
        return row

    async def get_detail(self, document_id: uuid.UUID) -> Document:
        document = await self.documents.get_with_pages(document_id)
        if document is None:
            raise DocumentNotFound(f"No document with id {document_id}.")
        return document

    async def get_page(self, document_id: uuid.UUID, page_no: int) -> Page:
        page = await self.pages.get_by_number(document_id, page_no)
        if page is None:
            # Distinguish a missing document from a missing page: the caller
            # gets a different message for a typo'd id than for page 99 of 3.
            if await self.documents.get(document_id) is None:
                raise DocumentNotFound(f"No document with id {document_id}.")
            raise PageNotFound(f"Document has no page {page_no}.")
        return page

    async def list_page_attempts(self, document_id: uuid.UUID, page_no: int):
        page = await self.get_page(document_id, page_no)
        return await self.extractions.list_for_page(page.id)

    # --- helpers --------------------------------------------------------

    async def _require_live(self, document_id: uuid.UUID) -> Document:
        document = await self.documents.get(document_id)
        if document is None:
            raise DocumentNotFound(f"No document with id {document_id}.")
        if document.expires_at <= datetime.now(UTC):
            # Gated on the retention date, never on whether the image file
            # happens to still be on disk -- otherwise the answer would depend
            # on what a cleanup job did or did not get to.
            raise DocumentExpired(
                "This document is past its retention window. Upload the file again."
            )
        return document
