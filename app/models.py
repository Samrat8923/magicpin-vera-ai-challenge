from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field

# Valid context scopes
ScopeEnum = Literal["category", "merchant", "customer", "trigger"]


# --- /v1/context ---
class ContextPush(BaseModel):
    scope: ScopeEnum
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str


class ContextSuccessResponse(BaseModel):
    accepted: Literal[True] = True
    ack_id: str
    stored_at: str


class ContextStaleResponse(BaseModel):
    accepted: Literal[False] = False
    reason: Literal["stale_version"] = "stale_version"
    current_version: int


class ContextErrorResponse(BaseModel):
    accepted: Literal[False] = False
    reason: str
    details: Optional[str] = None


ContextResponse = Union[ContextSuccessResponse, ContextStaleResponse, ContextErrorResponse]


# --- /v1/healthz ---
class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    uptime_seconds: int
    contexts_loaded: Dict[str, int]


# --- /v1/metadata ---
class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str


# --- /v1/tick ---
class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = Field(default_factory=list)


class TickAction(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: Literal["vera", "merchant_on_behalf"]
    trigger_id: str
    template_name: str
    template_params: List[str]
    body: str
    cta: str
    suppression_key: str
    rationale: str


class TickResponse(BaseModel):
    actions: List[TickAction] = Field(default_factory=list)


# --- /v1/reply ---
class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


class ReplySendResponse(BaseModel):
    action: Literal["send"] = "send"
    body: str
    cta: str = "open_ended"
    rationale: str


class ReplyWaitResponse(BaseModel):
    action: Literal["wait"] = "wait"
    wait_seconds: int
    rationale: str


class ReplyEndResponse(BaseModel):
    action: Literal["end"] = "end"
    rationale: str


ReplyResponse = Union[ReplySendResponse, ReplyWaitResponse, ReplyEndResponse]
