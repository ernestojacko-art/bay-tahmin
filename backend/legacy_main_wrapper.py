from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_analysis_service, get_chat_orchestrator
from app.main import app as intelligence_app
from app.schemas.chat import ChatRequest

import five_dollar_bridge as five

# The Cloud Engine is the application of record. These compatibility routes
# keep the existing Bay Tahmin frontend contract alive while the frontend can
# continue using the same Render service URL.
app = intelligence_app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["legacy-compat"])
async def root_compat():
    return {"status": "online", "agent": "Bay Tahmin", "engine": "BAY TAHMİN Football Intelligence Engine"}


@app.get("/matches", tags=["legacy-compat"])
async def legacy_matches(date: str | None = None):
    """Original fixture-list contract backed by the existing 5Dollar bridge."""
    return await five.get_matches(date)


@app.get("/mac/{match_id}", tags=["legacy-compat"])
async def legacy_match_detail(match_id: int):
    """Original match-detail contract, including live market data."""
    return await five.get_match_detail(match_id)


@app.get("/match/{match_id}", tags=["legacy-compat"])
async def legacy_match_detail_alias(match_id: int):
    return await legacy_match_detail(match_id)


@app.get("/leagues", tags=["legacy-compat"])
async def legacy_leagues():
    return []


@app.get("/ai/analyze/{match_id}", tags=["legacy-compat"])
async def legacy_ai_analyze(match_id: int):
    """Compatibility endpoint now powered by the Cloud Intelligence Engine."""
    service = get_analysis_service()
    prediction = await service.analyze_match(str(match_id))
    return {
        "analysis": prediction.model_dump(mode="json"),
        "source": "BAY_TAHMIN_FOOTBALL_INTELLIGENCE_ENGINE",
        "provider": "5dollarfootballapi",
    }


@app.post("/matches/{match_id}/chat", tags=["legacy-compat"])
async def legacy_match_chat(match_id: int, request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    session_id = str(payload.get("session_id") or f"match-{match_id}")
    result = await get_chat_orchestrator().handle_message(session_id, message, str(match_id))
    return result.model_dump(mode="json")


# Also expose the new versioned API under the same Render service:
# /api/v1/matches, /api/v1/matches/{id}/analysis, /api/v1/chat, etc.
