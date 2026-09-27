"""Deduplication and the retention window, against the real index.

The rule under test: one live document per content hash for the length of the
retention window, enforced by the partial unique index
`uq_documents_content_hash_live`.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.exceptions import DocumentNotFound, DuplicateDocument, UnsupportedFileType
from app.db.models.document import Document
from app.services.document_service import DocumentService


async def _upload(session_factory, settings, name: str, data: bytes) -> Document:
    async with session_factory() as session:
        document = await DocumentService(session, settings).upload(name, data)
        await session.commit()
        return document


async def test_uploading_the_same_file_twice_is_refused(
    session_factory, settings, unique_pdf, created_documents
):
    data = unique_pdf()
    first = await _upload(session_factory, settings, "it-a.pdf", data)
    created_documents.append(first.id)

    with pytest.raises(DuplicateDocument) as exc_info:
        await _upload(session_factory, settings, "it-a-again.pdf", data)

    # The caller is handed the existing run so it can re-run a failed page
    # there, rather than being told only that the upload was rejected.
    assert exc_info.value.existing_document_id == str(first.id)


async def test_a_different_file_is_accepted(
    session_factory, settings, unique_pdf, created_documents
):
    first = await _upload(session_factory, settings, "it-b1.pdf", unique_pdf())
    second = await _upload(session_factory, settings, "it-b2.pdf", unique_pdf())
    created_documents.extend([first.id, second.id])

    assert first.id != second.id


async def test_a_lapsed_document_releases_its_hash(
    session_factory, settings, unique_pdf, created_documents
):
    """After the TTL the same file may be uploaded again as a fresh document."""
    data = unique_pdf()
    first = await _upload(session_factory, settings, "it-c.pdf", data)
    created_documents.append(first.id)

    async with session_factory() as session:
        document = await session.get(Document, first.id)
        document.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    second = await _upload(session_factory, settings, "it-c-again.pdf", data)
    created_documents.append(second.id)

    assert second.id != first.id
    async with session_factory() as session:
        # The old row is stamped rather than deleted: the history of that run
        # stays readable, it just no longer holds the hash.
        assert (await session.get(Document, first.id)).expired_at is not None


async def test_two_simultaneous_uploads_create_one_document(
    session_factory, settings, unique_pdf, created_documents
):
    """The race the partial unique index exists for.

    Both requests find no live row and both insert. Without the index the
    result is two documents and twice the vendor spend; with it, one insert
    wins and the loser is turned into the 409 the client already handles.
    """
    data = unique_pdf()

    results = await asyncio.gather(
        _upload(session_factory, settings, "it-race-1.pdf", data),
        _upload(session_factory, settings, "it-race-2.pdf", data),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, Document)]
    losers = [r for r in results if isinstance(r, DuplicateDocument)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    created_documents.append(winners[0].id)

    async with session_factory() as session:
        rows = await session.scalars(
            select(Document).where(Document.content_hash == winners[0].content_hash)
        )
        assert len(list(rows)) == 1

    assert losers[0].existing_document_id == str(winners[0].id)


async def test_a_non_pdf_never_reaches_the_database(session_factory, settings):
    with pytest.raises(UnsupportedFileType):
        await _upload(session_factory, settings, "it-notes.txt", b"just some text")


async def test_losing_the_insert_race_reports_the_winner(
    session_factory, settings, unique_pdf, created_documents
):
    """The branch the index exists for, reached deterministically.

    The pre-insert check is made to see nothing -- as it would for a request
    that ran before the other one committed -- so the insert itself collides.
    Racing two uploads with gather() usually resolves at the check instead.
    """
    data = unique_pdf()
    first = await _upload(session_factory, settings, "it-winner.pdf", data)
    created_documents.append(first.id)

    async with session_factory() as session:
        service = DocumentService(session, settings)
        real = service.documents.get_live_by_hash
        calls = 0

        async def blind_then_real(content_hash):
            nonlocal calls
            calls += 1
            return None if calls == 1 else await real(content_hash)

        service.documents.get_live_by_hash = blind_then_real

        with pytest.raises(DuplicateDocument) as exc_info:
            await service.upload("it-loser.pdf", data)

    assert exc_info.value.existing_document_id == str(first.id)
    assert "already being processed" in exc_info.value.message
    async with session_factory() as session:
        rows = await session.scalars(
            select(Document).where(Document.content_hash == first.content_hash)
        )
        assert len(list(rows)) == 1  # the loser left nothing behind


async def test_a_summary_for_an_unknown_document_is_not_found(session_factory, settings):
    async with session_factory() as session:
        with pytest.raises(DocumentNotFound):
            await DocumentService(session, settings).get_summary(uuid.uuid4())
