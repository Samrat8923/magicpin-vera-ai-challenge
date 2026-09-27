from typing import Union
from fastapi import APIRouter
from app.models import (
    ReplyRequest,
    ReplySendResponse,
    ReplyWaitResponse,
    ReplyEndResponse,
)
from conversation_handlers import conversation_handler

router = APIRouter()


@router.post(
    "/v1/reply",
    response_model=Union[ReplySendResponse, ReplyWaitResponse, ReplyEndResponse],
)
async def reply(body: ReplyRequest):
    return conversation_handler.handle_reply(body)
