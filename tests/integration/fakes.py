"""A stand-in for the FastAccounting client.

The pipeline's interesting behaviour is what it does when a page fails, so the
tests need to *choose* which pages fail. That is not something the sandbox can
be asked for, and calling it from a test suite would be slow and wasteful.
"""

from contextlib import asynccontextmanager
from typing import Any

from app.clients.fastaccounting.errors import ConvertFailed, ReceiptFailed
from app.clients.fastaccounting.schemas import ConvertResult

JPEG = b"\xff\xd8\xff\xe0 fake jpeg"


class FakeClient:
    """Records what it was asked to do and answers from a script.

    ``fail_pages`` holds 1-based page numbers that should raise, keyed off the
    filename the pipeline passes ("page-2.jpg").
    """

    def __init__(
        self,
        *,
        page_count: int = 3,
        fail_pages: set[int] | None = None,
        convert_error: str | None = None,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        self.page_count = page_count
        self.fail_pages = fail_pages or set()
        self.convert_error = convert_error
        self.receipt = receipt or {
            "date": "2017-08-11",
            "amount": "85550",
            "tel": "0352091010",
            "issuer": "Test Issuer",
            "options": {"note": "kept verbatim"},
        }
        self.convert_calls: list[str] = []
        self.receipt_calls: list[str] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def convert_to_jpg(self, pdf: bytes, filename: str = "upload.pdf") -> ConvertResult:
        self.convert_calls.append(filename)
        if self.convert_error:
            raise ConvertFailed(self.convert_error, retryable=False)
        return ConvertResult(images=[JPEG] * self.page_count)

    async def extract_receipt(self, image: bytes, filename: str = "page.jpg"):
        self.receipt_calls.append(filename)
        page_no = _page_no(filename)
        if page_no in self.fail_pages:
            raise ReceiptFailed(
                "Bad Request: unsupported file type",
                error_code=400003,
                status_code=400,
                retryable=False,
            )
        return self.receipt


def _page_no(filename: str) -> int:
    digits = "".join(c for c in filename if c.isdigit())
    return int(digits) if digits else 0


def factory(client: FakeClient):
    """Wrap an instance as the callable PipelineWorker expects."""

    @asynccontextmanager
    async def _factory():
        yield client

    return _factory
