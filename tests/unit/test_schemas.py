"""Read models, built from in-memory rows: no database involved."""

import uuid
from datetime import UTC, datetime, timedelta

from app.db.models.document import Document
from app.db.models.enums import DocumentStatus
from app.db.models.extraction import Extraction
from app.db.models.page import Page
from app.schemas.document import DocumentDetail, DocumentSummary
from app.schemas.extraction import ExtractionDetail, ExtractionRead
from app.schemas.page import PageRead

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _extraction(status: str, raw: dict | None, **extra) -> Extraction:
    return Extraction(
        id=uuid.uuid4(),
        page_id=uuid.uuid4(),
        engine_key="engine-a",
        attempt_no=1,
        status=status,
        raw_response=raw,
        created_at=NOW,
        **extra,
    )


def _document(**extra) -> Document:
    values = {
        "id": uuid.uuid4(),
        "original_filename": "a.pdf",
        "size_bytes": 10,
        "status": "completed",
        "created_at": NOW,
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }
    return Document(**{**values, **extra})


def _page(document: Document, page_no: int, status: str, image_path: str | None) -> Page:
    page = Page(
        id=uuid.uuid4(),
        document_id=document.id,
        page_no=page_no,
        image_path=image_path,
        status=status,
    )
    page.extractions = []
    return page


def test_a_successful_extraction_exposes_its_fields():
    read = ExtractionRead.from_model(
        _extraction("succeeded", {"date": "2017-08-11", "amount": "85550", "issuer": "Shop"})
    )

    assert {f.name for f in read.fields} == {"date", "amount", "issuer"}


def test_a_failed_extraction_exposes_an_error_and_no_fields():
    """Even if the stored payload happens to carry values, a failure has none."""
    read = ExtractionRead.from_model(
        _extraction(
            "failed",
            {"date": "2017-08-11"},
            error_code=400003,
            error_message="unsupported file type",
        )
    )

    assert read.fields == []
    assert (read.error_code, read.error_message) == (400003, "unsupported file type")


def test_the_detail_view_keeps_the_raw_payload_and_the_cost_arithmetic():
    detail = ExtractionDetail.from_model(
        _extraction("succeeded", {"issuer": "Shop"}, usage_qty=1, unit_cost_snapshot=2)
    )

    assert detail.raw_response == {"issuer": "Shop"}
    assert (detail.usage_qty, detail.unit_cost_snapshot) == (1, 2)


def test_a_document_is_expired_once_its_window_has_passed():
    live = DocumentSummary.from_model(_document())
    lapsed = DocumentSummary.from_model(
        _document(expires_at=datetime.now(UTC) - timedelta(hours=1))
    )

    assert live.is_expired is False
    assert lapsed.is_expired is True


def test_the_summary_carries_the_page_counts_it_is_given():
    summary = DocumentSummary.from_model(_document(), pages_succeeded=2, pages_failed=1)

    assert (summary.pages_succeeded, summary.pages_failed) == (2, 1)
    assert summary.status is DocumentStatus.COMPLETED


def test_a_page_without_an_image_has_no_image_url():
    read = PageRead.from_model(_page(_document(), 1, "pending", image_path=None))

    assert read.image_url is None
    assert read.latest_extraction is None
    assert read.attempt_count == 0


def test_a_page_reports_its_newest_attempt_first():
    doc = _document()
    page = _page(doc, 2, "succeeded", image_path="x")
    newest = _extraction("succeeded", {"issuer": "Shop"})
    newest.attempt_no = 2
    oldest = _extraction("failed", None, error_message="boom")
    page.extractions = [newest, oldest]  # the relationship orders newest first

    read = PageRead.from_model(page)

    assert read.attempt_count == 2
    assert read.latest_extraction.attempt_no == 2
    assert read.image_url == f"/api/v1/documents/{doc.id}/pages/2/image"


def test_document_detail_counts_pages_by_status():
    doc = _document()
    doc.pages = [_page(doc, 1, "succeeded", "a"), _page(doc, 2, "failed", "b")]

    detail = DocumentDetail.from_model_with_pages(doc)

    assert [p.page_no for p in detail.pages] == [1, 2]
    assert (detail.pages_succeeded, detail.pages_failed) == (1, 1)
