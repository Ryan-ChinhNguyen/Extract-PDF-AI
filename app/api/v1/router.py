"""Aggregates every v1 router under a single prefix."""

from fastapi import APIRouter

from app.api.v1 import documents, health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(documents.router)
