from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.models import ContextPush, ContextSuccessResponse, ContextStaleResponse
from app.store import context_store

router = APIRouter()


@router.post("/v1/context", response_model=ContextSuccessResponse)
async def push_context(body: ContextPush):
    accepted, current_version = context_store.upsert(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
    )

    if not accepted:
        stale_resp = ContextStaleResponse(
            accepted=False,
            reason="stale_version",
            current_version=current_version,
        )
        return JSONResponse(status_code=409, content=stale_resp.model_dump())

    ack_id = f"ack_{body.context_id}_v{body.version}"
    stored_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ContextSuccessResponse(
        accepted=True,
        ack_id=ack_id,
        stored_at=stored_at,
    )
