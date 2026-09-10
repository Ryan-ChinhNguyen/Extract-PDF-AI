"""Application entry point and OpenAPI metadata."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError, DuplicateDocument
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.web.routes import router as web_router
from app.workers.pipeline_worker import PipelineWorker

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"

DESCRIPTION = """
OCR pipeline for multi-page PDF receipts, built on the FastAccounting sandbox APIs.

**How a PDF becomes rows**

1. `POST /api/v1/documents` stores the file and returns `202` with the document in `pending`.
2. The PDF is converted to one JPEG per page, and each page is sent to the receipt OCR API.
3. Each call — success or failure — is appended to `extractions`; a page's status follows
   its most recent attempt.
4. `GET /api/v1/documents` is the history screen; `GET /api/v1/documents/{id}` shows each
   page and its latest result.

**Two behaviours worth knowing before calling this**

* *A retained file cannot be uploaded twice.* Uploading a file that is already stored and
  still inside its retention window returns `409` with `existing_document_id`. Re-run the
  failed pages of that document instead.
* *Retries append, never overwrite.* Re-running a page adds an attempt; the failed one stays
  readable, which is also how the same page gets compared across engines.
"""

TAGS_METADATA = [
    {
        "name": "documents",
        "description": "Upload PDFs, browse the OCR history, re-run pages that failed.",
    },
    {
        "name": "engines",
        "description": "The registry of engines results can come from, with billing unit "
        "and current price. Extractions snapshot these values at call time.",
    },
    {"name": "health", "description": "Liveness and database reachability."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the pipeline worker alongside the API.

    One process serves both by default, which is all this needs. Setting
    WORKER_ENABLED=false gives an API-only process, so the same image can be
    deployed as several web replicas plus a smaller number of workers -- the
    claim query is what makes that safe.
    """
    settings = get_settings()
    worker: PipelineWorker | None = None
    task: asyncio.Task[None] | None = None

    if settings.worker_enabled:
        worker = PipelineWorker(SessionFactory, settings)
        task = asyncio.create_task(worker.run_forever())
    else:
        logger.info("pipeline worker disabled by configuration")

    try:
        yield
    finally:
        if worker is not None and task is not None:
            worker.stop()
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.debug)

    settings.images_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        summary="Multi-page PDF receipt OCR, with per-page status and retry.",
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    register_exception_handlers(app)
    return app


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        body: dict[str, object] = {"code": exc.code, "message": exc.message}
        if isinstance(exc, DuplicateDocument):
            # The caller is meant to open the existing run rather than retry.
            body["existing_document_id"] = exc.existing_document_id
        return JSONResponse(status_code=exc.status_code, content=body)


app = create_app()
