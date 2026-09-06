"""
BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE -- FastAPI application entrypoint.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import BayTahminError, MatchNotFoundError, ProviderNotConfiguredError
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "BAY TAHMİN Football Intelligence Engine -- a modular football analysis and "
        "prediction platform. The Chat Agent is a conversational voice layered on top "
        "of an independent statistical Prediction Engine; it is never the source of "
        "truth for match predictions itself."
    ),
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.exception_handler(ProviderNotConfiguredError)
async def provider_not_configured_handler(request: Request, exc: ProviderNotConfiguredError) -> JSONResponse:
    logger.warning("Provider not configured: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"error": "provider_not_configured", "detail": str(exc)},
    )


@app.exception_handler(MatchNotFoundError)
async def match_not_found_handler(request: Request, exc: MatchNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "match_not_found", "detail": str(exc)})


@app.exception_handler(BayTahminError)
async def generic_app_error_handler(request: Request, exc: BayTahminError) -> JSONResponse:
    logger.exception("Unhandled application error")
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "active_provider": settings.active_provider,
    }
