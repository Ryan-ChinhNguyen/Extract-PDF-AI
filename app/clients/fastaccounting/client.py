"""HTTP client for the FastAccounting convert and receipt APIs.

Only the synchronous endpoints are used. The async ones exist and would allow
submitting work without holding a connection, but they cap the queue at 200
jobs and add a second polling loop on top of the one this application already
runs -- not worth the extra moving part at this size.
"""

import base64
import binascii
import logging
import re
from typing import Any

import httpx

from app.clients.fastaccounting.errors import (
    ConvertFailed,
    FastAccountingError,
    ReceiptFailed,
)
from app.clients.fastaccounting.schemas import ConvertResult
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Statuses where the same request may succeed later: the vendor is briefly
# unavailable rather than rejecting what we sent.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

# "data:image/jpg;base64," and friends, as returned by convert_to_jpg.
DATA_URI_PREFIX = re.compile(r"^data:[^;,]*;base64,")


class FastAccountingClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "FastAccountingClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.settings.fa_timeout_seconds)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:  # pragma: no cover - misuse guard
            raise RuntimeError("Use FastAccountingClient as an async context manager.")
        return self._http

    # --- endpoints ------------------------------------------------------

    async def convert_to_jpg(self, pdf: bytes, filename: str = "upload.pdf") -> ConvertResult:
        """Convert a PDF into one JPEG per page."""
        payload = await self._post(
            self.settings.fa_convert_url,
            files={"file": (filename, pdf, "application/pdf")},
            error_cls=ConvertFailed,
        )
        data = payload.get("data") or {}
        encoded = data.get("image") or []
        if not encoded:
            raise ConvertFailed("Convert returned no images.", retryable=False)
        return ConvertResult(images=[_decode(i) for i in encoded])

    async def extract_receipt(self, image: bytes, filename: str = "page.jpg") -> dict[str, Any]:
        """OCR one receipt image, returning the payload untouched.

        Nothing is parsed here: the result is stored as the vendor sent it,
        and interpreted at read time. Success is a flat object
        ({"date": ..., "amount": ...}), not the result/data envelope the
        convert endpoint uses.
        """
        return await self._post(
            self.settings.fa_receipt_url,
            files={"file": (filename, image, "image/jpeg")},
            error_cls=ReceiptFailed,
        )

    # --- plumbing -------------------------------------------------------

    async def _post(
        self,
        url: str,
        *,
        files: dict[str, Any],
        error_cls: type[FastAccountingError],
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.settings.fa_api_token}"}
        try:
            response = await self.http.post(url, files=files, headers=headers)
        except httpx.TimeoutException as exc:
            raise error_cls(f"Timed out calling {url}.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise error_cls(f"Transport error calling {url}: {exc}", retryable=True) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise error_cls(
                f"Non-JSON response from {url} (HTTP {response.status_code}).",
                status_code=response.status_code,
                retryable=response.status_code in RETRYABLE_STATUS,
            ) from exc

        if isinstance(payload, dict) and payload.get("result") == "FAILED":
            # A refusal, reported in the body. The status code is usually 400
            # regardless of what went wrong, so the code below comes from the
            # payload -- that is the part worth storing and counting.
            detail = payload.get("data") or {}
            raise error_cls(
                str(detail.get("error_message") or "Request failed."),
                error_code=_as_int(detail.get("error_code")),
                status_code=response.status_code,
                retryable=False,
            )

        if response.is_error:
            raise error_cls(
                f"HTTP {response.status_code} from {url}.",
                status_code=response.status_code,
                retryable=response.status_code in RETRYABLE_STATUS,
            )

        if not isinstance(payload, dict):
            raise error_cls(f"Unexpected response shape from {url}.", retryable=False)
        return payload


def _decode(encoded: str) -> bytes:
    """Decode one image from the convert response.

    The endpoint returns a data URI ("data:image/jpg;base64,/9j/4AAQ..."), not
    the bare base64 the API reference's `/* base64 */` placeholder suggests.
    The prefix is stripped when present so both shapes work, in case the vendor
    ever sends the documented form.
    """
    payload = DATA_URI_PREFIX.sub("", encoded.strip(), count=1)
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConvertFailed("Convert returned an undecodable image.", retryable=False) from exc


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
