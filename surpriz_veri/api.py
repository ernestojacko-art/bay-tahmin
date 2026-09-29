"""
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
from .historical_matcher import build_historical_pools
from .main import analyze_match
from .models import HistoricalMatch
from .result_mapper import map_fixture_result
from .surprise_candidates import generate_candidates

app = FastAPI(
    title="Sürpriz Veri API",
    description="Bay Tahmin'den bağımsız Sürpriz Veri analiz API'si.",
    version="1.1.0",
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


def _historical_matches(days: int) -> List[HistoricalMatch]:
    """
    Son N günlük tamamlanmış maçları toplar.

    5DollarFootballAPI fixtures endpoint'i 24 saatlik pencere
    kullandığı için günlük pencereler korunur. Ancak pencereler
    paralel çekilir; böylece /analyze isteği Render üzerinde
    gereksiz yere uzun sürmez.

    Opening, closing ve hareket aynı MatchRecord içinde
    ayrı alanlar olarak korunur.
    """
    from concurrent.futures import ThreadPoolExecutor

    now = datetime.now(timezone.utc)
    day_count = min(max(days, 1), 90)

    def load_day(offset: int) -> List[HistoricalMatch]:
        client = _client()
        end_dt = now - timedelta(days=offset - 1)
        start_dt = end_dt - timedelta(days=1)

        try:
            payload = client.fixtures(
                start_time=int(start_dt.timestamp()),
                end_time=int(end_dt.timestamp()),
                status="finished",
                include="odds",
                page=1,
                per_page=100,
            )
        except FootballAPIError:
            # Tek bir tarihsel günün API hatası bütün analizi
            # başarısız bırakmamalıdır. Diğer günlerin havuzları
            # kullanılmaya devam eder.
            return []

        result: List[HistoricalMatch] = []

        for fixture in _rows(payload):
            try:
                fixture_id = fixture.get("id")
                if fixture_id is None:
                    continue

                # Tarihsel fixture listesindeki odds eksikse,
                # maçın bookmaker odds endpoint'inden gerçek
                # 1X2 odds verisini ayrıca al.
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
                        fixture_odds = client.fixture_odds(
                            fixture_id=int(fixture_id),
                            bookmaker="bet365",
                        )
                    except (FootballAPIError, ValueError, TypeError):
                        fixture_odds = None

                record = map_fixture_to_match_record(
                    fixture,
                    odds_data=fixture_odds,
                )
                record = replace(
                    record,
                    outcome=map_fixture_result(fixture),
                )

                result.append(
                    HistoricalMatch(
                        record=record,
                        source="5DollarFootballAPI",
                    )
                )
            except (ValueError, TypeError):
                continue

        return result

    # Aynı anda sınırlı sayıda istek gönderilir; API'yi
    # gereksiz şekilde yüklememek için worker sayısı sabittir.
    with ThreadPoolExecutor(max_workers=5) as executor:
        batches = executor.map(
            load_day,
            range(1, day_count + 1),
        )

        result: List[HistoricalMatch] = []
        seen = set()

        for batch in batches:
            for historical in batch:
                fixture_id = historical.record.match_id

                if fixture_id in seen:
                    continue

                seen.add(fixture_id)
                result.append(historical)

    return result


def _current_match(fixture_id: int):
    client = _client()
    payload = client.fixture(fixture_id, include="odds")
    rows = client.flatten_fixture_list(payload)
    fixture = rows[0] if rows else payload.get("data", payload)

    if not isinstance(fixture, dict):
        raise FootballAPIError("Maç verisi bulunamadı.")

    # Güncel fixture cevabında odds zaten include="odds" ile istenir.
    # Ayrı bookmaker odds çağrısı bazı maçlarda veri bulunmadığında
    # analizi gereksiz yere 502 ile kesebildiği için burada zorunlu değildir.
    return map_fixture_to_match_record(
        fixture,
        odds_data=fixture.get("odds")
        if isinstance(fixture.get("odds"), dict)
        else None,
    )


def _team_history_summary(current) -> Dict[str, Any]:
    """Güncel maçın iki takımının son 10 tamamlanmış maçını özetler."""
    fixture = current.metadata.get("fixture", {}) if isinstance(current.metadata, dict) else {}
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
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """Gerçek benzer tarihsel maçlar + güncel takım verileri + AI notu."""
    try:
        current = _current_match(fixture_id)
        historical = _historical_matches(days)
        similar = find_similar_matches(current, historical, display_limit=limit)

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
                "description": "Açılış ve kapanış 1X2 oranları ölçeklenmiş toleransla, hareket ise ilgili profil ile birlikte karşılaştırılır.",
            },
        }

    except FootballAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sürpriz Veri analizi başarısız: {exc}",
        ) from exc
