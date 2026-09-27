"""Fixtures for tests that need a real PostgreSQL.

These run against the same database the application uses (`DATABASE_URL`),
because the behaviour under test only exists in PostgreSQL: the partial unique
index on `documents.content_hash`, `FOR UPDATE ... SKIP LOCKED`, and JSONB.

**Nothing here truncates a table.** Every test registers the documents it
creates and only those rows are removed afterwards (pages and extractions
follow by cascade). Running the suite against a database that holds real data
therefore leaves that data alone.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models.document import Document

TEST_FILENAME_PREFIX = "it-"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Real database, throwaway storage directory.

    Page images written by a test land in tmp_path, so a test run never adds
    files to the application's storage tree.
    """
    return Settings(storage_dir=tmp_path, fa_api_token="integration-test")


@pytest.fixture
async def engine(settings: Settings):
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    missing = {"documents", "pages", "extraction_engines", "extractions"} - tables
    if missing:
        pytest.fail(f"Schema is missing {sorted(missing)}. Run: alembic upgrade head")
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def created_documents(session_factory) -> AsyncIterator[list[uuid.UUID]]:
    """Ids to delete when the test ends. Append to it, or use `track`."""
    ids: list[uuid.UUID] = []
    yield ids
    if ids:
        async with session_factory() as session:
            await session.execute(delete(Document).where(Document.id.in_(ids)))
            await session.commit()


@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def engine_key(session_factory) -> str:
    """The engine seeded by migration 0001, which every test attributes to."""
    async with session_factory() as session:
        key = await session.scalar(
            text("SELECT key FROM extraction_engines WHERE is_active ORDER BY key LIMIT 1")
        )
    if key is None:
        pytest.fail("No active engine registered. Run: alembic upgrade head")
    return str(key)


@pytest.fixture
async def api_client(session_factory, settings) -> AsyncIterator[AsyncClient]:
    """The real application, in-process, on this test's own database engine.

    The app's module-level engine belongs to whichever event loop first used
    it, so the session and settings dependencies are replaced with ones built
    from the fixtures above. The lifespan is not run, which keeps the
    background worker off: tests drive the pipeline themselves.
    """
    from app.core.config import get_settings
    from app.db.session import get_session
    from app.main import create_app

    async def _session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    # Redirects are asserted on, so they are not followed.
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def upload_via_api(
    api_client: AsyncClient, unique_pdf, created_documents
) -> Callable[..., Awaitable[dict]]:
    """Upload a fresh PDF through the endpoint and return the response body."""

    async def _upload(data: bytes | None = None, name: str = "it-api.pdf") -> dict:
        response = await api_client.post(
            "/api/v1/documents",
            files={"file": (name, data or unique_pdf(), "application/pdf")},
        )
        assert response.status_code == 202, response.text
        body = response.json()
        created_documents.append(uuid.UUID(body["id"]))
        return body

    return _upload


@pytest.fixture
def run_pipeline(session_factory, settings) -> Callable[..., Awaitable[None]]:
    """Drive the worker until it has nothing left to pick up."""
    from app.workers.pipeline_worker import PipelineWorker
    from tests.integration.fakes import factory

    async def _run(client, limit: int = 12) -> None:
        worker = PipelineWorker(session_factory, settings, factory(client))
        for _ in range(limit):
            if not await worker.tick():
                return

    return _run


@pytest.fixture
def unique_pdf() -> Callable[[], bytes]:
    """Bytes that are a valid PDF header and unique per call.

    Uniqueness matters: the content hash is the deduplication key, so two tests
    using identical bytes would collide with each other.
    """

    def _make() -> bytes:
        return b"%PDF-1.4 integration test " + uuid.uuid4().hex.encode()

    return _make
