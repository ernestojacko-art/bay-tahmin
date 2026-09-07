from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_analysis_service, get_provider
from app.core.exceptions import BayTahminError
from app.providers.base import BaseFootballDataProvider
from app.schemas.prediction import MatchPrediction, SurpriseCandidate
from app.services.analysis_service import AnalysisService

router = APIRouter(tags=["predictions"])


@router.get("/predictions", response_model=list[MatchPrediction])
async def list_predictions(
    match_date: date = Query(default_factory=date.today, alias="date"),
    league_id: str | None = Query(default=None),
    provider: BaseFootballDataProvider = Depends(get_provider),
    service: AnalysisService = Depends(get_analysis_service),
) -> list[MatchPrediction]:
    """Analyze every real fixture on the requested date."""
    try:
        fixtures = await provider.get_fixtures(match_date, league_id)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    results: list[MatchPrediction] = []
    for fixture in fixtures:
        try:
            results.append(await service.analyze_match(fixture.match_id))
        except BayTahminError:
            continue
    return results


@router.get("/daily-surprises")
async def daily_surprises(
    match_date: date = Query(default_factory=date.today, alias="date"),
    limit: int = Query(default=5, ge=1, le=20),
    league_id: str | None = Query(default=None),
    provider: BaseFootballDataProvider = Depends(get_provider),
    service: AnalysisService = Depends(get_analysis_service),
):
    """Rank real fixtures by their strongest Cloud Engine HT/FT surprise scenario."""
    try:
        fixtures = await provider.get_fixtures(match_date, league_id)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ranked = []
    for fixture in fixtures:
        try:
            prediction = await service.analyze_match(fixture.match_id)
        except BayTahminError:
            continue

        if not prediction.surprises:
            continue

        best = max(prediction.surprises, key=lambda item: item.composite_score)
        ranked.append({
            "match_id": prediction.match_id,
            "home_team": prediction.home_team.team_name,
            "away_team": prediction.away_team.team_name,
            "combination": best.combination,
            "score": best.composite_score,
            "risk": best.risk.value,
            "confidence": best.confidence,
            "data_quality": prediction.data_quality.value,
        })

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return {
        "date": match_date.isoformat(),
        "source": "BAY_TAHMIN_FOOTBALL_INTELLIGENCE_ENGINE",
        "provider": provider.name,
        "matches": ranked[:limit],
    }


@router.get("/surprises", response_model=list[SurpriseCandidate])
async def list_surprises(
    match_id: str = Query(..., description="Match to fetch surprise scenarios for"),
    service: AnalysisService = Depends(get_analysis_service),
) -> list[SurpriseCandidate]:
    try:
        prediction = await service.analyze_match(match_id)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return prediction.surprises
