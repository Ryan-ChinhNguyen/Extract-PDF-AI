"""Errors raised by the FastAccounting client."""


class FastAccountingError(Exception):
    """A call to the vendor did not produce a usable result.

    The API reports business failures *in the body* with HTTP 400 and a
    ``{"result": "FAILED", "data": {"error_code": ..., "error_message": ...}}``
    envelope, so the HTTP status alone is not enough to classify a response --
    ``error_code`` is carried separately and stored on the extraction row.
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.status_code = status_code
        # Whether running the exact same request again could plausibly work.
        # A timeout or a 502 is worth another go; "unsupported file type" is
        # not, and retrying it only burns quota.
        self.retryable = retryable


class ConvertFailed(FastAccountingError):
    """convert_to_jpg refused or could not process the PDF."""


class ReceiptFailed(FastAccountingError):
    """The receipt OCR endpoint refused or could not process the image."""
