"""The pipeline end to end, with the vendor stubbed.

Covers what happens when some pages fail -- the case the whole retry design
exists for -- and the claim mechanics that let more than one worker run.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import NoEngineAvailable, PageBusy
from app.db.models.document import Document
from app.db.models.enums import DocumentStatus, PageStatus
from app.db.models.extraction import Extraction
from app.db.models.page import Page
from app.services.document_service import DocumentService
from app.services.extraction_service import ExtractionService
from app.workers.pipeline_worker import PipelineWorker
from tests.integration.fakes import FakeClient, factory


async def _upload(session_factory, settings, data: bytes, name: str = "it-pipe.pdf"):
    async with session_factory() as session:
        document = await DocumentService(session, settings).upload(name, data)
        await session.commit()
        return document


async def _drain(worker: PipelineWorker, limit: int = 12) -> None:
    """Tick until there is nothing left to pick up."""
    for _ in range(limit):
        if not await worker.tick():
            return


async def _load(session_factory, document_id):
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        pages = list(
            await session.scalars(
                select(Page).where(Page.document_id == document_id).order_by(Page.page_no)
            )
        )
        return document, pages


async def test_a_clean_run_completes_every_page(
    session_factory, settings, unique_pdf, created_documents
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    client = FakeClient(page_count=3)

    await _drain(PipelineWorker(session_factory, settings, factory(client)))

    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.COMPLETED
    assert document.page_count == 3
    assert [p.status for p in pages] == [PageStatus.SUCCEEDED] * 3
    assert client.convert_calls and len(client.receipt_calls) == 3
    # Claims are handed back, otherwise the reaper would keep finding them.
    assert document.claimed_at is None
    assert all(p.claimed_at is None for p in pages)


async def test_one_failing_page_does_not_sink_the_document(
    session_factory, settings, unique_pdf, created_documents
):
    """The 5%-of-pages-erroring case: the rest still deliver."""
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)

    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=3, fail_pages={2})))
    )

    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.PARTIAL_FAILED
    assert [p.status for p in pages] == [
        PageStatus.SUCCEEDED,
        PageStatus.FAILED,
        PageStatus.SUCCEEDED,
    ]

    async with session_factory() as session:
        failed = await session.scalar(select(Extraction).where(Extraction.page_id == pages[1].id))
    # The vendor's own code is kept, not just a message: it is what makes
    # "which failures are we seeing" answerable later.
    assert failed.status == "failed"
    assert failed.error_code == 400003


async def test_retrying_appends_an_attempt_and_keeps_the_failure(
    session_factory, settings, unique_pdf, created_documents
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2, fail_pages={2})))
    )

    async with session_factory() as session:
        await DocumentService(session, settings).queue_retry(document.id)
        await session.commit()

    # Second run: the vendor behaves this time.
    await _drain(PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2))))

    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.COMPLETED

    async with session_factory() as session:
        attempts = list(
            await session.scalars(
                select(Extraction)
                .where(Extraction.page_id == pages[1].id)
                .order_by(Extraction.attempt_no)
            )
        )
    assert [(a.attempt_no, a.status) for a in attempts] == [
        (1, "failed"),
        (2, "succeeded"),
    ]


async def test_a_retry_only_reruns_the_pages_that_failed(
    session_factory, settings, unique_pdf, created_documents
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=3, fail_pages={3})))
    )

    async with session_factory() as session:
        await DocumentService(session, settings).queue_retry(document.id)
        await session.commit()

    second = FakeClient(page_count=3)
    await _drain(PipelineWorker(session_factory, settings, factory(second)))

    # Pages 1 and 2 already succeeded; re-running them would be paid for twice.
    assert second.receipt_calls == ["page-3.jpg"]


async def test_a_convert_failure_can_be_recovered_without_reuploading(
    session_factory, settings, unique_pdf, created_documents
):
    """A document that dies before it has pages must still be re-runnable.

    Its content hash is held for the whole retention window, so if `retry` did
    not restart the conversion the file could not be resubmitted at all.
    """
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)

    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(convert_error="boom")))
    )
    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.FAILED
    assert pages == []

    async with session_factory() as session:
        await DocumentService(session, settings).queue_retry(document.id)
        await session.commit()

    await _drain(PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2))))

    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.COMPLETED
    assert len(pages) == 2
    assert document.error_message is None


async def test_an_abandoned_claim_is_released(
    session_factory, settings, unique_pdf, created_documents
):
    """A worker that dies mid-call must not strand the page.

    The claim is a column rather than a held lock, precisely because the vendor
    call happens outside the transaction.
    """
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    worker = PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2)))

    await worker._claim_documents()
    async with session_factory() as session:
        stale = datetime.now(UTC) - timedelta(seconds=settings.worker_claim_timeout_seconds + 60)
        claimed = await session.get(Document, document.id)
        claimed.claimed_at = stale
        await session.commit()

    await worker.tick()

    await _drain(worker)
    document, pages = await _load(session_factory, document.id)
    assert document.status == DocumentStatus.COMPLETED
    assert len(pages) == 2


async def test_two_workers_never_claim_the_same_page(
    session_factory, settings, unique_pdf, created_documents
):
    """What FOR UPDATE ... SKIP LOCKED buys: replicas are safe."""
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=4))),
        limit=1,
    )  # one tick: converts, creating 4 pending pages

    settings.worker_batch_size = 2
    a = PipelineWorker(session_factory, settings, factory(FakeClient()))
    b = PipelineWorker(session_factory, settings, factory(FakeClient()))

    claimed_a, claimed_b = await asyncio.gather(a._claim_pages(), b._claim_pages())

    assert set(claimed_a).isdisjoint(claimed_b)
    assert len(claimed_a) + len(claimed_b) <= 4


async def test_a_page_already_running_refuses_a_retry(
    session_factory, settings, unique_pdf, created_documents
):
    """Resetting an in-flight page would lose the request without a trace."""
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    worker = PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2)))
    await _drain(worker, limit=1)  # convert only: pages exist, none run yet
    await worker._claim_pages()  # now they are `processing`

    async with session_factory() as session:
        with pytest.raises(PageBusy):
            await DocumentService(session, settings).queue_retry(document.id, page_no=1)


async def test_a_page_already_queued_is_left_alone(
    session_factory, settings, unique_pdf, created_documents
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=2))), limit=1
    )

    async with session_factory() as session:
        service = DocumentService(session, settings)
        await service.queue_retry(document.id, page_no=1)  # still `pending`
        await session.commit()

    _, pages = await _load(session_factory, document.id)
    assert pages[0].status == PageStatus.PENDING


async def test_a_missing_engine_returns_the_page_to_the_queue(
    session_factory, settings, unique_pdf, created_documents
):
    """A configuration fault must not be recorded as the page's failure."""
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await _drain(
        PipelineWorker(session_factory, settings, factory(FakeClient(page_count=1))), limit=1
    )
    _, pages = await _load(session_factory, document.id)

    async with session_factory() as session:
        service = ExtractionService(session, settings, FakeClient())
        service.engines.get_active = AsyncMock(return_value=None)

        with pytest.raises(NoEngineAvailable):
            await service.extract_page(pages[0].id)
        await session.rollback()

    # No attempt was burned, and the page is untouched -- a later tick retries.
    async with session_factory() as session:
        attempts = list(
            await session.scalars(select(Extraction).where(Extraction.page_id == pages[0].id))
        )
    assert attempts == []
    _, pages = await _load(session_factory, document.id)
    assert pages[0].status == PageStatus.PENDING
