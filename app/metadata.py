from fastapi import APIRouter
from app.config import settings
from app.models import MetadataResponse

router = APIRouter()


@router.get("/v1/metadata", response_model=MetadataResponse)
async def metadata():
    return MetadataResponse(
        team_name=settings.TEAM_NAME,
        team_members=settings.TEAM_MEMBERS,
        model=settings.MODEL,
        approach=settings.APPROACH,
        contact_email=settings.CONTACT_EMAIL,
        version=settings.VERSION,
        submitted_at=settings.SUBMITTED_AT,
    )
