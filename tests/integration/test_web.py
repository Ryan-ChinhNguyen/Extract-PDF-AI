"""The server-rendered screens.

Redirects are the contract here: a browser is sent somewhere with a message,
never shown a JSON error body.
"""

import uuid
from urllib.parse import parse_qs, urlparse

from tests.integration.fakes import FakeClient

MISSING = "00000000-0000-4000-8000-000000000000"


def _query(response) -> dict[str, str]:
    location = urlparse(response.headers["location"])
    return {key: values[0] for key, values in parse_qs(location.query).items()}


def _path(response) -> str:
    return urlparse(response.headers["location"]).path


async def _upload(api_client, data: bytes, name: str = "it-web.pdf"):
    return await api_client.post("/upload", files={"file": (name, data, "application/pdf")})


async def test_the_history_screen_renders(api_client, upload_via_api):
    await upload_via_api(name="it-listed.pdf")

    response = await api_client.get("/")

    assert response.status_code == 200
    assert "it-listed.pdf" in response.text
    assert "Upload a PDF" in response.text


async def test_the_history_screen_shows_the_notice_and_error_messages(api_client):
    response = await api_client.get("/", params={"notice": "All good", "error": "Not so good"})

    assert response.status_code == 200
    assert "All good" in response.text
    assert "Not so good" in response.text


async def test_the_detail_screen_polls_only_while_work_is_moving(
    api_client, upload_via_api, run_pipeline
):
    """The page reloads itself until the document reaches a terminal status."""
    body = await upload_via_api(name="it-moving.pdf")
    moving = await api_client.get(f"/documents/{body['id']}")
    await run_pipeline(FakeClient(page_count=1))
    settled = await api_client.get(f"/documents/{body['id']}")

    assert "window.location.reload" in moving.text
    assert "window.location.reload" not in settled.text


async def test_upload_redirects_to_the_new_document(api_client, unique_pdf, created_documents):
    response = await _upload(api_client, unique_pdf())

    assert response.status_code == 303
    document_id = _path(response).removeprefix("/documents/")
    created_documents.append(uuid.UUID(document_id))
    assert _query(response)["notice"] == "Upload accepted. Processing..."


async def test_upload_of_a_duplicate_redirects_to_the_existing_document(
    api_client, upload_via_api, unique_pdf
):
    data = unique_pdf()
    first = await upload_via_api(data)

    response = await _upload(api_client, data)

    assert response.status_code == 303
    assert _path(response) == f"/documents/{first['id']}"
    assert "already uploaded" in _query(response)["notice"]


async def test_upload_of_a_non_pdf_redirects_home_with_an_error(api_client):
    response = await api_client.post(
        "/upload", files={"file": ("it-notes.txt", b"just some text", "text/plain")}
    )

    assert response.status_code == 303
    assert _path(response) == "/"
    assert _query(response) == {"error": "Only PDF files are accepted."}


async def test_the_detail_screen_renders_pages_and_the_rerun_button(
    api_client, upload_via_api, run_pipeline
):
    body = await upload_via_api(name="it-detail.pdf")
    await run_pipeline(FakeClient(page_count=2, fail_pages={2}))

    response = await api_client.get(f"/documents/{body['id']}")

    assert response.status_code == 200
    assert "it-detail.pdf" in response.text
    assert "Page 1" in response.text and "Page 2" in response.text
    assert "Re-run the failed pages" in response.text


async def test_the_detail_screen_for_an_unknown_document_redirects_home(api_client):
    response = await api_client.get(f"/documents/{MISSING}")

    assert response.status_code == 303
    assert _path(response) == "/"
    assert "No document" in _query(response)["error"]


async def test_retry_redirects_back_with_a_notice(api_client, upload_via_api, run_pipeline):
    body = await upload_via_api()
    await run_pipeline(FakeClient(page_count=2, fail_pages={1}))

    whole = await api_client.post(f"/documents/{body['id']}/retry")
    assert whole.status_code == 303
    assert _path(whole) == f"/documents/{body['id']}"
    assert _query(whole)["notice"] == "Queued a re-run of the failed pages."

    await run_pipeline(FakeClient(page_count=2, fail_pages={1}))
    single = await api_client.post(f"/documents/{body['id']}/retry", data={"page_no": "1"})
    assert single.status_code == 303
    assert _query(single)["notice"] == "Queued a re-run of the page."


async def test_retry_of_an_unknown_document_redirects_with_an_error(api_client):
    response = await api_client.post(f"/documents/{MISSING}/retry")

    assert response.status_code == 303
    assert _path(response) == f"/documents/{MISSING}"
    assert "No document" in _query(response)["error"]
