""""
SÜRPRİZ VERİ - Bağımsız analiz API'si
"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import CONFIG
from .data_mapper import map_fixture_to_match_record
from .data_provider import FiveDollarFootballAPI, FootballAPIError
from .ai_analysis import build_ai_note, _team_form
from .similar_match_finder import find_similar_matches
from .models import HistoricalMatch
from .result_mapper import map_fixture_result

app = FastAPI(
    title="Sürpriz Veri API",
    description="Bay Tahmin'den bağımsız Sürpriz Veri analiz API'si.",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.futbol-ajani.com",
        "https://futbol-ajani.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _client() -> FiveDollarFootballAPI:
    return FiveDollarFootballAPI()


def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _client().flatten_fixture_list(payload)


def _historical_matches(days: int, current=None) -> List[HistoricalMatch]:
    """Güncel maçın ligindeki gerçek tarihsel maçları uzun dönem tarar.

    /fixtures yerine /leagues/{id}/fixtures kullanılır; böylece 24 saat
    pencere sınırına takılmadan API planının izin verdiği geçmiş taranır.
    """
    client = _client()
    now = datetime.now(timezone.utc)
    day_count = min(max(days, 1), 730)

    fixture = (
        current.metadata.get("fixture", {})
        if current is not None and isinstance(current.metadata, dict)
        else {}
    )
    league = fixture.get("league", {}) if isinstance(fixture, dict) else {}
    league_id = league.get("id") if isinstance(league, dict) else None
    if league_id is None:
        return []

    start_dt = now - timedelta(days=day_count)
    end_dt = now

    fixtures: List[Dict[str, Any]] = []
    page = 1

    while page <= 100:
        try:
            payload = client.league_fixtures(
                league_id=int(league_id),
                start_time=int(start_dt.timestamp()),
                end_time=int(end_dt.timestamp()),
                status="finished",
                include="odds,events,stats",
                page=page,
                per_page=50,
                order="desc",
            )
        except FootballAPIError:
            try:
                payload = client.league_fixtures(
                    league_id=int(league_id),
                    start_time=int(start_dt.timestamp()),
                    end_time=int(end_dt.timestamp()),
                    status="finished",
                    include="events,stats",
                    page=page,
                    per_page=100,
                    order="desc",
                )
            except FootballAPIError:
                break

        batch = _rows(payload)
        if not batch:
            break
        fixtures.extend(batch)

        pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
        if not isinstance(pagination, dict) or not pagination.get("has_more"):
            break
        page += 1

    result: List[HistoricalMatch] = []
    seen = set()

    for fixture in fixtures:
        fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
        if fixture_id is None or str(fixture_id) in seen:
            continue
        seen.add(str(fixture_id))

        fixture_odds = (
            fixture.get("odds")
            if isinstance(fixture.get("odds"), dict)
            else None
        )
        has_1x2 = (
            isinstance(fixture_odds, dict)
            and isinstance(fixture_odds.get("1x2"), dict)
            and isinstance(fixture_odds["1x2"].get("opening"), dict)
            and isinstance(fixture_odds["1x2"].get("closing"), dict)
        )

        if not has_1x2:
            try:
                odds_payload = client.fixture_odds(
                    fixture_id=int(fixture_id),
                    bookmaker="bet365",
                )
                fixture_odds = odds_payload
            except (FootballAPIError, ValueError, TypeError):
                fixture_odds = None

        try:
            record = map_fixture_to_match_record(
                fixture,
                odds_data=fixture_odds,
            )
            record = replace(record, outcome=map_fixture_result(fixture))
            result.append(
                HistoricalMatch(
                    record=record,
                    source="5DollarFootballAPI",
                )
            )
        except (ValueError, TypeError):
            continue

    return result


def _current_match(fixture_id: int):
    client = _client()
    payload = client.fixture(fixture_id, include="odds")
    rows = client.flatten_fixture_list(payload)
    fixture = rows[0] if rows else payload.get("data", payload)

    if not isinstance(fixture, dict):
        raise FootballAPIError("Maç verisi bulunamadı.")

    return map_fixture_to_match_record(
        fixture,
        odds_data=fixture.get("odds")
        if isinstance(fixture.get("odds"), dict)
        else None,
    )


def _team_history_summary(current) -> Dict[str, Any]:
    """Güncel maçın iki takımının son 10 tamamlanmış maçını özetler."""
    fixture = (
        current.metadata.get("fixture", {})
        if isinstance(current.metadata, dict)
        else {}
    )
    teams = fixture.get("teams", {}) if isinstance(fixture, dict) else {}
    home = teams.get("home", {}) if isinstance(teams, dict) else {}
    away = teams.get("away", {}) if isinstance(teams, dict) else {}
    home_id = home.get("id") if isinstance(home, dict) else None
    away_id = away.get("id") if isinstance(away, dict) else None
    client = _client()

    def load(team_id):
        if team_id is None:
            return []
        try:
            payload = client.team_fixtures(
                int(team_id),
                status="finished",
                include="stats",
                page=1,
                per_page=10,
            )
            return client.flatten_fixture_list(payload)
        except (FootballAPIError, TypeError, ValueError):
            return []

    return {
        "home": _team_form(load(home_id), home_id),
        "away": _team_form(load(away_id), away_id),
    }


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "status": "online",
        "service": "Sürpriz Veri",
        "engine": "Independent Surprise Data Engine",
        "data_provider": "5DollarFootballAPI",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "healthy", "service": "surpriz-veri"}


@app.get("/config")
def config() -> Dict[str, Any]:
    return {
        "api_base_url": CONFIG.api_base_url,
        "historical_days": CONFIG.historical_days,
        "opening_odds_tolerance": CONFIG.opening_odds_tolerance,
        "closing_odds_tolerance": CONFIG.closing_odds_tolerance,
        "movement_tolerance": CONFIG.movement_tolerance,
        "min_historical_samples": CONFIG.min_historical_samples,
        "default_result_limit": CONFIG.default_result_limit,
        "max_result_limit": CONFIG.max_result_limit,
        "pools": {
            "opening": CONFIG.use_opening_pool,
            "closing": CONFIG.use_closing_pool,
            "movement": CONFIG.use_movement_pool,
            "market_profile": CONFIG.use_market_profile_pool,
            "kickoff": CONFIG.use_kickoff_pool,
        },
    }


@app.get("/fixtures")
def fixtures(
    start_time: Optional[int] = Query(default=None),
    end_time: Optional[int] = Query(default=None),
    status: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    try:
        return _client().fixtures(
            start_time=start_time,
            end_time=end_time,
            status=status,
            include="odds,events,stats",
            page=page,
            per_page=per_page,
        )
    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/fixtures/{fixture_id}")
def fixture(fixture_id: int) -> Dict[str, Any]:
    try:
        return _client().fixture(fixture_id)
    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/fixtures/{fixture_id}/odds")
def fixture_odds(fixture_id: int) -> Dict[str, Any]:
    try:
        return _client().fixture_odds(fixture_id)
    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/fixtures/{fixture_id}/odds-history")
def fixture_odds_history(
    fixture_id: int,
    market: str = Query(default="1x2"),
) -> Dict[str, Any]:
    try:
        return _client().fixture_odds_history(
            fixture_id=fixture_id,
            market=market,
        )
    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/analyze/{fixture_id}")
def analyze(
    fixture_id: int,
    days: int = Query(default=CONFIG.historical_days, ge=1, le=730),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """Gerçek benzer tarihsel maçlar + güncel takım verileri + AI notu."""
    try:
        current = _current_match(fixture_id)
        historical = _historical_matches(days, current)
        similar = find_similar_matches(
            current,
            historical,
            display_limit=limit,
        )

        forms = _team_history_summary(current)
        ai = build_ai_note(
            current.home_team,
            current.away_team,
            forms["home"],
            forms["away"],
            similar["common"]["outcome_distribution"],
            similar["opening"]["outcome_distribution"],
            similar["closing"]["outcome_distribution"],
        )

        return {
            "match": {
                "id": current.match_id,
                "home_team": current.home_team,
                "away_team": current.away_team,
                "kickoff_time": current.kickoff_time,
                "competition": current.competition,
                "opening_odds": asdict(current.opening_odds),
                "closing_odds": asdict(current.closing_odds),
                "odds_movement": asdict(current.odds_movement),
                "market_profile": asdict(current.market_profile),
            },
            "historical_matches": len(historical),
            "similar_matches": similar,
            "team_analysis": forms,
            "ai_analysis": ai,
            "matching_rule": {
                "opening_tolerance": CONFIG.opening_odds_tolerance,
                "closing_tolerance": CONFIG.closing_odds_tolerance,
                "movement_tolerance": CONFIG.movement_tolerance,
                "description": (
                    "Açılış ve kapanış 1X2 oranları ölçeklenmiş toleransla, "
                    "hareket ise ilgili profil ile birlikte karşılaştırılır."
                ),
            },
        }

    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sürpriz Veri analizi başarısız: {exc}",
        ) from exc
