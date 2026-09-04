import os
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import HTTPException

BASE = os.getenv("FIVE_DOLLAR_BASE_URL", "https://api.5dollarfootballapi.com/v1").rstrip("/")
KEY = os.getenv("FIVE_DOLLAR_API_KEY")


def _headers():
    if not KEY:
        raise HTTPException(status_code=500, detail="FIVE_DOLLAR_API_KEY environment variable bulunamadı.")
    return {"Authorization": f"Bearer {KEY}", "Accept": "application/json"}


async def _get(path: str, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.get(f"{BASE}/{path.lstrip('/')}", headers=_headers(), params=params or {})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"5DollarFootballAPI bağlantı hatası: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"5DollarFootballAPI isteği başarısız oldu ({response.status_code}): {response.text[:500]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="5DollarFootballAPI geçerli JSON döndürmedi.") from exc
    if payload.get("success") != 1:
        raise HTTPException(status_code=502, detail=f"5DollarFootballAPI hatası: {payload}")
    return payload


def _day_window(date: str | None):
    if date:
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)
        day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(day.timestamp()), int((day + timedelta(days=1)).timestamp())


def _status(value):
    value = str(value or "").lower()
    if value in {"live", "inplay", "in_play"}:
        return "live"
    if value in {"finished", "ft", "aet", "pen"}:
        return "finished"
    if value in {"postponed", "canceled", "cancelled", "abandoned"}:
        return "canceled"
    return "scheduled"


def _fixture_row(f):
    league = f.get("league") or {}
    teams = f.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    goals = f.get("goals") or {}
    return {
        "MatchID": str(f.get("id")),
        "matchID": str(f.get("id")),
        "id": str(f.get("id")),
        "Date": f.get("kickoff_utc") or "",
        "DateTime": f.get("kickoff_utc") or "",
        "Time": f.get("kickoff_utc") or "",
        "Country": league.get("country") or "",
        "League": league.get("name") or "",
        "LeagueID": league.get("id"),
        "Teams": f"{home.get('name', '')} - {away.get('name', '')}".strip(" -"),
        "Team1": home.get("name") or "",
        "Team2": away.get("name") or "",
        "HomeTeamID": home.get("id"),
        "AwayTeamID": away.get("id"),
        "HomeTeamLogo": home.get("logo"),
        "AwayTeamLogo": away.get("logo"),
        "Status": _status(f.get("status")),
        "KickoffUTC": f.get("kickoff_utc"),
        "Score": {
            "home": goals.get("home"),
            "away": goals.get("away"),
            "halftimeHome": goals.get("half_home"),
            "halftimeAway": goals.get("half_away"),
        },
    }


MARKET_NAMES = {
    "1x2": "Maç Sonucu 1X2",
    "asian_handicap": "Asya Handikap",
    "goal_line": "Alt/Üst Gol",
    "corner_line": "Alt/Üst Korner",
    "corner_asian": "Korner Asya Handikap",
    "card_line": "Alt/Üst Kart",
    "card_asian": "Kart Asya Handikap",
    "asian_handicap_half": "İlk Yarı Asya Handikap",
    "goal_line_half": "İlk Yarı Alt/Üst Gol",
    "corner_line_half": "İlk Yarı Alt/Üst Korner",
    "btts": "Karşılıklı Gol (KG)",
}


def _stage(entry, live=False):
    if not isinstance(entry, dict):
        return None
    if live and isinstance(entry.get("inplay"), dict):
        return entry["inplay"]
    for key in ("closing", "current", "opening"):
        value = entry.get(key)
        if isinstance(value, dict):
            return value
    return None


def _add_market(markets, name, market_type, values):
    clean = []
    for value, odd in values:
        if value in (None, ""):
            continue
        try:
            odd = float(odd)
        except (TypeError, ValueError):
            continue
        if odd > 0:
            clean.append({"value": str(value), "odd": odd})
    if clean:
        markets.append({"gameName": name, "type": str(market_type), "odds": clean})


def _markets_from_odds(odds_payload, live=False):
    markets = []
    data = odds_payload.get("data") or {}
    for bookmaker in data.get("bookmakers", []):
        book_name = bookmaker.get("name") or "Bet 365"
        odds = bookmaker.get("odds") or {}
        for key, entry in odds.items():
            stage = _stage(entry, live=live)
            if not stage:
                continue
            display = MARKET_NAMES.get(key)
            if not display:
                continue
            name = f"{display} ({book_name})"
            if key == "1x2":
                _add_market(markets, name, "1x2", [("1", stage.get("home")), ("X", stage.get("draw")), ("2", stage.get("away"))])
            elif key in {"goal_line", "goal_line_half"}:
                line = stage.get("line")
                _add_market(markets, name, key, [(f"Üst {line}", stage.get("over")), (f"Alt {line}", stage.get("under"))])
            elif key in {"corner_line", "corner_line_half", "card_line"}:
                line = stage.get("line")
                _add_market(markets, name, key, [(f"Üst {line}", stage.get("over")), (f"Alt {line}", stage.get("under"))])
            elif key == "btts":
                _add_market(markets, name, key, [("Var", stage.get("yes")), ("Yok", stage.get("no"))])
            elif key in {"asian_handicap", "asian_handicap_half", "corner_asian", "card_asian"}:
                line = stage.get("line")
                _add_market(markets, name, key, [(f"Ev {line}", stage.get("home")), (f"Dep {line}", stage.get("away"))])
    return markets


async def get_matches(date=None):
    start, end = _day_window(date)
    payload = await _get("fixtures", {"start_time": start, "end_time": end, "status": "all", "lang": "tr"})
    rows = [_fixture_row(x) for x in (payload.get("data") or [])]
    return {"data": rows, "source": "5dollarfootballapi", "cache": {"hit": False}, "live": {"count": sum(x["Status"] == "live" for x in rows), "source": "5dollarfootballapi"}}


async def get_match_detail(match_id: int):
    fixture_payload = await _get(f"fixtures/{match_id}", {"lang": "tr"})
    fixture = fixture_payload.get("data") or {}
    if not fixture:
        raise HTTPException(status_code=404, detail="Maç bulunamadı.")
    row = _fixture_row(fixture)
    live = row.get("Status") == "live"
    odds_payload = await _get(f"fixtures/{match_id}/odds", {"bookmakers": "bet365", "lang": "tr"})
    markets = _markets_from_odds(odds_payload, live=live)
    return {
        "fixture": fixture,
        "match": {
            "id": row["id"],
            "kickoff": row["KickoffUTC"],
            "status": row["Status"],
            "league": {"id": str(row.get("LeagueID") or ""), "name": row.get("League") or "", "country": row.get("Country") or ""},
            "homeTeam": {"id": str(row.get("HomeTeamID") or ""), "name": row.get("Team1") or "", "logoUrl": row.get("HomeTeamLogo")},
            "awayTeam": {"id": str(row.get("AwayTeamID") or ""), "name": row.get("Team2") or "", "logoUrl": row.get("AwayTeamLogo")},
            "score": row["Score"],
        },
        "markets": markets,
        "source": "5dollarfootballapi",
        "odds_cache": "5dollarfootballapi",
        "prediction": None,
        "prediction_cache": "unavailable",
    }


def patch_main(m):
    if not KEY:
        return
    m.get_matches = get_matches
    m.get_match_detail = get_match_detail
    m.get_match_detail_alias = get_match_detail
    m.nosy_get = None

    async def inspect_match(row):
        key = str(row.get("MatchID") or row.get("matchID") or row.get("id") or "")
        if not key:
            return None
        try:
            detail = await get_match_detail(int(key))
        except Exception:
            return None
        markets = detail.get("markets", [])
        iyms = next((x for x in markets if "iy/ms" in x.get("gameName", "").lower() or "ilk yarı/maç sonucu" in x.get("gameName", "").lower()), None)
        return {
            "match": m.slim_match(row),
            "markets": m.market_payload(markets),
            "iyms_market_open": bool(iyms),
            "iyms": m.parse_iyms_market(iyms) if iyms else None,
        }

    m.inspect_match = inspect_match
    m.app.router.routes = [
        r for r in m.app.router.routes
        if getattr(r, "path", None) not in ["/matches", "/mac/{match_id}", "/match/{match_id}"]
    ]
    m.app.add_api_route("/matches", get_matches, methods=["GET"])
    m.app.add_api_route("/mac/{match_id}", get_match_detail, methods=["GET"])
    m.app.add_api_route("/match/{match_id}", get_match_detail, methods=["GET"])
