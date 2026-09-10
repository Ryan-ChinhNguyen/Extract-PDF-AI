"""Test configuration.

Unit tests must not reach the FastAccounting sandbox: the HTTP layer is
stubbed with respx. Environment defaults are set here so importing
``app.core.config`` never fails on a machine without a .env file.
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://pdfx:pdfx@localhost:5432/pdfx_test"
)
os.environ.setdefault("FA_API_TOKEN", "test-token")
