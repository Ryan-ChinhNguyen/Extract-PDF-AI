"""Test configuration.

Unit tests never reach the network or a database: the HTTP layer is stubbed
with respx and no session is opened. Integration tests under tests/integration
do use the real database named by DATABASE_URL -- see that package's conftest
for how they avoid touching rows they did not create.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://pdfx:pdfx@localhost:5432/pdfx")
os.environ.setdefault("FA_API_TOKEN", "test-token")
