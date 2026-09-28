"""
SÜRPRİZ VERİ - Independent API

Bu API mevcut Bay Tahmin API'sinden bağımsızdır.

Amaç:
- Güncel maçları almak
- Tarihsel veriyle karşılaştırmak
- HT/MS sürpriz adaylarını üretmek

Mevcut Bay Tahmin main.py dosyasına dokunmaz.
"""

from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query

from .config import CONFIG
from .data_provider import (
    FiveDollarFootballAPI,
    FootballAPIError,
)


app = FastAPI(
    title="Sürpriz Veri API",
    description=(
        "Bay Tahmin'den bağımsız "
        "Sürpriz Veri analiz API'si."
    ),
    version="1.0.0",
)


def _client() -> FiveDollarFootballAPI:
    """
    Sürpriz Veri için bağımsız 5DollarFootballAPI istemcisi.
    """

    return FiveDollarFootballAPI()


@app.get("/")
def root() -> Dict[str, Any]:
    """
    API durum kontrolü.
    """

    return {
        "status": "online",
        "service": "Sürpriz Veri",
        "engine": "Independent Surprise Data Engine",
        "data_provider": "5DollarFootballAPI",
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    """
    Sağlık kontrolü.
    """

    return {
        "status": "healthy",
        "service": "surpriz-veri",
    }


@app.get("/config")
def config() -> Dict[str, Any]:
    """
    Kullanılan temel motor ayarlarını döndürür.

    API anahtarı hiçbir şekilde döndürülmez.
    """

    return {
        "api_base_url": CONFIG.api_base_url,

        "opening_odds_tolerance": (
            CONFIG.opening_odds_tolerance
        ),

        "closing_odds_tolerance": (
            CONFIG.closing_odds_tolerance
        ),

        "movement_tolerance": (
            CONFIG.movement_tolerance
        ),

        "min_historical_samples": (
            CONFIG.min_historical_samples
        ),

        "default_result_limit": (
            CONFIG.default_result_limit
        ),

        "max_result_limit": (
            CONFIG.max_result_limit
        ),

        "pools": {
            "opening": (
                CONFIG.use_opening_pool
            ),
            "closing": (
                CONFIG.use_closing_pool
            ),
            "movement": (
                CONFIG.use_movement_pool
            ),
            "market_profile": (
                CONFIG.use_market_profile_pool
            ),
            "kickoff": (
                CONFIG.use_kickoff_pool
            ),
        },
    }


@app.get("/fixtures")
def fixtures(
    start_time: Optional[int] = Query(
        default=None
    ),
    end_time: Optional[int] = Query(
        default=None
    ),
    status: str = Query(
        default="all"
    ),
    page: int = Query(
        default=1,
        ge=1,
    ),
    per_page: int = Query(
        default=100,
        ge=1,
        le=500,
    ),
) -> Dict[str, Any]:
    """
    5DollarFootballAPI fixture verisini
    bağımsız olarak Sürpriz Veri API'sinden sunar.

    Bu endpoint henüz tahmin üretmez.
    """

    try:

        client = _client()

        return client.fixtures(
            start_time=start_time,
            end_time=end_time,
            status=status,
            include=(
                "odds,events,stats"
            ),
            page=page,
            per_page=per_page,
        )

    except FootballAPIError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.get("/fixtures/{fixture_id}")
def fixture(
    fixture_id: int,
) -> Dict[str, Any]:
    """
    Tek fixture detayını döndürür.
    """

    try:

        client = _client()

        return client.fixture(
            fixture_id
        )

    except FootballAPIError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.get(
    "/fixtures/{fixture_id}/odds"
)
def fixture_odds(
    fixture_id: int,
) -> Dict[str, Any]:
    """
    Fixture'ın mevcut odds verisini döndürür.
    """

    try:

        client = _client()

        return client.fixture_odds(
            fixture_id
        )

    except FootballAPIError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


@app.get(
    "/fixtures/{fixture_id}/odds-history"
)
def fixture_odds_history(
    fixture_id: int,
    market: str = Query(
        default="1x2"
    ),
) -> Dict[str, Any]:
    """
    Fixture odds geçmişini döndürür.

    Opening / Closing / Movement analizinin
    ham verisini sağlar.
    """

    try:

        client = _client()

        return client.fixture_odds_history(
            fixture_id=fixture_id,
            market=market,
        )

    except FootballAPIError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
