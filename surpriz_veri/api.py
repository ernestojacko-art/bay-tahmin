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


def _historical_matches(days: int, current=None) -> List[HistoricalMatch]:
    """Uzun dönem gerçek tarihsel maç havuzu oluşturur.

    /fixtures yerine /leagues/{id}/fixtures kullanılır; böylece 24 saat
    pencere sınırına takılmadan 12-24 aylık geçmiş taranabilir. API planı
    hangi tarih aralığına izin veriyorsa sağlayıcı o sınırı uygular.
    """
    client = _client()
    now = datetime.now(timezone.utc)
    day_count = min(max(days, 1), 730)

    # Öncelikle güncel maçın ligini kullan. Bu, tarihsel odds karşılaştırmasını
    # gereksiz yüzlerce lig isteğiyle boğmadan aynı rekabet içindeki gerçek
    # geçmiş maçları yüksek veri kalitesiyle toplar.
    fixture = current.metadata.get("fixture", {}) if current is not None and isinstance(current.metadata, dict) else {}
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
            # Free planlarda fixture-list odds genişletmesi kapalı olabilir.
            # Aynı uzun tarihsel liste odds olmadan alınır; yalnızca gereken
            # maçların odds'u tekil endpoint'ten tamamlanır.
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

        fixture_odds = fixture.get("odds") if isinstance(fixture.get("odds"), dict) else None
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
            result.append(HistoricalMatch(record=record, source="5DollarFootballAPI"))
        except (ValueError, TypeError):
            continue

    return result

