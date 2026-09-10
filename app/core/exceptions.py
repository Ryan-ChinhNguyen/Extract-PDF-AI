"""Domain-level errors.

These are raised by the service layer and translated into HTTP responses by
handlers registered in ``app.main``. Keeping them out of the service layer
means services never import ``fastapi``.
"""


class AppError(Exception):
    """Base class for errors this application raises on purpose."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DocumentNotFound(AppError):
    status_code = 404
    code = "document_not_found"


class PageNotFound(AppError):
    status_code = 404
    code = "page_not_found"


class DuplicateDocument(AppError):
    """Same content hash already uploaded and still inside its TTL window.

    Carries the existing document id so the caller can redirect the user to
    the run that already exists instead of paying for the APIs twice.
    """

    status_code = 409
    code = "duplicate_document"

    def __init__(self, message: str, existing_document_id: str) -> None:
        super().__init__(message)
        self.existing_document_id = existing_document_id


class DocumentExpired(AppError):
    """Re-run was requested on a document past its retention window."""

    status_code = 410
    code = "document_expired"


class UnsupportedFileType(AppError):
    status_code = 415
    code = "unsupported_file_type"


class EngineNotFound(AppError):
    """A retry asked for an engine that is not in the registry."""

    status_code = 404
    code = "engine_not_found"


class PageBusy(AppError):
    """A re-run was asked for a page a worker is currently running.

    Silently resetting it would be worse than refusing: the in-flight attempt
    would finish and overwrite the status, and the request would vanish without
    a trace.
    """

    status_code = 409
    code = "page_busy"


class NoEngineAvailable(AppError):
    """No engine is registered, so nothing can process a page.

    A configuration problem, not a property of the page -- raised rather than
    recorded as a failed attempt, so the page returns to the queue instead of
    burning an attempt on a fault it did not cause.
    """

    status_code = 503
    code = "no_engine_available"
