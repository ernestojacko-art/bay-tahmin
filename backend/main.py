from __future__ import annotations

import uuid
from datetime import date

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_analysis_service, get_chat_orchestrator
from app.main import app as intelligence_app

import five_dollar_bridge as five

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
    return await five.get_matches(date)


@app.get("/mac/{match_id}", tags=["legacy-compat"])
async def legacy_match_detail(match_id: int):
    return await five.get_match_detail(match_id)


@app.get("/match/{match_id}", tags=["legacy-compat"])
async def legacy_match_detail_alias(match_id: int):
    return await legacy_match_detail(match_id)


@app.get("/leagues", tags=["legacy-compat"])
async def legacy_leagues():
    return []


def _legacy_analysis_contract(prediction):
    """Expose Cloud Engine output both in its native shape and legacy-friendly fields."""
    data = prediction.model_dump(mode="json")
    top_scores = sorted(data.get("score_matrix") or [], key=lambda item: item.get("probability", 0), reverse=True)[:5]
    scenarios = data.get("scenarios") or []
    surprises = data.get("surprises") or []

    return {
        "analysis": data,
        "prediction": data,
        "source": "BAY_TAHMIN_FOOTBALL_INTELLIGENCE_ENGINE",
        "provider": "5dollarfootballapi",
        "one_x_two": data.get("one_x_two"),
        "expected_goals": data.get("expected_goals"),
        "btts_yes_probability": data.get("btts_yes_probability"),
        "over_under": data.get("over_under") or [],
        "half_time_one_x_two": data.get("half_time_one_x_two"),
        "half_time_full_time": data.get("half_time_full_time"),
        "score_matrix": data.get("score_matrix") or [],
        "score_scenarios": top_scores,
        "scenarios": scenarios,
        "surprises": surprises,
        "projections": {
            "one_x_two": data.get("one_x_two"),
            "expected_goals": data.get("expected_goals"),
            "btts_yes_probability": data.get("btts_yes_probability"),
            "over_under": data.get("over_under") or [],
            "half_time_one_x_two": data.get("half_time_one_x_two"),
            "half_time_full_time": data.get("half_time_full_time"),
        },
        "confidence": data.get("confidence"),
        "market_comparison": data.get("market_comparison"),
        "data_quality": data.get("data_quality"),
    }


@app.get("/ai/analyze/{match_id}", tags=["legacy-compat"])
async def legacy_ai_analyze(match_id: int):
    prediction = await get_analysis_service().analyze_match(str(match_id))
    return _legacy_analysis_contract(prediction)


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
