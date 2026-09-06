from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import chat, matches, predictions

api_router = APIRouter()
api_router.include_router(matches.router)
api_router.include_router(predictions.router)
api_router.include_router(chat.router)
