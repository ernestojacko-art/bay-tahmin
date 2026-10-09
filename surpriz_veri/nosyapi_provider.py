"""
SÜRPRİZ VERİ - NosyAPI Data Provider

5DollarFootballAPI'nin aylık ücretli hesabı yenilenene kadar geçici
yedek veri sağlayıcısı. NosyAPI'nin ücretsiz/aylık kota ile çalışan
"İddaa Oranları ve Programı" + "İddaa Maç Sonuçları" servislerini
kullanır.

Önemli:
    Bu modül SADECE Sürpriz Veri (surpriz_veri/) servisi içindir.
    Ana Bay Tahmin backend'i (backend/) hâlâ 5DollarFootballAPI
    kullanır ve bu dosyadan etkilenmez.

NosyAPI dokümantasyonu (www.nosyapi.com/api/iddaa-oranlari-ve-programi-api
ve www.nosyapi.com/api/iddaa-oranlari-ve-programi-sonuclari-api) genel
kamuya açık sayfalardan çıkarılmıştır; NosyAPI resmi, makine-okunur bir
OpenAPI şeması yayımlamaz. Bu yüzden istemci mümkün olduğunca savunmacı
(defensive) yazılmıştır: beklenmeyen alanlar sessizce yok sayılır, hiçbir
veri uydurulmaz.

Kredi modeli: NosyAPI'de kredi, dönen KAYIT SAYISI kadar düşer (çağrı
başına değil). Bu nedenle tarihsel tarama burada kasıtlı olarak dar
tutulmuştur (bkz. NOSYAPI_HISTORICAL_DAY_SCAN_CAP).
"""

from __future__ import annotations

import os
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from .data_provider import FootballAPIError

ISTANBUL = ZoneInfo("Europe/Istanbul")

DEFAULT_BASE_URL = "https://www.nosyapi.com/apiv2/service"

# Render ortam değişkeni adı tam olarak bilinmediği için birden çok
# yaygın isim denenir; Render panelinde hangisi tanımlıysa o kullanılır.
_API_KEY_ENV_NAMES = (
    "NOSYAPI_API_KEY",
    "NOSY_API_KEY",
    "NOSYAPI_KEY",
    "NOSY_APIKEY",
    "NOSYAPI_APIKEY",
    "NOSYAPI_TOKEN",
    "NOSY_API_TOKEN",
)


def _resolve_api_key(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    for name in _API_KEY_ENV_NAMES:
        value = os.getenv(name)
        if value:
            return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_kickoff_utc(row: Dict[str, Any]) -> Optional[str]:
    """NosyAPI Date/Time alanlarını (Europe/Istanbul yerel saat) UTC ISO'ya çevirir."""

    date_str = row.get("Date")
    time_str = row.get("Time") or "00:00:00"
    date_time_str = row.get("DateTime")

    raw = None
    if date_str:
        raw = f"{date_str} {time_str}"
    elif date_time_str:
        raw = str(date_time_str)

    if not raw:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            local_dt = datetime.strptime(raw.strip(), fmt).replace(tzinfo=ISTANBUL)
            return local_dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return None


def _row_to_fixture(row: Dict[str, Any], finished: bool = False) -> Dict[str, Any]:
    """NosyAPI'nin düz (flat) maç satırını Sürpriz Veri'nin beklediği
    iç içe fixture şemasına çevirir (bkz. data_mapper.map_fixture_to_match_record).
    """

    match_id = row.get("MatchID") or row.get("matchID")
    team1 = row.get("Team1")
    team2 = row.get("Team2")
    league_name = row.get("League")
    country = row.get("Country")
    league_code = row.get("LeagueCode")

    home_win = _to_float(row.get("HomeWin"))
    draw = _to_float(row.get("Draw"))
    away_win = _to_float(row.get("AwayWin"))

    odds_block: Dict[str, Any] = {}
    if home_win is not None or draw is not None or away_win is not None:
        stage = "closing" if finished else "current"
        odds_block["1x2"] = {
            stage: {
                "home": home_win,
                "draw": draw,
                "away": away_win,
            }
        }

    goals: Dict[str, Any] = {}
    if finished:
        for item in row.get("matchResult") or []:
            if not isinstance(item, dict):
                continue
            meta = item.get("metaName")
            value = _to_int(item.get("value"))
            if meta == "msHomeScore":
                goals["home"] = value
            elif meta == "msAwayScore":
                goals["away"] = value
            elif meta == "htHomeScore":
                goals["half_home"] = value
            elif meta == "htAwayScore":
                goals["half_away"] = value

    return {
        "id": match_id,
        "teams": {
            "home": {"name": team1},
            "away": {"name": team2},
        },
        "kickoff_utc": _parse_kickoff_utc(row),
        "league": {
            "name": league_name,
            "country": country,
            "code": league_code,
        },
        "odds": odds_block,
        "goals": goals,
        "status": "finished" if finished else "scheduled",
        "_nosyapi_raw": row,
    }


class NosyAPIClient:
    """NosyAPI için FiveDollarFootballAPI ile aynı arayüzü sunan istemci.

    api.py'deki tüm çağıranlar (fixtures/fixture/fixture_odds/leagues/
    league_fixtures/team_fixtures/bookmakers/flatten_fixture_list) bu
    sınıfı FiveDollarFootballAPI'nin yerine şeffaf biçimde kullanabilir.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
    ):
        self.api_key = _resolve_api_key(api_key)
        self.base_url = (base_url or os.getenv("NOSYAPI_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout = timeout
        self._request_times: deque = deque(maxlen=30)
        # leagues() tarafından doldurulur; league_fixtures() sentetik
        # id -> lig adı çözümlemesi için kullanır (NosyAPI'de program
        # endpoint'i lig adıyla, sonuç endpoint'i tarihle sorgulanır).
        self._league_name_by_id: Dict[int, str] = {}

        if not self.api_key:
            raise FootballAPIError(
                "NosyAPI anahtarı bulunamadı. Render'da şu ortam "
                "değişkenlerinden birini tanımlayın: "
                + ", ".join(_API_KEY_ENV_NAMES)
            )

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        request_params = dict(params or {})
        request_params["apiKey"] = self.api_key

        now = time.monotonic()
        recent = [t for t in self._request_times if now - t < 60]
        self._request_times.clear()
        self._request_times.extend(recent)
        if len(recent) >= 25:
            time.sleep(max(0.0, 60.0 - (now - recent[0]) + 0.25))

        response = None
        for attempt in range(3):
            self._request_times.append(time.monotonic())
            response = requests.get(url, params=request_params, timeout=self.timeout)
            if response.status_code == 429 and attempt < 2:
                time.sleep(3.0)
                continue
            break

        if response is None:
            raise FootballAPIError("NosyAPI'ye ulaşılamadı.")

        if response.status_code >= 400:
            raise FootballAPIError(f"NosyAPI HTTP {response.status_code}: {response.text[:500]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise FootballAPIError("NosyAPI geçerli JSON döndürmedi.") from exc

        if isinstance(payload, dict) and payload.get("status") not in (None, "success"):
            raise FootballAPIError(str(payload.get("messageTR") or payload.get("message") or payload))

        return payload if isinstance(payload, dict) else {"data": payload}

    # ------------------------------------------------------------
    # PROGRAM (gelecek/güncel maçlar)
    # ------------------------------------------------------------

    def fixtures(
        self,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        status: str = "all",
        include: str = "odds,events,stats",
        page: int = 1,
        per_page: int = 100,
    ) -> Dict[str, Any]:
        """Belirtilen zaman aralığındaki (epoch saniye, UTC) maçları getirir.

        NosyAPI programı tek günlük `date` parametresiyle sorgulanır; bu
        yüzden aralık gün gün taranıp birleştirilir.
        """

        now_utc = datetime.now(timezone.utc)
        start_dt = (
            datetime.fromtimestamp(start_time, tz=timezone.utc) if start_time else now_utc
        )
        end_dt = (
            datetime.fromtimestamp(end_time, tz=timezone.utc)
            if end_time
            else now_utc + timedelta(days=1)
        )

        start_local_day = start_dt.astimezone(ISTANBUL).date()
        end_local_day = end_dt.astimezone(ISTANBUL).date()
        if end_local_day < start_local_day:
            end_local_day = start_local_day

        day_count = (end_local_day - start_local_day).days + 1
        day_count = min(day_count, 8)  # /fixtures/week zaten 7 günle sınırlı

        rows: List[Dict[str, Any]] = []
        for offset in range(day_count):
            day = start_local_day + timedelta(days=offset)
            try:
                payload = self._get(
                    "bettable-matches",
                    params={"type": 1, "date": day.isoformat()},
                )
            except FootballAPIError:
                continue
            data = payload.get("data")
            if isinstance(data, list):
                rows.extend(r for r in data if isinstance(r, dict))

        fixtures = [_row_to_fixture(row, finished=False) for row in rows]

        # start_time/end_time ile tam saat filtrelemesi (gün bazlı çekim
        # bazen pencerenin dışında kalan maçları da getirebilir).
        if start_time is not None or end_time is not None:
            filtered = []
            for fx in fixtures:
                kickoff = fx.get("kickoff_utc")
                if not kickoff:
                    continue
                try:
                    ts = datetime.fromisoformat(kickoff).timestamp()
                except ValueError:
                    continue
                if start_time is not None and ts < start_time:
                    continue
                if end_time is not None and ts > end_time:
                    continue
                filtered.append(fx)
            fixtures = filtered

        return {
            "success": 1,
            "data": fixtures,
            "pagination": {
                "page": 1,
                "per_page": len(fixtures),
                "count": len(fixtures),
                "has_more": False,
            },
            "provider": "nosyapi",
        }

    def fixture(self, fixture_id: int, include: str = "events,stats") -> Dict[str, Any]:
        payload = self._get("bettable-matches/details", params={"matchID": fixture_id})
        rows = payload.get("data")
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
        if not isinstance(row, dict):
            raise FootballAPIError("NosyAPI maç detayı bulunamadı.")

        fixture = _row_to_fixture(row, finished=False)

        # bettable-matches/details, "gameType" -> "Bets" altında 1X2 dışı
        # marketleri de verebilir; en azından pazar sayısını zenginleştir.
        game_types = row.get("gameType")
        if isinstance(game_types, list):
            extra_markets = {}
            for gt in game_types:
                if not isinstance(gt, dict):
                    continue
                name = gt.get("gameName") or gt.get("type")
                if name:
                    extra_markets[str(name)] = gt
            if extra_markets:
                fixture.setdefault("odds", {}).update(
                    {k: v for k, v in extra_markets.items() if k not in fixture["odds"]}
                )

        return {"data": fixture}

    def fixture_odds(self, fixture_id: int, bookmaker: str = "iddaa") -> Dict[str, Any]:
        detail = self.fixture(fixture_id)
        fixture = detail.get("data", {})
        odds = fixture.get("odds") if isinstance(fixture, dict) else None
        return {"data": {"odds": odds or {}}}

    def fixture_odds_history(
        self,
        fixture_id: int,
        market: str = "1x2",
        bookmaker: str = "iddaa",
        page: int = 1,
        per_page: int = 500,
    ) -> Dict[str, Any]:
        # NosyAPI, oran hareket geçmişi (opening->closing tick history)
        # sunan belgelenmiş bir endpoint sağlamıyor. Boş döner; data_mapper
        # bu durumda "current" oranı yaklaşık kapanış olarak işaretler
        # (closing_is_live_fallback=True), hiçbir şey uydurulmaz.
        return {"data": []}

    # ------------------------------------------------------------
    # LİGLER
    # ------------------------------------------------------------

    def leagues(
        self,
        popular: Optional[int] = None,
        active_since: Optional[int] = None,
        page: int = 1,
        per_page: int = 100,
    ) -> Dict[str, Any]:
        if page > 1:
            # NosyAPI lig listesi sayfalanmaz; tek seferde tüm liste döner.
            return {"data": []}

        payload = self._get("bettable-matches/league", params={"type": 1})
        data = payload.get("data")
        leagues: List[Dict[str, Any]] = []
        if isinstance(data, list):
            for idx, item in enumerate(data):
                if isinstance(item, dict):
                    name = item.get("League") or item.get("league") or item.get("name")
                elif isinstance(item, str):
                    name = item
                else:
                    continue
                if not name:
                    continue
                league_id = abs(hash(str(name))) % 10_000_000
                self._league_name_by_id[league_id] = str(name)
                leagues.append({"id": league_id, "name": str(name)})

        return {"data": leagues, "pagination": {"has_more": False}}

    def league_fixtures(
        self,
        league_id: int,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        status: str = "finished",
        include: str = "odds,events,stats",
        page: int = 1,
        per_page: int = 50,
        order: str = "desc",
    ) -> Dict[str, Any]:
        """Bir ligin tamamlanmış maçlarını NosyAPI'nin günlük sonuç
        servisinden (matches-result) tarar.

        NosyAPI kredisi dönen kayıt sayısı kadar düştüğünden ve bu
        servis tarih aralığı parametresi sunmadığından, tarama gün gün
        yapılır ve NOSYAPI_HISTORICAL_DAY_SCAN_CAP ile sert şekilde
        sınırlandırılır (varsayılan 10 gün).
        """

        league_name = self._league_name_by_id.get(int(league_id))
        if not league_name:
            return {"data": []}

        now_utc = datetime.now(timezone.utc)
        end_dt = datetime.fromtimestamp(end_time, tz=timezone.utc) if end_time else now_utc
        start_dt = (
            datetime.fromtimestamp(start_time, tz=timezone.utc)
            if start_time
            else end_dt - timedelta(days=10)
        )

        day_cap = int(os.getenv("NOSYAPI_HISTORICAL_DAY_SCAN_CAP", "10"))
        end_day = end_dt.astimezone(ISTANBUL).date()
        start_day = start_dt.astimezone(ISTANBUL).date()
        total_days = (end_day - start_day).days + 1
        days_to_scan = min(total_days, day_cap)

        matches: List[Dict[str, Any]] = []
        for offset in range(days_to_scan):
            day = end_day - timedelta(days=offset)
            try:
                payload = self._get(
                    "matches-result",
                    params={"type": 1, "date": day.isoformat()},
                )
            except FootballAPIError:
                continue
            data = payload.get("data")
            if not isinstance(data, list):
                continue
            for row in data:
                if not isinstance(row, dict):
                    continue
                row_league = str(row.get("League") or "")
                if league_name.strip().lower() not in row_league.strip().lower():
                    continue
                matches.append(_row_to_fixture(row, finished=True))
            if len(matches) >= per_page:
                break

        return {"data": matches[:per_page], "pagination": {"has_more": False}}

    def team_fixtures(
        self,
        team_id: int,
        status: str = "finished",
        include: str = "stats",
        page: int = 1,
        per_page: int = 20,
    ) -> Dict[str, Any]:
        # NosyAPI takım bazlı geçmiş maç sorgusu sunmuyor. Boş liste
        # döner; takım formu bu durumda "veri yok" olarak ele alınır,
        # hiçbir şey uydurulmaz.
        return {"data": []}

    def bookmakers(self) -> Dict[str, Any]:
        return {"data": [{"id": "iddaa", "name": "İddaa"}]}

    def flatten_fixture_list(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = payload.get("data", [])
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("fixtures", "items", "results", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []
