from fastapi import APIRouter
from app.models import TickRequest, TickResponse
from app.decision_engine import decision_engine

router = APIRouter()


@router.post("/v1/tick", response_model=TickResponse)
async def tick(body: TickRequest):
    actions = decision_engine.evaluate_tick(body.now, body.available_triggers)
    return TickResponse(actions=actions)
