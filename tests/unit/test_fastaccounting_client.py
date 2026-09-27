"""The client, against a stubbed transport. The sandbox is never called."""

import base64

import httpx
import pytest
import respx

from app.clients.fastaccounting.client import FastAccountingClient
from app.clients.fastaccounting.errors import ConvertFailed, ReceiptFailed
from app.core.config import Settings

JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://x/y",
        fa_api_token="test-token",
        fa_base_url="https://api.example.test/fa",
        fa_api_version="v1.5",
    )


@respx.mock
async def test_convert_decodes_every_page(settings):
    respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "SUCCESS",
                "data": {
                    "lid": "21_20180205105158.7579_8810",
                    "image": [
                        base64.b64encode(JPEG).decode(),
                        base64.b64encode(JPEG).decode(),
                    ],
                },
            },
        )
    )

    async with FastAccountingClient(settings) as client:
        result = await client.convert_to_jpg(b"%PDF-1.4 ...")

    assert result.page_count == 2
    assert result.images[0] == JPEG


@respx.mock
async def test_convert_sends_the_bearer_token(settings):
    route = respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "SUCCESS",
                "data": {"lid": "l", "image": [base64.b64encode(JPEG).decode()]},
            },
        )
    )

    async with FastAccountingClient(settings) as client:
        await client.convert_to_jpg(b"%PDF-1.4 ...")

    assert route.calls.last.request.headers["Authorization"] == "Bearer test-token"


@respx.mock
async def test_failure_is_read_from_the_body_not_the_status(settings):
    """The API answers 400 with the reason inside the payload."""
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(
            400,
            json={
                "result": "FAILED",
                "data": {
                    "error_code": 400003,
                    "error_message": "Bad Request: unsupported file type",
                },
            },
        )
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    error = exc_info.value
    assert error.error_code == 400003
    assert error.message == "Bad Request: unsupported file type"
    # A rejected file will be rejected again; retrying only burns quota.
    assert error.retryable is False


@respx.mock
async def test_receipt_returns_a_flat_payload(settings):
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(
            200,
            json={"date": "2015-09-07", "amount": "840", "tel": "0352091010", "issuer": "Kyowa"},
        )
    )

    async with FastAccountingClient(settings) as client:
        payload = await client.extract_receipt(JPEG)

    # Returned exactly as sent: nothing is parsed or dropped on the way in.
    assert payload == {
        "date": "2015-09-07",
        "amount": "840",
        "tel": "0352091010",
        "issuer": "Kyowa",
    }


@respx.mock
async def test_server_errors_are_marked_retryable(settings):
    respx.post(settings.fa_receipt_url).mock(return_value=httpx.Response(503, text="upstream down"))

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.retryable is True
    assert exc_info.value.status_code == 503


@respx.mock
async def test_timeouts_are_retryable(settings):
    respx.post(settings.fa_receipt_url).mock(side_effect=httpx.ReadTimeout("too slow"))

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.retryable is True


@respx.mock
async def test_convert_without_images_is_an_error(settings):
    """SUCCESS with an empty list would otherwise create a page-less document."""
    respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200, json={"result": "SUCCESS", "data": {"lid": "l", "image": []}}
        )
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ConvertFailed):
            await client.convert_to_jpg(b"%PDF-1.4 ...")


@respx.mock
async def test_convert_strips_the_data_uri_prefix(settings):
    """The endpoint returns data URIs, not the bare base64 the docs show."""
    respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "SUCCESS",
                "data": {
                    "lid": "l",
                    "image": ["data:image/jpg;base64," + base64.b64encode(JPEG).decode()],
                },
            },
        )
    )

    async with FastAccountingClient(settings) as client:
        result = await client.convert_to_jpg(b"%PDF-1.4 ...")

    assert result.images[0] == JPEG


@respx.mock
async def test_convert_still_accepts_bare_base64(settings):
    """Both shapes decode, in case the vendor sends the documented one."""
    respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": "SUCCESS",
                "data": {"lid": "l", "image": [base64.b64encode(JPEG).decode()]},
            },
        )
    )

    async with FastAccountingClient(settings) as client:
        result = await client.convert_to_jpg(b"%PDF-1.4 ...")

    assert result.images[0] == JPEG


@respx.mock
async def test_transport_errors_are_retryable(settings):
    """A refused connection is the vendor being down, not a bad request."""
    respx.post(settings.fa_receipt_url).mock(side_effect=httpx.ConnectError("refused"))

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.retryable is True
    assert "Transport error" in exc_info.value.message


@respx.mock
async def test_a_json_error_status_without_a_failed_body_uses_the_status(settings):
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(502, json={"message": "bad gateway"})
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.status_code == 502
    assert exc_info.value.retryable is True


@respx.mock
async def test_a_client_error_status_is_not_retryable(settings):
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(401, json={"message": "unauthorized"})
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False


@respx.mock
async def test_a_non_object_payload_is_an_error(settings):
    respx.post(settings.fa_receipt_url).mock(return_value=httpx.Response(200, json=["not", "it"]))

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed, match="Unexpected response shape"):
            await client.extract_receipt(JPEG)


@respx.mock
async def test_a_non_numeric_error_code_is_stored_as_none(settings):
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(
            400,
            json={"result": "FAILED", "data": {"error_code": "oops", "error_message": "nope"}},
        )
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.error_code is None
    assert exc_info.value.message == "nope"


@respx.mock
async def test_a_failed_body_without_a_message_still_says_something(settings):
    respx.post(settings.fa_receipt_url).mock(
        return_value=httpx.Response(400, json={"result": "FAILED"})
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ReceiptFailed) as exc_info:
            await client.extract_receipt(JPEG)

    assert exc_info.value.message == "Request failed."
    assert exc_info.value.error_code is None


@respx.mock
async def test_an_undecodable_image_fails_the_conversion(settings):
    respx.post(settings.fa_convert_url).mock(
        return_value=httpx.Response(
            200,
            json={"result": "SUCCESS", "data": {"lid": "l", "image": ["!!! not base64 !!!"]}},
        )
    )

    async with FastAccountingClient(settings) as client:
        with pytest.raises(ConvertFailed, match="undecodable"):
            await client.convert_to_jpg(b"%PDF-1.4 ...")


async def test_a_supplied_http_client_is_left_open(settings):
    """The caller owns a client it passed in; closing it would break its other users."""
    http = httpx.AsyncClient()
    try:
        async with FastAccountingClient(settings, http=http):
            pass
        assert not http.is_closed
    finally:
        await http.aclose()
