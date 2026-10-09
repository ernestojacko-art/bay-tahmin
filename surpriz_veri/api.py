"""SÜRPRİZ VERİ - Bağımsız analiz API'si"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import logging
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import os

logger = logging.getLogger("surpriz_veri.api")

from .config import CONFIG
from .data_mapper import map_fixture_to_match_record
from .data_provider import FiveDollarFootballAPI, FootballAPIError
from .nosyapi_provider import NosyAPIClient
from .ai_analysis import build_ai_note, _team_form
from .similar_match_finder import find_similar_matches
from .historical_matcher import build_historical_pools
from .surprise_candidates import generate_candidates
from .models import HistoricalMatch
from .result_mapper import map_fixture_result

app = FastAPI(
    title="Sürpriz Veri API",
    description="Bay Tahmin'den bağımsız Sürpriz Veri analiz API'si.",
    version="1.3.0",
)

_HISTORICAL_CACHE: Dict[int, Any] = {}
_HISTORICAL_CACHE_AT: Dict[int, float] = {}
_HISTORICAL_CACHE_TTL = 12 * 60 * 60

# Liste uç noktaları (ör. /leagues/{id}/fixtures) "include=odds" ile
# çağrılsa bile her zaman güvenilir şekilde oran gömmez. Bu nedenle
# gerçek açılış/kapanış oranı gereken tarihsel maçlar için ayrıca
# /fixtures/{id}/odds çağrılır. Bu, her analizde yüzlerce ek istek
# anlamına geleceğinden, çağrı sayısı sabit bir üst sınırla korunur.
_HISTORICAL_ODDS_FETCH_CAP = 40

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


# ---------------------------------------------------------------------------
# VERİ SAĞLAYICI SEÇİMİ
#
# 5DollarFootballAPI hesabımızın aylık ücretli aboneliği yenilenene kadar
# geçici olarak NosyAPI'ye geçildi (Render'da zaten tanımlı NosyAPI anahtarı
# kullanılır). Bu değişiklik SADECE Sürpriz Veri servisini kapsar; ana Bay
# Tahmin backend'i (backend/) hâlâ 5DollarFootballAPI ile çalışmaya devam
# eder ve bu dosyadan etkilenmez.
#
# Abonelik yenilendiğinde geri dönmek için:
#   Render ortam değişkenlerine SURPRISE_DATA_PROVIDER=5dollar eklemek yeterli.
# ---------------------------------------------------------------------------
_DATA_PROVIDER = os.getenv("SURPRISE_DATA_PROVIDER", "nosyapi").strip().lower()


def _client():
    if _DATA_PROVIDER in ("5dollar", "5dollarfootball", "5dollarfootballapi"):
        return FiveDollarFootballAPI()
    return NosyAPIClient()


def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _client().flatten_fixture_list(payload)


def _historical_matches(days: int, current=None) -> List[HistoricalMatch]:
    """Kapsanan liglerdeki gerçek tarihsel maçları uzun dönem tarar.

    Artık yalnızca güncel maçın ligine bağlı değildir. Böylece aynı/similar
    1X2 oran profiline sahip geçmiş maçlar farklı liglerde de bulunabilir.
    API planının tarih ve lig kapsamı sınırları aynen geçerlidir.
    """
    cached = _HISTORICAL_CACHE.get(days)
    cached_at = _HISTORICAL_CACHE_AT.get(days, 0.0)
    if cached is not None and time.time() - cached_at < _HISTORICAL_CACHE_TTL:
        return cached

    client = _client()
    now = datetime.now(timezone.utc)
    day_count = min(max(days, 1), 730)
    start_dt = now - timedelta(days=day_count)
    end_dt = now
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    # Önce tarih aralığında aktif olan tüm futbol liglerini al.
    leagues: List[Dict[str, Any]] = []
    page = 1
    while page <= 20:
        try:
            payload = client.leagues(
                active_since=start_ts,
                page=page,
                per_page=100,
            )
        except FootballAPIError:
            break

        data = payload.get("data", [])
        if isinstance(data, dict):
            data = (
                data.get("leagues")
                or data.get("items")
                or data.get("results")
                or []
            )
        if not isinstance(data, list) or not data:
            break

        leagues.extend(x for x in data if isinstance(x, dict))

        pagination = payload.get("pagination", {})
        if not isinstance(pagination, dict) or not pagination.get("has_more"):
            break
        page += 1

    # Aynı lig birden fazla sayfadan gelirse tekilleştir.
    league_ids: List[int] = []
    seen_leagues = set()
    for league in leagues:
        league_id = league.get("id")
        if league_id is None:
            continue
        try:
            league_id = int(league_id)
        except (TypeError, ValueError):
            continue
        if league_id not in seen_leagues:
            seen_leagues.add(league_id)
            league_ids.append(league_id)

    fixtures: List[Dict[str, Any]] = []

    # Analiz isteği içinde bütün ligleri ve sayfaları taramak, 10/dk
    # API sınırında dakikalarca beklemeye neden oluyordu. Her analizde
    # sınırlı sayıda toplu sayfa alınır; alınan gerçek kayıtlar 6 saat cache edilir.
    # Per-fixture odds çağrıları özellikle yapılmaz.
    fixtures: List[Dict[str, Any]] = []
    scan_budget = 8
    for league_id in league_ids:
        if scan_budget <= 0:
            break
        try:
            payload = client.league_fixtures(
                league_id=league_id,
                start_time=start_ts,
                end_time=end_ts,
                status="finished",
                include="odds,events,stats",
                page=1,
                per_page=50,
                order="desc",
            )
        except FootballAPIError:
            continue
        scan_budget -= 1
        fixtures.extend(_rows(payload))

    result: List[HistoricalMatch] = []
    seen = set()
    odds_fetch_budget = _HISTORICAL_ODDS_FETCH_CAP

    for fixture in fixtures:
        if odds_fetch_budget <= 0:
            break

        fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
        if fixture_id is None or str(fixture_id) in seen:
            continue
        seen.add(str(fixture_id))

        # Liste uç noktasının gömdüğü oran varsa önce onu dene;
        # yoksa (çoğunlukla olduğu gibi) tek maç oran uç noktasından
        # gerçek açılış/kapanış değerini ayrıca çek. Oran bulunamayan
        # bir tarihsel maç, açılış/kapanış havuzlarında hiçbir zaman
        # eşleşmeyeceğinden havuza eklenmesi anlamsızdır.
        fixture_odds = (
            fixture.get("odds")
            if isinstance(fixture.get("odds"), dict)
            else None
        )

        if not fixture_odds:
            try:
                fixture_odds = client.fixture_odds(int(fixture_id))
            except (FootballAPIError, TypeError, ValueError):
                fixture_odds = None

        odds_fetch_budget -= 1

        if not fixture_odds:
            continue

        try:
            record = map_fixture_to_match_record(
                fixture,
                odds_data=fixture_odds,
            )
            if (
                record.opening_odds.home is None
                and record.closing_odds.home is None
            ):
                # Oran bloğu geldi ama 1X2 çözümlenemedi; bu maç
                # hiçbir havuzda kullanılamaz, dahil etmenin anlamı yok.
                continue
            record = replace(record, outcome=map_fixture_result(fixture))
            result.append(
                HistoricalMatch(
                    record=record,
                    source="5DollarFootballAPI",
                )
            )
        except (ValueError, TypeError):
            continue

    _HISTORICAL_CACHE[days] = result
    _HISTORICAL_CACHE_AT[days] = time.time()
    return result


def _current_match(fixture_id: int):
    client = _client()
    payload = client.fixture(fixture_id, include="odds,events,stats")
    rows = client.flatten_fixture_list(payload)
    fixture = rows[0] if rows else payload.get("data", payload)

    if not isinstance(fixture, dict):
        raise FootballAPIError("Maç verisi bulunamadı.")

    odds_data = (
        fixture.get("odds")
        if isinstance(fixture.get("odds"), dict)
        else None
    )

    # "include=odds" bazı fixture yanıtlarında odds bloğunu ekine
    # taşımayabilir. Bu durumda ayrıca /fixtures/{id}/odds uç noktası
    # denenir; aksi hâlde oran karşılaştırması sessizce boş kalırdı.
    if not odds_data:
        try:
            odds_data = client.fixture_odds(fixture_id)
        except FootballAPIError:
            odds_data = None

    return map_fixture_to_match_record(
        fixture,
        odds_data=odds_data,
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
        "data_provider": "5DollarFootballAPI" if _DATA_PROVIDER.startswith("5dollar") else "NosyAPI",
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
        logger.exception("FootballAPIError: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/fixtures/week")
def weekly_fixtures(
    start_time: int = Query(...),
    end_time: int = Query(...),
    status: str = Query(default="scheduled"),
    per_page: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    """Haftalık programı, sağlayıcının 24 saatlik pencere sınırına uyarak toplar."""
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="Bitiş zamanı başlangıçtan sonra olmalıdır.")
    if end_time - start_time > 7 * 24 * 60 * 60:
        raise HTTPException(status_code=400, detail="En fazla 7 günlük aralık istenebilir.")

    client = _client()
    combined: List[Dict[str, Any]] = []
    cursor = start_time
    try:
        while cursor <= end_time:
            chunk_end = min(cursor + 24 * 60 * 60 - 1, end_time)
            payload = client.fixtures(
                start_time=cursor,
                end_time=chunk_end,
                status=status,
                include="odds,events,stats",
                page=1,
                per_page=min(per_page, 500),
            )
            combined.extend(client.flatten_fixture_list(payload))
            cursor = chunk_end + 1
    except FootballAPIError as exc:
        logger.exception("FootballAPIError: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    unique: Dict[str, Dict[str, Any]] = {}
    for fixture in combined:
        fixture_id = fixture.get("id") or fixture.get("fixture_id")
        if fixture_id is not None:
            unique[str(fixture_id)] = fixture
    return {"data": list(unique.values()), "count": len(unique)}


@app.get("/fixtures/{fixture_id}")
def fixture(fixture_id: int) -> Dict[str, Any]:
    try:
        return _client().fixture(fixture_id)
    except FootballAPIError as exc:
        logger.exception("FootballAPIError: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/fixtures/{fixture_id}/odds")
def fixture_odds(fixture_id: int) -> Dict[str, Any]:
    try:
        return _client().fixture_odds(fixture_id)
    except FootballAPIError as exc:
        logger.exception("FootballAPIError: %s", exc)
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
        logger.exception("FootballAPIError: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/analyze/{fixture_id}")
def analyze(
    fixture_id: int,
    days: int = Query(default=CONFIG.historical_days, ge=1, le=730),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """Gerçek benzer tarihsel maçlar + 5 havuzlu kanıt motoru + AI notu."""
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
            similar["comparison"]["outcome_distribution"],
            similar["opening"]["outcome_distribution"],
            similar["closing"]["outcome_distribution"],
            opening_count=similar["opening"]["match_count"],
            closing_count=similar["closing"]["match_count"],
            comparison_count=similar["comparison"]["match_count"],
        )

        # ------------------------------------------------------------
        # 5 HAVUZLU KANIT MOTORU (Opening / Closing / Movement /
        # Market Profile / Kickoff) — README'de tarif edilen asıl
        # sistem. Önceden bu motor hiçbir endpoint'e bağlı değildi;
        # artık /analyze çıktısının asıl gövdesidir.
        # ------------------------------------------------------------
        pools = build_historical_pools(current, historical)
        pool_sizes = pools.sizes()

        candidates = generate_candidates(
            current,
            pools,
            limit=limit,
        )

        surprise_candidates = [
            {
                "outcome": candidate.outcome,
                "score": candidate.score,
                "supporting_pools": candidate.supporting_pools,
                "sample_sizes": candidate.sample_sizes,
                "explanation": candidate.explanation,
            }
            for candidate in candidates
        ]

        return {
            "match": {
                "id": current.match_id,
                "home_team": current.home_team,
                "away_team": current.away_team,
                "kickoff_time": current.kickoff_time,
                "competition": current.competition,
                "opening_odds": asdict(current.opening_odds),
                "closing_odds": asdict(current.closing_odds),
                "closing_is_live_fallback": bool(
                    current.metadata.get("closing_is_live_fallback")
                    if isinstance(current.metadata, dict)
                    else False
                ),
                "odds_movement": asdict(current.odds_movement),
                "market_profile": asdict(current.market_profile),
            },
            "historical_matches": len(historical),
            "pool_sizes": pool_sizes,
            "surprise_candidates": surprise_candidates,
            "surprise_candidates_note": (
                "Yalnızca en az "
                f"{CONFIG.min_historical_samples} örneklemi olan havuzlardan "
                "destek alan İY/MS sonuçları listelenir. Skor, havuzların "
                "ağırlıklı tarihsel desteğini ifade eder; kesin sonuç veya "
                "kazanç garantisi değildir."
            ),
            "similar_matches": similar,
            "team_analysis": forms,
            "ai_analysis": ai,
            "matching_rule": {
                "opening_tolerance": CONFIG.opening_odds_tolerance,
                "closing_tolerance": CONFIG.closing_odds_tolerance,
                "movement_tolerance": CONFIG.movement_tolerance,
                "description": (
                    "İki ayrı tarihsel tablo kullanılır: mevcut maçın ilk açılış "
                    "1X2 oranı geçmiş maçların açılışlarıyla; mevcut/güncel 1X2 "
                    "oranı geçmiş maçların kapanışlarıyla karşılaştırılır. "
                    "Hareket ayrı bir benzerlik kriteri değildir. İki tablonun "
                    "aynı geçmiş maçta kesişmesi ayrıca gösterilir. "
                    "Tarihsel eşleşme lig ile sınırlandırılmaz. Bunların "
                    "yanında, beş bağımsız kanıt havuzunu (açılış, kapanış, "
                    "hareket, market profili, başlama saati) ağırlıklı olarak "
                    "birleştiren ayrı bir kanıt motoru 'surprise_candidates' "
                    "alanında sunulur."
                ),
            },
        }

    except FootballAPIError as exc:
        logger.exception("FootballAPIError: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Sürpriz Veri analizi başarısız: {exc}",
        ) from exc
