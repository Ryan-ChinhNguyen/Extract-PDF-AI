"""Document endpoints: upload, history, detail, page image, re-run.

Routes stay thin on purpose -- parse, delegate to ``DocumentService``,
serialise. Errors are raised as domain exceptions and turned into responses by
the handler in ``app.main``, so nothing here catches or maps HTTP codes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import DocumentServiceDep
from app.core.exceptions import PageNotFound
from app.db.models.enums import DocumentStatus
from app.schemas.common import DuplicateDocumentResponse, ErrorResponse, Page
from app.schemas.document import DocumentDetail, DocumentSummary, RetryRequest
from app.schemas.extraction import ExtractionDetail

router = APIRouter(prefix="/documents", tags=["documents"])

NOT_FOUND = {"model": ErrorResponse, "description": "No such document or page."}
RETRY_NOT_FOUND = {
    "model": ErrorResponse,
    "description": "No such document or page, or an unknown engine_key.",
}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentSummary,
    summary="Upload a PDF",
    responses={
        202: {"description": "Accepted; OCR runs in the background."},
        409: {
            "model": DuplicateDocumentResponse,
            "description": "The same file is already uploaded and still retained.",
        },
        415: {"model": ErrorResponse, "description": "The bytes are not a PDF."},
    },
)
async def upload_document(
    service: DocumentServiceDep,
    file: Annotated[UploadFile, File(description="The PDF to run OCR on.")],
) -> DocumentSummary:
    """Accept a PDF and start processing it.

    Returns immediately with the document in `pending`: converting the PDF and
    OCR-ing every page takes one vendor call per page, which is far too long to
    hold a request open. Poll `GET /documents/{id}` for progress.

    A file that is already uploaded and still inside its retention window is
    refused with **409**, carrying the id of the existing document — re-run a
    failed page from there instead of uploading again.
    """
    data = await file.read()
    document = await service.upload(file.filename or "upload.pdf", data)
    return DocumentSummary.from_model(document)


@router.get(
    "",
    response_model=Page[DocumentSummary],
    summary="List uploaded documents",
)
async def list_documents(
    service: DocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    document_status: Annotated[
        DocumentStatus | None, Query(alias="status", description="Filter by status.")
    ] = None,
) -> Page[DocumentSummary]:
    """The history screen: newest first, with per-document page counts."""
    rows, total = await service.list_documents(
        limit=limit,
        offset=offset,
        status=document_status.value if document_status else None,
    )
    return Page(
        items=[
            DocumentSummary.from_model(doc, pages_succeeded=ok, pages_failed=failed)
            for doc, ok, failed in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="Get one document with its pages",
    responses={404: NOT_FOUND},
)
async def get_document(document_id: uuid.UUID, service: DocumentServiceDep) -> DocumentDetail:
    """One document, every page, and each page's most recent attempt.

    Earlier attempts are not inlined; fetch them per page from
    `/documents/{document_id}/pages/{page_no}/extractions`.
    """
    document = await service.get_detail(document_id)
    return DocumentDetail.from_model_with_pages(document)


@router.get(
    "/{document_id}/pages/{page_no}/image",
    summary="Download a converted page image",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "The converted JPEG."},
        404: NOT_FOUND,
    },
)
async def get_page_image(
    document_id: uuid.UUID,
    page_no: int,
    service: DocumentServiceDep,
) -> FileResponse:
    """The JPEG produced by the convert API for this page."""
    page = await service.get_page(document_id, page_no)
    storage = service.storage
    if not page.image_path or not storage.exists(page.image_path):
        raise PageNotFound(f"No image stored for page {page_no}.")
    return FileResponse(
        storage.path_for(page.image_path),
        media_type="image/jpeg",
        filename=f"{document_id}-p{page_no}.jpg",
    )


@router.get(
    "/{document_id}/pages/{page_no}/extractions",
    response_model=list[ExtractionDetail],
    summary="List every attempt for a page",
    responses={404: NOT_FOUND},
)
async def list_page_extractions(
    document_id: uuid.UUID, page_no: int, service: DocumentServiceDep
) -> list[ExtractionDetail]:
    """Full attempt history for one page, newest first.

    Retries and runs on other engines all appear here, each with the raw vendor
    payload — this is what makes two engines comparable on the same input.
    """
    attempts = await service.list_page_attempts(document_id, page_no)
    return [ExtractionDetail.from_model(a) for a in attempts]


@router.post(
    "/{document_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentSummary,
    summary="Re-run every failed page",
    responses={
        404: RETRY_NOT_FOUND,
        410: {
            "model": ErrorResponse,
            "description": "Past its retention window; upload the file again.",
        },
    },
)
async def retry_document(
    document_id: uuid.UUID, service: DocumentServiceDep, body: RetryRequest | None = None
) -> DocumentSummary:
    """Queue another attempt for each page that failed.

    Pages that already succeeded are left alone — a re-run never spends money
    on work that is already done.
    """
    await service.queue_retry(document_id, engine_key=body.engine_key if body else None)
    doc, ok, failed = await service.get_summary(document_id)
    return DocumentSummary.from_model(doc, pages_succeeded=ok, pages_failed=failed)


@router.post(
    "/{document_id}/pages/{page_no}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentSummary,
    summary="Re-run one page",
    responses={
        404: RETRY_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "A worker is running this page right now.",
        },
        410: {
            "model": ErrorResponse,
            "description": "Past its retention window; upload the file again.",
        },
    },
)
async def retry_page(
    document_id: uuid.UUID,
    page_no: int,
    service: DocumentServiceDep,
    body: RetryRequest | None = None,
) -> DocumentSummary:
    """Queue another attempt for a single page.

    The previous attempt is kept: the new run is appended with the next
    `attempt_no`, so a failure stays visible next to whatever replaced it.

    A page a worker is already running is refused with **409** rather than
    reset, because the in-flight attempt would overwrite the reset anyway. A
    page already queued is left alone and reported as accepted.
    """
    await service.queue_retry(
        document_id, page_no=page_no, engine_key=body.engine_key if body else None
    )
    doc, ok, failed = await service.get_summary(document_id)
    return DocumentSummary.from_model(doc, pages_succeeded=ok, pages_failed=failed)
