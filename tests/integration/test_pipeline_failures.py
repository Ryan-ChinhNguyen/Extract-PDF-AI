"""Ways the pipeline can fail that are not the vendor's doing.

`test_pipeline.py` covers the vendor refusing a page. These are the other
causes: a file that has gone missing, an engine that is not registered, and a
worker step that blows up.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models.document import Document
from app.db.models.enums import DocumentStatus, PageStatus
from app.db.models.extraction import Extraction
from app.db.models.page import Page
from app.services.document_service import DocumentService
from app.services.extraction_service import ExtractionService
from app.workers.pipeline_worker import PipelineWorker
from tests.integration.fakes import FakeClient, factory


async def _upload(session_factory, settings, data: bytes):
    async with session_factory() as session:
        document = await DocumentService(session, settings).upload("it-fail.pdf", data)
        await session.commit()
        return document


async def _document(session_factory, document_id) -> Document:
    async with session_factory() as session:
        return await session.get(Document, document_id)


async def _pages(session_factory, document_id) -> list[Page]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(Page).where(Page.document_id == document_id).order_by(Page.page_no)
            )
        )


async def test_a_document_with_no_stored_file_fails_cleanly(
    session_factory, settings, unique_pdf, created_documents, run_pipeline
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    async with session_factory() as session:
        (await session.get(Document, document.id)).source_path = None
        await session.commit()

    client = FakeClient()
    await run_pipeline(client)

    failed = await _document(session_factory, document.id)
    assert failed.status == DocumentStatus.FAILED
    assert "missing on disk" in failed.error_message
    assert failed.claimed_at is None
    # Nothing to convert, so nothing was sent to the vendor.
    assert client.convert_calls == []


async def test_a_stored_pdf_that_cannot_be_read_fails_the_document(
    session_factory, settings, unique_pdf, created_documents, run_pipeline
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    (settings.storage_dir / document.source_path).unlink()

    client = FakeClient()
    await run_pipeline(client)

    failed = await _document(session_factory, document.id)
    assert failed.status == DocumentStatus.FAILED
    assert "Could not read the stored PDF" in failed.error_message
    assert client.convert_calls == []


async def test_a_missing_page_image_is_recorded_as_a_failed_attempt(
    session_factory, settings, unique_pdf, created_documents, run_pipeline
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await run_pipeline(FakeClient(page_count=2), limit=1)  # convert only
    pages = await _pages(session_factory, document.id)
    (settings.storage_dir / pages[0].image_path).unlink()

    client = FakeClient(page_count=2)
    await run_pipeline(client)

    pages = await _pages(session_factory, document.id)
    assert [p.status for p in pages] == [PageStatus.FAILED, PageStatus.SUCCEEDED]
    # The vendor is not asked to OCR an image that does not exist.
    assert client.receipt_calls == ["page-2.jpg"]
    async with session_factory() as session:
        attempt = await session.scalar(select(Extraction).where(Extraction.page_id == pages[0].id))
    assert attempt.status == "failed"
    assert "missing on disk" in attempt.error_message
    assert (await _document(session_factory, document.id)).status == DocumentStatus.PARTIAL_FAILED


async def test_a_document_whose_every_page_fails_is_failed(
    session_factory, settings, unique_pdf, created_documents, run_pipeline
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)

    await run_pipeline(FakeClient(page_count=2, fail_pages={1, 2}))

    finished = await _document(session_factory, document.id)
    assert finished.status == DocumentStatus.FAILED
    assert [p.status for p in await _pages(session_factory, document.id)] == [PageStatus.FAILED] * 2


async def test_a_requested_engine_is_used_and_then_forgotten(
    session_factory, settings, unique_pdf, created_documents, run_pipeline, engine_key
):
    document = await _upload(session_factory, settings, unique_pdf())
    created_documents.append(document.id)
    await run_pipeline(FakeClient(page_count=1, fail_pages={1}))

    async with session_factory() as session:
        await DocumentService(session, settings).queue_retry(
            document.id, page_no=1, engine_key=engine_key
        )
        await session.commit()
    (page,) = await _pages(session_factory, document.id)
    assert page.requested_engine_key == engine_key

    await run_pipeline(FakeClient(page_count=1))

    (page,) = await _pages(session_factory, document.id)
    assert page.status == PageStatus.SUCCEEDED
    # A one-off choice: the next run goes back to the active engine.
    assert page.requested_engine_key is None
    async with session_factory() as session:
        attempts = list(
            await session.scalars(select(Extraction).where(Extraction.page_id == page.id))
        )
    assert {a.engine_key for a in attempts} == {engine_key}


async def test_an_unknown_requested_engine_falls_back_to_the_active_one(
    session_factory, settings, engine_key
):
    async with session_factory() as session:
        service = ExtractionService(session, settings, FakeClient())
        ghost_page = SimpleNamespace(id="p", requested_engine_key="no-such-engine")

        engine = await service._engine_for(ghost_page)

    assert engine is not None
    assert engine.key == engine_key


async def test_a_failing_step_is_rolled_back_and_does_not_stop_the_worker(
    session_factory, settings, caplog
):
    worker = PipelineWorker(session_factory, settings, factory(FakeClient()))

    async def explode(_service) -> None:
        raise RuntimeError("step blew up")

    await worker._run(explode)  # must not raise

    assert "pipeline step failed" in caplog.text


async def test_a_failing_client_factory_is_contained(session_factory, settings, caplog):
    def broken_factory():
        raise RuntimeError("cannot build a client")

    worker = PipelineWorker(session_factory, settings, broken_factory)

    async def never_called(_service) -> None:  # pragma: no cover
        pytest.fail("the step should not run without a client")

    await worker._run(never_called)

    assert "pipeline step failed" in caplog.text
