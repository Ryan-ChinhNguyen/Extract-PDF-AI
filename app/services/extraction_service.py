"""The OCR pipeline: convert a PDF, then OCR each page.

Both steps are written to be called on a single claimed row, from a worker,
inside its own short transaction. Nothing here polls or schedules -- that is
``app.workers.pipeline_worker`` -- and nothing here talks HTTP directly, which
is what lets the pipeline be tested against a stubbed client.

The vendor call happens *outside* any open transaction: a receipt call takes
seconds, and holding a row lock for that long would serialise the whole system.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fastaccounting.client import FastAccountingClient
from app.clients.fastaccounting.errors import FastAccountingError
from app.core.config import Settings
from app.core.exceptions import NoEngineAvailable
from app.db.models.document import Document
from app.db.models.enums import BillingUnit, DocumentStatus, ExtractionStatus, PageStatus
from app.db.models.extraction import Extraction
from app.db.models.extraction_engine import ExtractionEngine
from app.db.models.page import Page
from app.repositories.document_repository import DocumentRepository
from app.repositories.engine_repository import EngineRepository
from app.repositories.extraction_repository import ExtractionRepository
from app.repositories.page_repository import PageRepository
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        client: FastAccountingClient,
    ) -> None:
        self.session = session
        self.settings = settings
        self.client = client
        self.documents = DocumentRepository(session)
        self.engines = EngineRepository(session)
        self.pages = PageRepository(session)
        self.extractions = ExtractionRepository(session)
        self.storage = FileStorage(settings)

    # --- step 1: PDF -> page images -------------------------------------

    async def convert_document(self, document_id: uuid.UUID) -> None:
        """Convert one claimed document and create its page rows.

        The document is already in ``converting``; this ends it in
        ``extracting`` (pages waiting for OCR) or ``failed``.
        """
        document = await self.documents.get(document_id)
        if document is None:  # pragma: no cover - claimed row cannot vanish
            logger.warning("document %s disappeared before convert", document_id)
            return
        if not document.source_path:
            await self._fail_document(document, "The uploaded file is missing on disk.")
            return

        try:
            pdf = self.storage.read(document.source_path)
        except OSError as exc:
            await self._fail_document(document, f"Could not read the stored PDF: {exc}")
            return

        try:
            result = await self.client.convert_to_jpg(pdf, document.original_filename)
        except FastAccountingError as exc:
            # A conversion failure kills the whole document: without images
            # there is nothing to OCR. Retryable or not, it is recorded the
            # same way -- the user re-uploads, which is cheap at this stage.
            await self._fail_document(document, exc.message)
            return

        for page_no, image in enumerate(result.images, start=1):
            path = self.storage.save_page_image(document.id, page_no, image)
            self.session.add(
                Page(
                    id=uuid.uuid4(),
                    document_id=document.id,
                    page_no=page_no,
                    image_path=path,
                    status=PageStatus.PENDING,
                )
            )

        document.page_count = result.page_count
        document.status = DocumentStatus.EXTRACTING
        document.claimed_at = None
        await self.session.flush()
        logger.info("converted document %s into %d pages", document.id, result.page_count)

    # --- step 2: page image -> extraction --------------------------------

    async def extract_page(self, page_id: uuid.UUID) -> None:
        """OCR one claimed page and append the attempt."""
        page = await self.session.get(Page, page_id)
        if page is None:  # pragma: no cover - claimed row cannot vanish
            logger.warning("page %s disappeared before extract", page_id)
            return

        engine = await self._engine_for(page)
        if engine is None:
            # Not this page's fault: raising rolls the step back, the claim
            # ages out, and the page is picked up again once an engine
            # exists -- rather than recording a failed attempt against it.
            raise NoEngineAvailable("No extraction engine is registered; cannot process pages.")

        if not page.image_path or not self.storage.exists(page.image_path):
            await self._record_failure(
                page, engine=engine, message="The page image is missing on disk."
            )
            return

        image = self.storage.read(page.image_path)
        started = datetime.now(UTC)
        try:
            payload = await self.client.extract_receipt(image, f"page-{page.page_no}.jpg")
        except FastAccountingError as exc:
            await self._record_failure(
                page,
                engine=engine,
                message=exc.message,
                error_code=exc.error_code,
                latency_ms=_elapsed_ms(started),
                raw={"result": "FAILED", "data": {"error_message": exc.message}},
            )
            return

        await self._record_success(
            page, engine=engine, payload=payload, latency_ms=_elapsed_ms(started)
        )

    # --- persistence helpers ---------------------------------------------

    async def _engine_for(self, page: Page) -> ExtractionEngine | None:
        if page.requested_engine_key:
            engine = await self.engines.get(page.requested_engine_key)
            if engine is not None:
                return engine
            logger.warning(
                "page %s requested unknown engine %s; falling back to active",
                page.id,
                page.requested_engine_key,
            )
        return await self.engines.get_active()

    async def _record_success(
        self,
        page: Page,
        *,
        engine: ExtractionEngine,
        payload: dict,
        latency_ms: int,
    ) -> None:
        extraction = await self._new_extraction(page, engine, latency_ms)
        extraction.status = ExtractionStatus.SUCCEEDED
        # Stored as received. Which fields a receipt yields is the vendor's
        # answer, not a shape this schema gets to assume.
        extraction.raw_response = payload
        await self.extractions.add(extraction)

        page.status = PageStatus.SUCCEEDED
        await self._finish_page(page)

    async def _record_failure(
        self,
        page: Page,
        *,
        engine: ExtractionEngine,
        message: str,
        error_code: int | None = None,
        latency_ms: int | None = None,
        raw: dict | None = None,
    ) -> None:
        extraction = await self._new_extraction(page, engine, latency_ms)
        extraction.status = ExtractionStatus.FAILED
        extraction.error_code = error_code
        extraction.error_message = message
        extraction.raw_response = raw
        await self.extractions.add(extraction)

        page.status = PageStatus.FAILED
        await self._finish_page(page)

    async def _new_extraction(
        self, page: Page, engine: ExtractionEngine, latency_ms: int | None
    ) -> Extraction:
        attempt_no = await self.extractions.next_attempt_no(page.id, engine.key)
        # One page, one call: both of these bill a single unit. A token-priced
        # engine would report its own usage and is left to do so.
        one_unit = engine.unit in (BillingUnit.PAGE, BillingUnit.REQUEST)
        usage_qty = Decimal(1) if one_unit else None
        cost = (
            engine.unit_cost * usage_qty
            if engine.unit_cost is not None and usage_qty is not None
            else None
        )
        return Extraction(
            id=uuid.uuid4(),
            page_id=page.id,
            engine_key=engine.key,
            attempt_no=attempt_no,
            latency_ms=latency_ms,
            usage_qty=usage_qty,
            # Snapshotted, not joined: repricing the engine later must not
            # rewrite what this run cost.
            engine_version=engine.version,
            unit_cost_snapshot=engine.unit_cost,
            currency=engine.currency,
            cost_amount=cost,
        )

    async def _finish_page(self, page: Page) -> None:
        page.claimed_at = None
        page.requested_engine_key = None
        await self.session.flush()
        await self._refresh_document_status(page.document_id)

    async def _refresh_document_status(self, document_id: uuid.UUID) -> None:
        """Derive the document's status from its pages.

        Kept derived rather than incremented so that a retry, a crash, or two
        workers finishing at once cannot leave a counter out of step with the
        rows it is supposed to describe.
        """
        document = await self.documents.get(document_id)
        if document is None:  # pragma: no cover
            return
        statuses = await self.pages.list_statuses(document_id)
        if not statuses or any(s in (PageStatus.PENDING, PageStatus.PROCESSING) for s in statuses):
            return  # still work to do; leave it in `extracting`

        if all(s == PageStatus.SUCCEEDED for s in statuses):
            document.status = DocumentStatus.COMPLETED
        elif all(s == PageStatus.FAILED for s in statuses):
            document.status = DocumentStatus.FAILED
        else:
            document.status = DocumentStatus.PARTIAL_FAILED
        await self.session.flush()

    async def _fail_document(self, document: Document, message: str) -> None:
        document.status = DocumentStatus.FAILED
        document.error_message = message
        document.claimed_at = None
        await self.session.flush()
        logger.warning("document %s failed: %s", document.id, message)


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)
