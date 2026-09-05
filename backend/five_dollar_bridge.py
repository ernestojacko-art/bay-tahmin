import asyncio
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

BASE = os.getenv("FIVE_DOLLAR_BASE_URL", "https://api.5dollarfootballapi.com/v1").rstrip("/")
KEY = os.getenv("FIVE_DOLLAR_API_KEY")

_MATCH_CACHE = {}
_MATCH_CACHE_TTL_SECONDS = 15 * 60
_MATCH_STALE_TTL_SECONDS = 6 * 60 * 60
_FIXTURE_DETAIL_CACHE = {}
_FIXTURE_DETAIL_TTL_SECONDS = 5 * 60

# Production resilience: successful GET responses are reused across the engine so
# one page load does not fan out into repeated upstream requests. Concurrent
# identical requests are coalesced into one upstream call.
_API_CACHE = {}
_API_CACHE_TTLS = {
    "fixtures": 10 * 60,
    "leagues/": 6 * 60 * 60,
    "teams/": 6 * 60 * 60,
    "fixtures/": 5 * 60,
}
_INFLIGHT = {}
_RATE_LIMIT_UNTIL = 0.0


def _headers():
    if not KEY:
        raise HTTPException(status_code=500, detail="FIVE_DOLLAR_API_KEY environment variable bulunamadı.")
    return {"Authorization": f"Bearer {KEY}", "Accept": "application/json"}


def _cache_ttl(path: str) -> int:
    if path.startswith("fixtures/") and path.count("/") == 1:
        return _API_CACHE_TTLS["fixtures/"]
    if path.startswith("teams/"):
        return _API_CACHE_TTLS["teams/"]
    if path.startswith("leagues/"):
        return _API_CACHE_TTLS["leagues/"]
    if path == "fixtures":
        return _API_CACHE_TTLS["fixtures"]
    return 5 * 60


def _cache_key(path: str, params: dict | None):
    return (path, tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())))


def _cached(cache_key):
    item = _API_CACHE.get(cache_key)
    if not item:
        return None
    return item


async def _paced_request(url: str, params: dict):
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
        return await client.get(url, headers=_headers(), params=params)


async def _get(path: str, params: dict | None = None, *, retries: int = 0):
    global _RATE_LIMIT_UNTIL
    cache_key = _cache_key(path, params)
    now = datetime.now(timezone.utc).timestamp()
    item = _cached(cache_key)
    ttl = _cache_ttl(path)
    if item and now - item[0] < ttl:
        return item[1]

    # If another coroutine is already fetching exactly this resource, wait for
    # that result instead of spending another upstream request.
    existing = _INFLIGHT.get(cache_key)
    if existing is not None:
        return await existing

    if now < _RATE_LIMIT_UNTIL:
        if item:
            return item[1]
        raise HTTPException(
            status_code=503,
            detail="Canlı veri sağlayıcısı geçici olarak yoğun. Güvenilir olmayan veya uydurma veri göstermek yerine bu isteği güvenli biçimde durdurdum.",
        )

    async def fetch():
        global _RATE_LIMIT_UNTIL
        last_error = None
        try:
            for attempt in range(retries + 1):
                try:
                    response = await _paced_request(f"{BASE}/{path.lstrip('/')}", params or {})
                except httpx.HTTPError as exc:
                    last_error = exc
                    if attempt < retries:
                        await asyncio.sleep(0.5)
                        continue
                    raise HTTPException(status_code=502, detail=f"5DollarFootballAPI bağlantı hatası: {exc}") from exc

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = max(1, int(float(retry_after))) if retry_after else 60
                    except (TypeError, ValueError):
                        wait = 60
                    _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, datetime.now(timezone.utc).timestamp() + wait)
                    stale = _cached(cache_key)
                    if stale:
                        return stale[1]
                    raise HTTPException(
                        status_code=503,
                        detail="Canlı veri sağlayıcısı geçici olarak yoğun. Güvenilir olmayan veya uydurma veri göstermek yerine bu isteği güvenli biçimde durdurdum.",
                    )

                if response.status_code in {500, 502, 503, 504} and attempt < retries:
                    await asyncio.sleep(0.5)
                    continue

                if response.status_code != 200:
                    raise HTTPException(status_code=502, detail=f"5DollarFootballAPI isteği başarısız oldu ({response.status_code}): {response.text[:500]}")
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="5DollarFootballAPI geçerli JSON döndürmedi.") from exc
                if payload.get("success") != 1:
                    raise HTTPException(status_code=502, detail=f"5DollarFootballAPI hatası: {payload}")

                _API_CACHE[cache_key] = (datetime.now(timezone.utc).timestamp(), payload)
                if path.startswith("fixtures/") and path.count("/") == 1:
                    _FIXTURE_DETAIL_CACHE[cache_key] = _API_CACHE[cache_key]
                return payload
        finally:
            _INFLIGHT.pop(cache_key, None)

    task = asyncio.create_task(fetch())
    _INFLIGHT[cache_key] = task
    try:
        return await task
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"5DollarFootballAPI bağlantısı başarısız: {last_error or exc}") from exc


async def _get_all(path: str, params: dict | None = None):
    """Fetch every page from a paginated list endpoint."""
    base_params = dict(params or {})
    page = 1
    rows = []
    while True:
        page_params = dict(base_params)
        page_params["page"] = page
        payload = await _get(path, page_params)
        rows.extend(payload.get("data") or [])
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_more"):
            return rows
        page += 1
        if page > 100:
            raise HTTPException(status_code=502, detail="5DollarFootballAPI sayfalama limiti aşıldı.")


def _day_window(date: str | None):
    tz = ZoneInfo("Europe/Istanbul")
    if date:
        day = datetime.fromisoformat(date).replace(tzinfo=tz)
    else:
        now = datetime.now(tz)
        day = datetime(now.year, now.month, now.day, tzinfo=tz)
    start = day.astimezone(timezone.utc)
    end = (day + timedelta(days=1)).astimezone(timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def _status(value):
    value = str(value or "").lower()
    if value in {"live", "inplay", "in_play"}: return "live"
    if value in {"finished", "ft", "aet", "pen"}: return "finished"
    if value in {"postponed", "canceled", "cancelled", "abandoned"}: return "canceled"
    return "scheduled"


def _fixture_row(f):
    league = f.get("league") or {}
    teams = f.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    goals = f.get("goals") or {}
    return {
        "MatchID": str(f.get("id")), "matchID": str(f.get("id")), "id": str(f.get("id")),
        "Date": f.get("kickoff_utc") or "", "DateTime": f.get("kickoff_utc") or "", "Time": f.get("kickoff_utc") or "",
        "Country": league.get("country") or "", "League": league.get("name") or "", "LeagueID": league.get("id"),
        "Teams": f"{home.get('name', '')} - {away.get('name', '')}".strip(" -"),
        "Team1": home.get("name") or "", "Team2": away.get("name") or "",
        "HomeTeamID": home.get("id"), "AwayTeamID": away.get("id"),
        "HomeTeamLogo": home.get("logo"), "AwayTeamLogo": away.get("logo"),
        "Status": _status(f.get("status")), "KickoffUTC": f.get("kickoff_utc"),
        "Score": {"home": goals.get("home"), "away": goals.get("away"), "halftimeHome": goals.get("half_home"), "halftimeAway": goals.get("half_away")},
    }


KEY_ALIASES = {
    "asian": "asian_handicap", "goalline": "goal_line", "corner": "corner_line", "cards": "card_line",
    "cards_asian": "card_asian", "asian_half": "asian_handicap_half", "goalline_half": "goal_line_half",
    "corner_half": "corner_line_half", "corner_asian": "corner_asian", "btts": "btts", "1x2_half": "1x2_half",
}

MARKET_NAMES = {
    "1x2": "Maç Sonucu 1X2", "1x2_half": "İlk Yarı Maç Sonucu",
    "asian_handicap": "Asya Handikap", "goal_line": "Alt/Üst Gol", "corner_line": "Alt/Üst Korner",
    "corner_asian": "Korner Asya Handikap", "card_line": "Alt/Üst Kart", "card_asian": "Kart Asya Handikap",
    "asian_handicap_half": "İlk Yarı Asya Handikap", "goal_line_half": "İlk Yarı Alt/Üst Gol",
    "corner_line_half": "İlk Yarı Alt/Üst Korner", "btts": "Karşılıklı Gol (KG)",
}


def _stage(entry, live=False):
    if not isinstance(entry, dict): return None
    if live and isinstance(entry.get("inplay"), dict): return entry["inplay"]
    for key in ("closing", "current", "opening"):
        value = entry.get(key)
        if isinstance(value, dict): return value
    return None


def _add_market(markets, name, market_type, values):
    clean = []
    for value, odd in values:
        if value in (None, ""): continue
        try: odd = float(odd)
        except (TypeError, ValueError): continue
        if odd > 0: clean.append({"value": str(value), "odd": odd})
    if clean: markets.append({"gameName": name, "type": str(market_type), "odds": clean})


def _markets_from_odds(odds_payload, live=False):
    markets = []
    data = odds_payload.get("data") or {}
    bookmaker_entries = []
    raw_odds = data.get("odds")

    if isinstance(raw_odds, dict):
        bookmaker_entries.append(("Bet 365", raw_odds))
    elif isinstance(raw_odds, list):
        for bookmaker in raw_odds:
            if not isinstance(bookmaker, dict): continue
            odds = bookmaker.get("odds") or {}
            if isinstance(odds, dict): bookmaker_entries.append((bookmaker.get("name") or "Bet 365", odds))

    if isinstance(data.get("bookmakers"), list):
        for bookmaker in data["bookmakers"]:
            if isinstance(bookmaker, dict):
                odds = bookmaker.get("odds") or {}
                if isinstance(odds, dict): bookmaker_entries.append((bookmaker.get("name") or "Bet 365", odds))

    if not bookmaker_entries and isinstance(odds_payload.get("odds"), list):
        for bookmaker in odds_payload["odds"]:
            if isinstance(bookmaker, dict) and isinstance(bookmaker.get("odds"), dict): bookmaker_entries.append((bookmaker.get("name") or "Bet 365", bookmaker["odds"]))

    seen_markets = set()
    for book_name, odds in bookmaker_entries:
        for raw_key, entry in odds.items():
            key = KEY_ALIASES.get(raw_key, raw_key)
            stage = _stage(entry, live=live)
            if not stage: continue
            display = MARKET_NAMES.get(key)
            if not display: continue
            name = f"{display} ({book_name})"
            market_key = (book_name, key, str(stage.get("line")))
            if market_key in seen_markets: continue
            seen_markets.add(market_key)
            if key == "1x2": _add_market(markets, name, "1x2", [("1", stage.get("home")), ("X", stage.get("draw")), ("2", stage.get("away"))])
            elif key == "1x2_half": _add_market(markets, name, "1x2_half", [("1", stage.get("home")), ("X", stage.get("draw")), ("2", stage.get("away"))])
            elif key in {"goal_line", "goal_line_half", "corner_line", "corner_line_half", "card_line"}:
                line = stage.get("line"); _add_market(markets, name, key, [(f"Üst {line}", stage.get("over")), (f"Alt {line}", stage.get("under"))])
            elif key == "btts": _add_market(markets, name, key, [("Var", stage.get("yes")), ("Yok", stage.get("no"))])
            elif key in {"asian_handicap", "asian_handicap_half", "corner_asian", "card_asian"}:
                line = stage.get("line"); _add_market(markets, name, key, [(f"Ev {line}", stage.get("home")), (f"Dep {line}", stage.get("away"))])
    return markets


async def get_matches(date=None):
    cache_key = str(date or datetime.now(ZoneInfo("Europe/Istanbul")).date())
    now_ts = datetime.now(timezone.utc).timestamp()
    cached = _MATCH_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < _MATCH_CACHE_TTL_SECONDS:
        result = dict(cached[1]); result["cache"] = {"hit": True}; return result
    start, end = _day_window(date)
    try:
        rows_raw = await _get_all("fixtures", {"start_time": start, "end_time": end, "status": "all", "lang": "en", "per_page": 100})
    except HTTPException:
        if cached and now_ts - cached[0] < _MATCH_STALE_TTL_SECONDS:
            result = dict(cached[1]); result["cache"] = {"hit": True, "stale": True}; return result
        raise
    rows = [_fixture_row(x) for x in rows_raw]
    result = {"data": rows, "source": "5dollarfootballapi", "cache": {"hit": False}, "live": {"count": sum(x["Status"] == "live" for x in rows), "source": "5dollarfootballapi"}}
    _MATCH_CACHE[cache_key] = (now_ts, result)
    return result


async def get_match_detail(match_id: int):
    fixture_payload = await _get(f"fixtures/{match_id}", {"lang": "en"})
    fixture = fixture_payload.get("data") or {}
    if not fixture: raise HTTPException(status_code=404, detail="Maç bulunamadı.")
    row = _fixture_row(fixture); live = row.get("Status") == "live"
    markets = _markets_from_odds({"data": {"odds": fixture.get("odds") or {}}}, live=live)
    return {
        "fixture": fixture,
        "match": {"id": row["id"], "kickoff": row["KickoffUTC"], "status": row["Status"],
                  "league": {"id": str(row.get("LeagueID") or ""), "name": row.get("League") or "", "country": row.get("Country") or ""},
                  "homeTeam": {"id": str(row.get("HomeTeamID") or ""), "name": row.get("Team1") or "", "logoUrl": row.get("HomeTeamLogo")},
                  "awayTeam": {"id": str(row.get("AwayTeamID") or ""), "name": row.get("Team2") or "", "logoUrl": row.get("AwayTeamLogo")}, "score": row["Score"]},
        "markets": markets, "source": "5dollarfootballapi", "odds_cache": "5dollarfootballapi",
        "prediction": None, "prediction_cache": "unavailable",
    }


def patch_main(m):
    if not KEY: return
    m.get_matches = get_matches; m.get_match_detail = get_match_detail; m.get_match_detail_alias = get_match_detail; m.nosy_get = None

    async def inspect_match(row):
        key = str(row.get("MatchID") or row.get("matchID") or row.get("id") or "")
        if not key: return None
        try: detail = await get_match_detail(int(key))
        except Exception: return None
        markets = detail.get("markets", [])
        iyms = next((x for x in markets if "iy/ms" in x.get("gameName", "").lower() or "ilk yarı/maç sonucu" in x.get("gameName", "").lower()), None)
        return {"match": m.slim_match(row), "markets": m.market_payload(markets), "iyms_market_open": bool(iyms), "iyms": m.parse_iyms_market(iyms) if iyms else None}

    m.inspect_match = inspect_match
    m.app.router.routes = [r for r in m.app.router.routes if getattr(r, "path", None) not in ["/matches", "/mac/{match_id}", "/match/{match_id}"]]
    m.app.add_api_route("/matches", get_matches, methods=["GET"])
    m.app.add_api_route("/mac/{match_id}", get_match_detail, methods=["GET"])
    m.app.add_api_route("/match/{match_id}", get_match_detail, methods=["GET"])
