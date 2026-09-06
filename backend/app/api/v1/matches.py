from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_analysis_service, get_provider
from app.core.exceptions import BayTahminError, MatchNotFoundError
from app.providers.base import BaseFootballDataProvider
from app.schemas.match import MatchSummary, TeamSummary
from app.schemas.prediction import MatchPrediction
from app.services.analysis_service import AnalysisService

router = APIRouter(tags=["matches"])


@router.get("/matches", response_model=list[MatchSummary])
async def list_matches(
    match_date: date = Query(default_factory=date.today, alias="date"),
    league_id: str | None = Query(default=None),
    provider: BaseFootballDataProvider = Depends(get_provider),
) -> list[MatchSummary]:
    try:
        fixtures = await provider.get_fixtures(match_date, league_id)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return [
        MatchSummary(
            match_id=f.match_id,
            league_id=f.league_id,
            league_name=f.league_name,
            kickoff=f.kickoff,
            home_team=TeamSummary(team_id=f.home_team.team_id, name=f.home_team.name),
            away_team=TeamSummary(team_id=f.away_team.team_id, name=f.away_team.name),
            status=f.status.value,
        )
        for f in fixtures
    ]


@router.get("/matches/{match_id}", response_model=MatchSummary)
async def get_match(
    match_id: str, provider: BaseFootballDataProvider = Depends(get_provider)
) -> MatchSummary:
    try:
        fixture = await provider.get_fixture(match_id)
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if fixture is None:
        raise HTTPException(status_code=404, detail=f"Match '{match_id}' not found.")
    return MatchSummary(
        match_id=fixture.match_id,
        league_id=fixture.league_id,
        league_name=fixture.league_name,
        kickoff=fixture.kickoff,
        home_team=TeamSummary(team_id=fixture.home_team.team_id, name=fixture.home_team.name),
        away_team=TeamSummary(team_id=fixture.away_team.team_id, name=fixture.away_team.name),
        status=fixture.status.value,
    )


@router.get("/matches/{match_id}/analysis", response_model=MatchPrediction)
async def get_match_analysis(
    match_id: str, service: AnalysisService = Depends(get_analysis_service)
) -> MatchPrediction:
    try:
        return await service.analyze_match(match_id)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/matches/{match_id}/analyze", response_model=MatchPrediction)
async def analyze_match(
    match_id: str, service: AnalysisService = Depends(get_analysis_service)
) -> MatchPrediction:
    """Force a fresh analysis, bypassing the cache."""
    try:
        return await service.analyze_match(match_id, force_refresh=True)
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BayTahminError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
