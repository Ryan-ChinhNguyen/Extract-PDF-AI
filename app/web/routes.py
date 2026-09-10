"""Server-rendered screens.

Deliberately thin and template-driven: the assignment asks for the results to
be visible, not for a front-end. These routes reuse the same
``DocumentService`` the JSON API uses, so there is one implementation of the
rules and two presentations of it.

Errors are handled here rather than by the JSON exception handler, because a
browser needs a redirect and a message, not a 409 body.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import DocumentServiceDep
from app.core.exceptions import AppError
from app.db.models.enums import DocumentStatus
from app.schemas.document import DocumentDetail, DocumentSummary

TEMPLATES_DIR = __file__.rsplit("routes.py", 1)[0] + "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(include_in_schema=False)

# Statuses where the screen has nothing left to wait for.
TERMINAL = {DocumentStatus.COMPLETED, DocumentStatus.PARTIAL_FAILED, DocumentStatus.FAILED}


@router.get("/")
async def index(request: Request, service: DocumentServiceDep):
    rows, total = await service.list_documents(limit=50, offset=0)
    documents = [
        DocumentSummary.from_model(doc, pages_succeeded=ok, pages_failed=failed)
        for doc, ok, failed in rows
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "documents": documents,
            "total": total,
            # Drives the auto-refresh: only poll while something is moving.
            "in_progress": any(d.status not in TERMINAL for d in documents),
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/upload")
async def upload(
    service: DocumentServiceDep,
    file: Annotated[UploadFile, File()],
):
    data = await file.read()
    try:
        document = await service.upload(file.filename or "upload.pdf", data)
    except AppError as exc:
        existing = getattr(exc, "existing_document_id", None)
        if existing:
            # Not an error worth stopping on: send the user to the run that
            # already exists, which is where they can re-run a failed page.
            return _redirect(f"/documents/{existing}", notice=exc.message)
        return _redirect("/", error=exc.message)
    return _redirect(f"/documents/{document.id}", notice="Upload accepted. Processing...")


@router.get("/documents/{document_id}")
async def detail(request: Request, document_id: uuid.UUID, service: DocumentServiceDep):
    try:
        document = await service.get_detail(document_id)
    except AppError as exc:
        # A browser gets the history screen and a message, not the JSON body
        # the API handler would produce.
        return _redirect("/", error=exc.message)
    view = DocumentDetail.from_model_with_pages(document)
    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "document": view,
            "in_progress": view.status not in TERMINAL,
            "low_confidence": service.settings.ocr_low_confidence_threshold,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/documents/{document_id}/retry")
async def retry(
    document_id: uuid.UUID,
    service: DocumentServiceDep,
    page_no: Annotated[int | None, Form()] = None,
):
    try:
        await service.queue_retry(document_id, page_no=page_no)
    except AppError as exc:
        return _redirect(f"/documents/{document_id}", error=exc.message)
    target = "page" if page_no else "failed pages"
    return _redirect(f"/documents/{document_id}", notice=f"Queued a re-run of the {target}.")


def _redirect(path: str, *, notice: str | None = None, error: str | None = None):
    query = ""
    if notice:
        query = f"?notice={notice}"
    elif error:
        query = f"?error={error}"
    # 303, so the browser turns the POST into a GET and a refresh does not
    # resubmit the upload.
    return RedirectResponse(url=path + query, status_code=303)
