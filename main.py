from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_analysis_service, get_chat_orchestrator
from app.main import app as intelligence_app

import five_dollar_bridge as five

ISTANBUL = ZoneInfo("Europe/Istanbul")

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
        "double_chance": data.get("double_chance"),
        "draw_no_bet": data.get("draw_no_bet"),
        "expected_goals": data.get("expected_goals"),
        "btts_yes_probability": data.get("btts_yes_probability"),
        "over_under": data.get("over_under") or [],
        "asian_handicap": data.get("asian_handicap") or [],
        "half_time_one_x_two": data.get("half_time_one_x_two"),
        "half_time_full_time": data.get("half_time_full_time"),
        "score_matrix": data.get("score_matrix") or [],
        "score_scenarios": top_scores,
        "scenarios": scenarios,
        "surprises": surprises,
        "projections": {
            "one_x_two": data.get("one_x_two"),
            "double_chance": data.get("double_chance"),
            "draw_no_bet": data.get("draw_no_bet"),
            "expected_goals": data.get("expected_goals"),
            "btts_yes_probability": data.get("btts_yes_probability"),
            "over_under": data.get("over_under") or [],
            "asian_handicap": data.get("asian_handicap") or [],
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


# ---------------------------------------------------------------------------
# NOTE (2026-09-08): The routes below used to be wired in only via
# sitecustomize.py / usercustomize.py meta-path hooks that patch this module
# after import. Those hooks depend on this backend directory already being on
# sys.path when the Python interpreter starts, which is NOT guaranteed by the
# Render start command ("uvicorn main:app"). Registering them directly here,
# unconditionally, removes that fragile dependency. The frontend
# (bay-tahmin-pro) calls all three of these directly.
# ---------------------------------------------------------------------------


@app.post("/chat", tags=["legacy-compat"])
async def legacy_general_chat(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get("message") or payload.get("question") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")
    session_id = str(payload.get("session_id") or f"general-{uuid.uuid4()}")
    match_id = payload.get("match_id")
    result = await get_chat_orchestrator().handle_message(
        session_id, message, str(match_id) if match_id is not None else None
    )
    return result.model_dump(mode="json")


@app.get("/admin/cache/status", tags=["admin"])
async def admin_cache_status():
    now = datetime.now(ISTANBUL).date()
    keys = []
    for i in range(7):
        key = str(now + timedelta(days=i))
        entry = five._MATCH_CACHE.get(key)
        keys.append({
            "date": key,
            "cached": bool(entry),
            "age_seconds": round(datetime.now().timestamp() - entry[0], 1) if entry else None,
        })
    return {
        "match_cache": {
            "ttl_seconds": five._MATCH_CACHE_TTL_SECONDS,
            "stale_ttl_seconds": five._MATCH_STALE_TTL_SECONDS,
            "days": keys,
        },
        "fixture_detail_cache": {
            "ttl_seconds": five._FIXTURE_DETAIL_TTL_SECONDS,
            "entries": len(five._FIXTURE_DETAIL_CACHE),
        },
        "provider": "5dollarfootballapi",
    }


@app.get("/admin/accuracy", tags=["admin"])
async def admin_accuracy(resolve: bool = True):
    from accuracy import accuracy_snapshot, resolve_pending

    resolved_now = await resolve_pending(8) if resolve else 0
    return {"resolved_now": resolved_now, **(await accuracy_snapshot(30))}


@app.post("/admin/accuracy/resolve", tags=["admin"])
async def admin_accuracy_resolve():
    from accuracy import accuracy_snapshot, resolve_pending

    resolved_now = await resolve_pending(8)
    return {"resolved_now": resolved_now, **(await accuracy_snapshot(30))}
