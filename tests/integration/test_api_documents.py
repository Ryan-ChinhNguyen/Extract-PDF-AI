"""The document endpoints, over HTTP, against the real database.

The pipeline is driven with the stubbed vendor client, so what is asserted here
is the contract a caller sees: status codes, error codes and response shapes.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models.document import Document
from app.db.models.page import Page
from tests.integration.fakes import JPEG, FakeClient

API = "/api/v1/documents"
MISSING = "00000000-0000-4000-8000-000000000000"


async def _set(session_factory, model, row_id, **values) -> None:
    async with session_factory() as session:
        row = await session.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()


async def _page_ids(session_factory, document_id: str) -> list[uuid.UUID]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(Page.id)
                .where(Page.document_id == uuid.UUID(document_id))
                .order_by(Page.page_no)
            )
        )


# --- upload ---------------------------------------------------------------


async def test_upload_is_accepted_and_starts_pending(upload_via_api):
    body = await upload_via_api(name="it-first.pdf")

    assert body["status"] == "pending"
    assert body["original_filename"] == "it-first.pdf"
    assert body["page_count"] is None
    assert body["is_expired"] is False


async def test_upload_of_a_non_pdf_is_415(api_client):
    response = await api_client.post(
        API, files={"file": ("it-notes.txt", b"just some text", "text/plain")}
    )

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_file_type"


async def test_upload_of_a_duplicate_is_409_and_names_the_existing_document(
    api_client, upload_via_api, unique_pdf
):
    data = unique_pdf()
    first = await upload_via_api(data)

    response = await api_client.post(API, files={"file": ("it-again.pdf", data, "application/pdf")})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "duplicate_document"
    assert body["existing_document_id"] == first["id"]


async def test_upload_without_a_file_is_422(api_client):
    response = await api_client.post(API)

    assert response.status_code == 422


# --- list -----------------------------------------------------------------


async def test_list_is_newest_first(api_client, upload_via_api):
    await upload_via_api(name="it-older.pdf")
    newer = await upload_via_api(name="it-newer.pdf")

    response = await api_client.get(API, params={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [newer["id"]]
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert body["total"] >= 2


async def test_list_offset_moves_past_the_newest(api_client, upload_via_api):
    older = await upload_via_api(name="it-older.pdf")
    await upload_via_api(name="it-newer.pdf")

    response = await api_client.get(API, params={"limit": 1, "offset": 1})

    assert [item["id"] for item in response.json()["items"]] == [older["id"]]


async def test_list_can_be_filtered_by_status(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))

    response = await api_client.get(API, params={"status": "completed", "limit": 100})

    assert response.status_code == 200
    items = response.json()["items"]
    assert items and {item["status"] for item in items} == {"completed"}
    assert response.json()["total"] >= 1
    # The upload above may sit beyond the first page on a busy database, so
    # its own row is checked directly.
    detail = await api_client.get(f"{API}/{body['id']}")
    assert detail.json()["status"] == "completed"


async def test_list_counts_succeeded_and_failed_pages(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=3, fail_pages={2}))

    response = await api_client.get(API, params={"limit": 1})

    item = response.json()["items"][0]
    assert item["id"] == body["id"]
    assert (item["pages_succeeded"], item["pages_failed"]) == (2, 1)
    assert item["status"] == "partial_failed"


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"status": "not-a-status"}],
)
async def test_list_rejects_bad_query_parameters(api_client, params):
    response = await api_client.get(API, params=params)

    assert response.status_code == 422


# --- detail ---------------------------------------------------------------


async def test_detail_shows_every_page_and_its_latest_attempt(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=3, fail_pages={2}))

    response = await api_client.get(f"{API}/{body['id']}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["page_count"] == 3
    assert [p["page_no"] for p in detail["pages"]] == [1, 2, 3]
    assert [p["status"] for p in detail["pages"]] == ["succeeded", "failed", "succeeded"]

    ok, failed = detail["pages"][0], detail["pages"][1]
    assert ok["image_url"] == f"{API}/{body['id']}/pages/1/image"
    assert ok["attempt_count"] == 1
    assert {f["name"] for f in ok["latest_extraction"]["fields"]} >= {"date", "amount"}
    # A failed attempt carries an error, and no extracted values.
    assert failed["latest_extraction"]["fields"] == []
    assert failed["latest_extraction"]["error_code"] == 400003
    assert failed["latest_extraction"]["status"] == "failed"


async def test_detail_of_a_pending_document_has_no_pages(api_client, upload_via_api):
    body = await upload_via_api()

    detail = (await api_client.get(f"{API}/{body['id']}")).json()

    assert detail["pages"] == []


async def test_detail_of_an_unknown_document_is_404(api_client):
    response = await api_client.get(f"{API}/{MISSING}")

    assert response.status_code == 404
    assert response.json()["code"] == "document_not_found"


async def test_detail_with_a_malformed_id_is_422(api_client):
    response = await api_client.get(f"{API}/not-a-uuid")

    assert response.status_code == 422


async def test_an_expired_document_is_flagged_as_expired(
    api_client, upload_via_api, session_factory
):
    body = await upload_via_api()
    await _set(
        session_factory,
        Document,
        uuid.UUID(body["id"]),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    detail = (await api_client.get(f"{API}/{body['id']}")).json()

    assert detail["is_expired"] is True


# --- page image -----------------------------------------------------------


async def test_page_image_is_served_as_jpeg(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))

    response = await api_client.get(f"{API}/{body['id']}/pages/1/image")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG


async def test_page_image_that_is_gone_from_disk_is_404(
    api_client, upload_via_api, run_pipeline, settings
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))
    for image in (settings.storage_dir / "images").rglob("*.jpg"):
        image.unlink()

    response = await api_client.get(f"{API}/{body['id']}/pages/1/image")

    assert response.status_code == 404
    assert response.json()["code"] == "page_not_found"


async def test_page_image_without_a_stored_path_is_404(
    api_client, upload_via_api, run_pipeline, session_factory
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))
    (page_id,) = await _page_ids(session_factory, body["id"])
    await _set(session_factory, Page, page_id, image_path=None)

    response = await api_client.get(f"{API}/{body['id']}/pages/1/image")

    assert response.status_code == 404


async def test_page_image_distinguishes_a_missing_document_from_a_missing_page(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))

    no_page = await api_client.get(f"{API}/{body['id']}/pages/99/image")
    no_document = await api_client.get(f"{API}/{MISSING}/pages/1/image")

    assert no_page.status_code == 404
    assert no_page.json()["code"] == "page_not_found"
    assert no_document.status_code == 404
    assert no_document.json()["code"] == "document_not_found"


# --- page attempts --------------------------------------------------------


async def test_page_attempts_are_listed_newest_first(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=2, fail_pages={2}))
    retry = await api_client.post(f"{API}/{body['id']}/retry")
    assert retry.status_code == 202
    await run_pipeline(FakeClient(page_count=2))

    response = await api_client.get(f"{API}/{body['id']}/pages/2/extractions")

    assert response.status_code == 200
    attempts = response.json()
    assert [(a["attempt_no"], a["status"]) for a in attempts] == [(2, "succeeded"), (1, "failed")]
    # The detail view carries what the summary view leaves out.
    assert attempts[0]["raw_response"]["issuer"] == "Test Issuer"
    assert attempts[0]["usage_qty"] is not None
    assert attempts[1]["error_code"] == 400003


async def test_page_attempts_for_unknown_page_and_document_are_404(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))

    no_page = await api_client.get(f"{API}/{body['id']}/pages/99/extractions")
    no_document = await api_client.get(f"{API}/{MISSING}/pages/1/extractions")

    assert no_page.json()["code"] == "page_not_found"
    assert no_document.json()["code"] == "document_not_found"


# --- retry ----------------------------------------------------------------


async def test_retry_document_requeues_only_the_failed_pages(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=3, fail_pages={3}))

    response = await api_client.post(f"{API}/{body['id']}/retry")

    assert response.status_code == 202
    summary = response.json()
    assert summary["status"] == "extracting"
    assert (summary["pages_succeeded"], summary["pages_failed"]) == (2, 0)

    rerun = FakeClient(page_count=3)
    await run_pipeline(rerun)
    assert rerun.receipt_calls == ["page-3.jpg"]


async def test_retry_document_with_nothing_failed_changes_nothing(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=2))

    response = await api_client.post(f"{API}/{body['id']}/retry")

    assert response.status_code == 202
    assert response.json()["status"] == "completed"


async def test_retry_page_accepts_a_known_engine(
    api_client, upload_via_api, run_pipeline, engine_key
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=2, fail_pages={1}))

    response = await api_client.post(
        f"{API}/{body['id']}/pages/1/retry", json={"engine_key": engine_key}
    )

    assert response.status_code == 202
    await run_pipeline(FakeClient(page_count=2))
    attempts = (await api_client.get(f"{API}/{body['id']}/pages/1/extractions")).json()
    assert [a["engine_key"] for a in attempts] == [engine_key, engine_key]
    assert [a["status"] for a in attempts] == ["succeeded", "failed"]


async def test_retry_with_an_unknown_engine_is_404(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1, fail_pages={1}))

    for path in (f"{API}/{body['id']}/retry", f"{API}/{body['id']}/pages/1/retry"):
        response = await api_client.post(path, json={"engine_key": "no-such-engine"})

        assert response.status_code == 404, path
        assert response.json()["code"] == "engine_not_found"


async def test_retry_of_an_unknown_document_or_page_is_404(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1))

    no_document = await api_client.post(f"{API}/{MISSING}/retry")
    no_document_page = await api_client.post(f"{API}/{MISSING}/pages/1/retry")
    no_page = await api_client.post(f"{API}/{body['id']}/pages/99/retry")

    assert no_document.json()["code"] == "document_not_found"
    assert no_document_page.json()["code"] == "document_not_found"
    assert no_page.json()["code"] == "page_not_found"


async def test_retry_past_the_retention_window_is_410(
    api_client, upload_via_api, run_pipeline, session_factory
):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=1, fail_pages={1}))
    await _set(
        session_factory,
        Document,
        uuid.UUID(body["id"]),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    for path in (f"{API}/{body['id']}/retry", f"{API}/{body['id']}/pages/1/retry"):
        response = await api_client.post(path)

        assert response.status_code == 410, path
        assert response.json()["code"] == "document_expired"


async def test_retry_of_a_page_a_worker_is_running_is_409(
    api_client, upload_via_api, run_pipeline, session_factory, settings
):
    from app.workers.pipeline_worker import PipelineWorker
    from tests.integration.fakes import factory

    body = await upload_via_api()
    worker = PipelineWorker(session_factory, settings, factory(FakeClient(page_count=1)))
    await worker.tick()  # converts: the page exists and is pending
    await worker._claim_pages()  # now it is processing

    response = await api_client.post(f"{API}/{body['id']}/pages/1/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "page_busy"


async def test_retry_of_a_page_already_queued_is_accepted(
    api_client, upload_via_api, run_pipeline, session_factory, settings
):
    from app.workers.pipeline_worker import PipelineWorker
    from tests.integration.fakes import factory

    body = await upload_via_api()
    await PipelineWorker(session_factory, settings, factory(FakeClient(page_count=1))).tick()

    response = await api_client.post(f"{API}/{body['id']}/pages/1/retry")

    assert response.status_code == 202
    assert response.json()["status"] == "extracting"
