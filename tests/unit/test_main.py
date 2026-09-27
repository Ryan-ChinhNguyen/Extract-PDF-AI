"""Application wiring: lifespan and error handling, without a database."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import main
from app.core.exceptions import AppError, DuplicateDocument, PageBusy


class RecordingWorker:
    """Stands in for PipelineWorker: runs until told to stop."""

    instances: list["RecordingWorker"] = []

    def __init__(self, *_: object) -> None:
        self.stopped = asyncio.Event()
        self.started = False
        RecordingWorker.instances.append(self)

    async def run_forever(self) -> None:
        self.started = True
        await self.stopped.wait()

    def stop(self) -> None:
        self.stopped.set()


@pytest.fixture(autouse=True)
def _fresh_instances():
    RecordingWorker.instances = []


async def test_the_lifespan_runs_the_worker_and_stops_it_on_shutdown(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(worker_enabled=True))
    monkeypatch.setattr(main, "PipelineWorker", RecordingWorker)

    async with main.lifespan(FastAPI()):
        await asyncio.sleep(0)  # let the background task start
        (worker,) = RecordingWorker.instances
        assert worker.started
        assert not worker.stopped.is_set()

    assert worker.stopped.is_set()


async def test_the_lifespan_starts_no_worker_when_it_is_disabled(monkeypatch, caplog):
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(worker_enabled=False))
    monkeypatch.setattr(main, "PipelineWorker", RecordingWorker)

    async with main.lifespan(FastAPI()):
        pass

    assert RecordingWorker.instances == []
    assert "worker disabled" in caplog.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PageBusy("busy"), 409, "page_busy"),
        (AppError("boom"), 500, "internal_error"),
    ],
)
async def test_a_domain_error_becomes_a_json_response(error, status, code):
    app = FastAPI()
    main.register_exception_handlers(app)

    @app.get("/raise")
    async def _raise():
        raise error

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/raise")

    assert response.status_code == status
    assert response.json() == {"code": code, "message": error.message}


async def test_a_duplicate_error_also_names_the_existing_document():
    app = FastAPI()
    main.register_exception_handlers(app)

    @app.get("/raise")
    async def _raise():
        raise DuplicateDocument("already here", "abc-123")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/raise")

    assert response.status_code == 409
    assert response.json()["existing_document_id"] == "abc-123"
