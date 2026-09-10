"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.services.document_service import DocumentService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def document_service(session: SessionDep, settings: SettingsDep) -> DocumentService:
    return DocumentService(session, settings)


DocumentServiceDep = Annotated[DocumentService, Depends(document_service)]
