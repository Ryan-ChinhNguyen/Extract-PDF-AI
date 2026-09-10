"""Application entry point."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import AppError, DuplicateDocument
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.debug)

    settings.images_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(api_router)
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
