"""Engine registry endpoints."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.repositories.engine_repository import EngineRepository
from app.schemas.engine import EngineRead

router = APIRouter(prefix="/engines", tags=["engines"])


@router.get("", response_model=list[EngineRead], summary="List extraction engines")
async def list_engines(
    session: SessionDep,
    active_only: Annotated[bool, Query(description="Only engines new work runs on.")] = False,
) -> list[EngineRead]:
    """Which engines results can come from, and what each one costs.

    Every extraction records the engine it ran on plus a snapshot of that
    engine's price and version, so changing a row here never rewrites the cost
    of work that already happened.
    """
    engines = await EngineRepository(session).list_all(active_only=active_only)
    return [EngineRead.model_validate(e) for e in engines]
