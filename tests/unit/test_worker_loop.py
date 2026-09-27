"""The worker's loop, without a database.

`tick` is replaced, so nothing here can pick up real rows. What is under test
is only the loop around it: when it sleeps, when it drains, and that it
survives a failing pass and stops promptly.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.workers.pipeline_worker import PipelineWorker


@pytest.fixture
def settings(tmp_path) -> Settings:
    # A poll interval far longer than the test: anything that finishes quickly
    # did so without waiting it out.
    return Settings(storage_dir=tmp_path, fa_api_token="t", worker_poll_interval_seconds=30)


async def _wait_for(condition, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def test_stop_wakes_a_sleeping_worker_immediately(settings):
    worker = PipelineWorker(None, settings)
    worker.tick = AsyncMock(return_value=False)
    task = asyncio.create_task(worker.run_forever())
    await _wait_for(lambda: worker.tick.await_count >= 1)

    worker.stop()

    # Without the wake-up this would sit in a 30-second sleep.
    await asyncio.wait_for(task, timeout=2)
    assert worker.tick.await_count == 1


async def test_a_busy_worker_drains_the_queue_before_sleeping(settings):
    worker = PipelineWorker(None, settings)
    worker.tick = AsyncMock(side_effect=[True, True, True, False])
    task = asyncio.create_task(worker.run_forever())

    await _wait_for(lambda: worker.tick.await_count >= 4)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)

    assert worker.tick.await_count == 4  # three busy passes, no sleep between them


async def test_a_failing_tick_does_not_end_the_loop(settings, caplog):
    # A failed pass counts as "no work", so the loop sleeps before trying again;
    # a short interval lets the test see it come back round.
    settings.worker_poll_interval_seconds = 0.01
    worker = PipelineWorker(None, settings)
    worker.tick = AsyncMock(side_effect=[RuntimeError("db went away"), False, False])
    task = asyncio.create_task(worker.run_forever())

    await _wait_for(lambda: worker.tick.await_count >= 3)
    worker.stop()
    await asyncio.wait_for(task, timeout=2)

    assert "pipeline tick failed" in caplog.text
    assert task.exception() is None


async def test_a_worker_told_to_stop_before_it_starts_does_no_work(settings):
    worker = PipelineWorker(None, settings)
    worker.tick = AsyncMock(return_value=False)
    worker.stop()

    await asyncio.wait_for(worker.run_forever(), timeout=2)

    worker.tick.assert_not_awaited()
