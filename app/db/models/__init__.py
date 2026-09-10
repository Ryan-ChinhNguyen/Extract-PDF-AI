"""Model package.

Every model is imported here so that ``Base.metadata`` is complete by the time
Alembic (or a test fixture) reads it.
"""

from app.db.base import Base
from app.db.models.document import Document
from app.db.models.enums import (
    BillingUnit,
    DocumentStatus,
    ExtractionStatus,
    PageStatus,
)
from app.db.models.extraction import Extraction
from app.db.models.extraction_engine import ExtractionEngine
from app.db.models.page import Page

__all__ = [
    "Base",
    "BillingUnit",
    "Document",
    "DocumentStatus",
    "Extraction",
    "ExtractionEngine",
    "ExtractionStatus",
    "Page",
    "PageStatus",
]
