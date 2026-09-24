"""The request-scoped session dependency, on the application's own engine.

The API tests replace this dependency (the app's engine is bound to one event
loop), so its commit/rollback behaviour is exercised directly here.
"""

import pytest
from sqlalchemy import text

from app.db import session as db_session


@pytest.fixture(autouse=True)
async def _dispose_app_engine():
    yield
    # Connections opened here belong to this test's loop; do not leave them
    # in the pool for another loop to trip over.
    await db_session.engine.dispose()


async def test_a_request_that_succeeds_commits_and_closes_its_session():
    generator = db_session.get_session()
    session = await anext(generator)
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1

    with pytest.raises(StopAsyncIteration):
        await anext(generator)  # the request finished normally


async def test_a_request_that_fails_rolls_back_and_re_raises():
    generator = db_session.get_session()
    session = await anext(generator)
    await session.execute(text("SELECT 1"))

    with pytest.raises(RuntimeError, match="handler failed"):
        await generator.athrow(RuntimeError("handler failed"))

    assert not session.in_transaction()
