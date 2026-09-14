from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_analysis_service, get_provider
from app.core.exceptions import BayTahminError
from app.providers.base import BaseFootballDataProvider
from app.schemas.prediction import MatchPrediction, SurpriseCandidate
from app.services.analysis_service import AnalysisService
from app.services.fixture_selection import istanbul_today, select_upcoming


def _market_surprise(prediction: MatchPrediction) -> tuple[float, str, float, float] | None:
    """Pick only a non-market-favourite outcome with a positive model edge."""
    market = prediction.market_comparison.market_implied
    if not prediction.market_comparison.market_available or market is None:
        return None
    model = {"1": prediction.one_x_two.home_win, "X": prediction.one_x_two.draw, "2": prediction.one_x_two.away_win}
    implied = {"1": market.home_win, "X": market.draw, "2": market.away_win}
    market_favorite = max(implied, key=implied.get)
    selection = max((side for side in model if side != market_favorite), key=model.get)
    edge = model[selection] - implied[selection]
    if edge < 0.04:
        return None
    score = min(1.0, 0.70 * edge + 0.20 * model[selection] + 0.10 * (1 - implied[selection]))
    return round(score, 4), selection, model[selection], implied[selection]

router = APIRouter(tags=["predictions"])


@router.get("/predictions", response_model=list[MatchPrediction])
async def list_predictions(
    match_date: date = Query(default_factory=istanbul_today, alias="date"),
    league_id: str | None = Query(default=None),
    provider: BaseFootballDataProvider = Depends(get_provider),
    service: AnalysisService = Depends(get_analysis_service),
) -> list[MatchPrediction]:
    """Analyze every real fixture on the requested date."""
    try:
        fixtures = select_upcoming(await provider.get_fixtures(match_date, league_id), target_date=match_date)
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
    match_date: date = Query(default_factory=istanbul_today, alias="date"),
    limit: int = Query(default=5, ge=1, le=20),
    league_id: str | None = Query(default=None),
    provider: BaseFootballDataProvider = Depends(get_provider),
    service: AnalysisService = Depends(get_analysis_service),
):
    """Rank real fixtures by market-validated 1X2 surprise potential."""
    try:
        fixtures = select_upcoming(await provider.get_fixtures(match_date, league_id), target_date=match_date)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ranked = []
    for fixture in fixtures:
        try:
            dataset = await service.get_dataset(fixture.match_id)
            prediction = await service.analyze_match(fixture.match_id)
        except BayTahminError:
            continue

        # Generic surprise ranking is a match-result question.  It must not
        # silently turn into an HT/FT list or use a market that is not open.
        if not any(m.market_name.lower().startswith("maç sonucu 1x2") for m in dataset.odds_markets):
            continue
        best = _market_surprise(prediction)
        if best is None:
            continue
        score, selection, model_probability, market_probability = best
        ranked.append({
            "match_id": prediction.match_id,
            "home_team": prediction.home_team.team_name,
            "away_team": prediction.away_team.team_name,
            "combination": selection,  # legacy field; use `selection` for 1X2 semantics
            "selection": selection,
            "score": score,
            "model_probability": model_probability,
            "market_implied_probability": market_probability,
            "risk": prediction.confidence.risk.value,
            "confidence": prediction.confidence.confidence,
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
