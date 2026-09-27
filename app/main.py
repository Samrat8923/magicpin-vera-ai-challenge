import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.health import router as health_router
from app.metadata import router as metadata_router
from app.context_routes import router as context_router
from app.tick_routes import router as tick_router
from app.reply_routes import router as reply_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("vera-bot")

app = FastAPI(
    title="Magicpin VERA AI Assistant",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    logger.warning("Validation error on %s: %s", request.url.path, errors)
    
    # Check if request was on context endpoint
    if request.url.path == "/v1/context":
        has_scope_err = any("scope" in err.get("loc", ()) for err in errors)
        reason = "invalid_scope" if has_scope_err else "invalid_request"
        return JSONResponse(
            status_code=400,
            content={
                "accepted": False,
                "reason": reason,
                "details": str(errors[0].get("msg", "Malformed request payload")),
            },
        )
    
    return JSONResponse(
        status_code=400,
        content={
            "accepted": False,
            "reason": "invalid_request",
            "details": str(errors[0].get("msg", "Malformed request")),
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "accepted": False,
            "reason": "internal_error",
            "details": "An unexpected error occurred.",
        },
    )


# Mount routers
app.include_router(health_router)
app.include_router(metadata_router)
app.include_router(context_router)
app.include_router(tick_router)
app.include_router(reply_router)
