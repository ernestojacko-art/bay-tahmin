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
    """Analyze every fixture on the given date. Errors on individual matches are skipped, not fatal."""
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
