"""SÜRPRİZ VERİ - Bağımsız analiz API'si"""

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import logging
import threading
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import os

# uvicorn kendi root logger handler'ını önceden kurmuş olabilir; force=True
# ile seviyeyi INFO'ya zorlamak, aksi halde WARNING eşiği yüzünden
# tanı amaçlı logger.info(...) çağrılarının sessizce yutulmasını önler.
logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger("surpriz_veri.api")
logging.getLogger("surpriz_veri.nosyapi_provider").setLevel(logging.INFO)

from .config import CONFIG
from .data_mapper import map_fixture_to_match_record
from .data_provider import FiveDollarFootballAPI, FootballAPIError
from .nosyapi_provider import NosyAPIClient
from .ai_analysis import build_ai_note, _team_form
from .similar_match_finder import find_similar_matches
from .historical_matcher import build_historical_pools
from .surprise_candidates import generate_candidates
from .models import HistoricalMatch
from .history_pool import HistoryPool
from .result_mapper import map_fixture_result

app = FastAPI(
    title="Sürpriz Veri API",
    description="Futbol Ajanı'den bağımsız Sürpriz Veri analiz API'si.",
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


WEEK_BUDGET_SECONDS = float(os.getenv("SURPRISE_WEEK_BUDGET_SECONDS", "25"))
_WEEK_TTL = 10 * 60
_WEEK_CACHE: Dict[Any, Dict[str, Any]] = {}
_WEEK_LOCK = threading.Lock()
_WEEK_BG: set = set()


_WEEK_NEXT: Dict[Any, Dict[str, Any]] = {}


def _week_fill_day(day, status: str, per_page: int, deadline: Optional[float], refresh: bool = True, background: bool = False) -> None:
    """Bir günün sayfalarını (kaldığı yerden) çeker; deadline dolunca bırakır.

    Süresi dolan gün önbelleği BOŞALTILMAZ: eski satırlar sunulmaya devam eder, yeni liste ayrı
    bir girişte doldurulur ve tamamlanınca eskisiyle yer değiştirir (kullanıcı hiç boş liste görmez).
    ``refresh=False`` (istek yolu): var olan giriş (bayat olsa da) olduğu gibi kullanılır.
    """
    key = (day, status)
    with _WEEK_LOCK:
        cur = _WEEK_CACHE.get(key)
        if cur is None:
            entry = {"rows": [], "next_page": 1, "complete": False, "ts": time.time()}
            _WEEK_CACHE[key] = entry
            cur = None
        elif not cur["complete"]:
            entry = cur
        elif time.time() - cur["ts"] > _WEEK_TTL:
            if not refresh:
                return
            entry = _WEEK_NEXT.get(key)
            if entry is None:
                entry = {"rows": [], "next_page": 1, "complete": False, "ts": time.time()}
                _WEEK_NEXT[key] = entry
        else:
            return
    client = _client()
    client.background = background
    while not entry["complete"]:
        if deadline is not None and time.monotonic() > deadline:
            return
        with _WEEK_LOCK:
            page = entry["next_page"]
        payload = client.fixtures(
            start_time=day[0], end_time=day[1], status=status, include="odds",
            page=page, per_page=min(per_page, 100),
        )
        rows = client.flatten_fixture_list(payload)
        pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
        with _WEEK_LOCK:
            if entry["next_page"] == page:  # başka bir iş parçacığı aynı sayfayı eklemiş olabilir
                entry["rows"].extend(rows)
                entry["next_page"] = page + 1
                if not rows or not (isinstance(pagination, dict) and pagination.get("has_more")) or page >= 60:
                    entry["complete"] = True
                    entry["ts"] = time.time()
                    if _WEEK_NEXT.get(key) is entry:  # yenisi tamamlandı: eskisiyle değiştir
                        _WEEK_CACHE[key] = entry
                        del _WEEK_NEXT[key]


def _istanbul_week_days(extra_days: int = 7):
    """Frontend'in (Türkiye saatiyle gün başı) haftalık penceresiyle birebir aynı 24 saatlik parçalar."""
    tz = timezone(timedelta(hours=3))
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(extra_days):
        d0 = start + timedelta(days=i)
        out.append((int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp()) - 1))
    return out


def _week_warm_loop() -> None:
    """Sunucu ayaktayken hafta listesini önceden hazırlar/yeniler (kullanıcı beklemesin)."""
    time.sleep(5)
    while True:
        try:
            for day in _istanbul_week_days():
                _week_fill_day(day, "all", 100, None, refresh=True, background=True)
        except Exception as exc:
            logger.warning("hafta listesi önhesaplama hatası: %s", exc)
        time.sleep(_WEEK_TTL // 2)


def _week_start_background(days, status: str, per_page: int) -> None:
    token = (tuple(days), status)
    with _WEEK_LOCK:
        if token in _WEEK_BG:
            return
        _WEEK_BG.add(token)

    def work():
        try:
            for day in days:
                _week_fill_day(day, status, per_page, None, refresh=True, background=True)
        except Exception as exc:
            logger.warning("hafta listesi arka plan doldurma hatası: %s", exc)
        finally:
            with _WEEK_LOCK:
                _WEEK_BG.discard(token)

    threading.Thread(target=work, name="week-fill", daemon=True).start()


_HISTORY_POOL = None


def _history_pool() -> HistoryPool:
    global _HISTORY_POOL
    if _HISTORY_POOL is None:
        _HISTORY_POOL = HistoryPool(_client, "NosyAPI" if _DATA_PROVIDER == "nosyapi" else "5DollarFootballAPI")
    return _HISTORY_POOL


def _historical_matches(days: int, current=None) -> List[HistoricalMatch]:
    """Kapsanan liglerdeki gerçek tarihsel maçları uzun dönem tarar.

    Artık yalnızca güncel maçın ligine bağlı değildir. Böylece aynı/similar
    1X2 oran profiline sahip geçmiş maçlar farklı liglerde de bulunabilir.
    API planının tarih ve lig kapsamı sınırları aynen geçerlidir.
    """
    pool = _history_pool()
    pool.ensure_started(days)
    # Havuz henüz boşsa (servis yeni uyandı) yükleyiciye kısa süre tanı.
    pool.wait_for_minimum(int(os.getenv("SURPRISE_WARMUP_MIN_MATCHES", "1500")), float(os.getenv("SURPRISE_WARMUP_SECONDS", "25")))
    result = pool.matches()
    logger.info("_historical_matches(): havuz=%d maç, durum=%s", len(result), pool.status)
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
    status: str = Query(default="all"),
    per_page: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    """Haftalık programı, sağlayıcının 24 saatlik pencere sınırına uyarak toplar."""
    if end_time <= start_time:
        raise HTTPException(status_code=400, detail="Bitiş zamanı başlangıçtan sonra olmalıdır.")
    if end_time - start_time > 7 * 24 * 60 * 60:
        raise HTTPException(status_code=400, detail="En fazla 7 günlük aralık istenebilir.")

    # Hız sınırı (~10 istek/dk) nedeniyle 7 günlük tam liste dakikalar sürer. Bu yüzden:
    # her gün ayrı önbelleğe alınır, istek en fazla WEEK_BUDGET saniye bekler ve o ana
    # kadar çekilen maçlarla yanıt verir; kalan sayfalar arka planda tamamlanır.
    # Yenilediğinizde daha fazla maç görünür (partial=true bu durumu belirtir).
    day_keys = []
    cursor = start_time
    while cursor <= end_time:
        chunk_end = min(cursor + 24 * 60 * 60 - 1, end_time)
        day_keys.append((cursor, chunk_end))
        cursor = chunk_end + 1

    deadline = time.monotonic() + WEEK_BUDGET_SECONDS
    try:
        for day in day_keys:
            _week_fill_day(day, status, per_page, deadline, refresh=False)
    except FootballAPIError as exc:
        logger.exception("FootballAPIError: %s", exc)
        if not any(_WEEK_CACHE.get((d, status), {}).get("rows") for d in day_keys):
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    pending = [
        d for d in day_keys
        if not _WEEK_CACHE.get((d, status), {}).get("complete")
        or time.time() - _WEEK_CACHE[(d, status)]["ts"] > _WEEK_TTL
    ]
    if pending:
        _week_start_background(pending, status, per_page)

    unique: Dict[str, Dict[str, Any]] = {}
    for day in day_keys:
        for fixture in _WEEK_CACHE.get((day, status), {}).get("rows", []):
            fixture_id = fixture.get("id") or fixture.get("fixture_id")
            if fixture_id is not None:
                unique[str(fixture_id)] = fixture
    still_filling = [d for d in day_keys if not _WEEK_CACHE.get((d, status), {}).get("complete")]
    return {"data": list(unique.values()), "count": len(unique), "partial": bool(still_filling)}


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


_ANALYSIS_CACHE: Dict[Any, Any] = {}
_ANALYSIS_TTL = 30 * 60


@app.on_event("startup")
def _warm_on_startup() -> None:
    """Uyanır uyanmaz havuzu (Supabase'ten) ve hafta listesini hazırla: ilk tıklama beklemesin."""
    try:
        _history_pool()  # kurucu diskten + Supabase'ten yüklemeyi arka planda başlatır
    except Exception as exc:
        logger.warning("havuz başlangıç yüklemesi başarısız: %s", exc)
    threading.Thread(target=_week_warm_loop, name="week-warm", daemon=True).start()

    def _refresh_history() -> None:
        # Supabase yüklemesi bitince havuz bayatsa (son maçlar eksikse) artımlı yenilemeyi başlat.
        try:
            pool = _history_pool()
            pool._db_ready.wait(timeout=300)
            pool.ensure_started(CONFIG.historical_days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("havuz yenileme başlatılamadı: %s", exc)

    threading.Thread(target=_refresh_history, name="history-refresh", daemon=True).start()


@app.get("/analyze/{fixture_id}")
def analyze(
    fixture_id: int,
    days: int = Query(default=CONFIG.historical_days, ge=1, le=730),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    """Gerçek benzer tarihsel maçlar + 5 havuzlu kanıt motoru + AI notu."""
    cache_key = (fixture_id, days, limit)
    hit = _ANALYSIS_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _ANALYSIS_TTL:
        return hit[1]
    result = _analyze_uncached(fixture_id, days, limit)
    _ANALYSIS_CACHE[cache_key] = (time.time(), result)
    if len(_ANALYSIS_CACHE) > 200:
        for k in sorted(_ANALYSIS_CACHE, key=lambda k: _ANALYSIS_CACHE[k][0])[:50]:
            _ANALYSIS_CACHE.pop(k, None)
    return result


def _analyze_uncached(fixture_id: int, days: int, limit: int) -> Dict[str, Any]:
    try:
        current = _current_match(fixture_id)
        # Takım formu (2 API çağrısı) ile havuz hazırlığı paralel yürür.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool_exec:
            forms_future = pool_exec.submit(_team_history_summary, current)
            historical = _historical_matches(days, current)
            forms = forms_future.result()
        similar = find_similar_matches(
            current,
            historical,
            display_limit=limit,
        )

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
            "history_status": _history_pool().status,
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
