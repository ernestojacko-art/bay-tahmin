"""
Generic REST Football Data Provider -- production adapter skeleton.

This is a REAL adapter (not a mock): it makes actual HTTP requests, sends
a real API key header, handles real HTTP/network failures, and maps a
real JSON response into the application's normalized models. It does
NOT fabricate any football data.

Because this project has not been told which specific vendor's API to
integrate (and this environment has no network access or API key to test
against), the JSON field-mapping functions (`_map_fixture`,
`_map_team_dataset`, etc.) use a documented, reasonably common REST
football-API response shape as a placeholder contract. Connecting a real
vendor is then a matter of:

  1. Setting `FOOTBALL_DATA_API_BASE_URL` and `FOOTBALL_DATA_API_KEY`
     (and the `FOOTBALL_DATA_*_PATH` endpoint paths, if different from
     the defaults) in the environment.
  2. Adjusting the small `_map_*` functions below to match that vendor's
     actual JSON field names. Nothing else in the application needs to
     change -- `DataIntelligenceLayer` and everything above it only ever
     sees the normalized models in `app/providers/models.py`.

If the API key / base URL are not configured, this provider raises
`ProviderNotConfiguredError` immediately (fail fast, never fabricate).
If a request fails (network error, non-2xx status, malformed payload),
it raises `ProviderError` -- callers must not treat that the same as
"this field is legitimately absent".
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.core.config import Settings
from app.core.exceptions import ProviderError, ProviderNotConfiguredError
from app.providers.base import BaseFootballDataProvider
from app.providers.models import (
    Fixture,
    FixtureCongestion,
    FixtureStatus,
    H2HRecord,
    MatchResult,
    OddsMarket,
    OddsSelection,
    RecentMatch,
    StandingsEntry,
    TeamRawDataset,
    TeamRef,
)

_STATUS_MAP = {
    "NS": FixtureStatus.SCHEDULED,
    "SCHEDULED": FixtureStatus.SCHEDULED,
    "LIVE": FixtureStatus.LIVE,
    "1H": FixtureStatus.LIVE,
    "2H": FixtureStatus.LIVE,
    "FT": FixtureStatus.FINISHED,
    "FINISHED": FixtureStatus.FINISHED,
    "PST": FixtureStatus.POSTPONED,
    "POSTPONED": FixtureStatus.POSTPONED,
}


def _map_status(raw_status: Optional[str]) -> FixtureStatus:
    if not raw_status:
        return FixtureStatus.SCHEDULED
    return _STATUS_MAP.get(raw_status.upper(), FixtureStatus.SCHEDULED)


def _map_team_ref(raw: dict[str, Any], league_id: Optional[str] = None) -> TeamRef:
    return TeamRef(
        team_id=str(raw.get("id") or raw.get("team_id")),
        name=raw.get("name") or raw.get("team_name") or "Unknown",
        league_id=league_id,
    )


def _map_fixture(raw: dict[str, Any]) -> Fixture:
    """
    Expected placeholder shape (adjust to real vendor response):
    {
      "id": "12345", "league_id": "L1", "league_name": "...",
      "kickoff": "2026-09-10T18:00:00Z",
      "home_team": {"id": "T1", "name": "..."},
      "away_team": {"id": "T2", "name": "..."},
      "status": "NS", "round": "Round 5"
    }
    """
    try:
        return Fixture(
            match_id=str(raw["id"]),
            league_id=str(raw.get("league_id", "")),
            league_name=raw.get("league_name"),
            kickoff=datetime.fromisoformat(str(raw["kickoff"]).replace("Z", "+00:00")),
            home_team=_map_team_ref(raw["home_team"], raw.get("league_id")),
            away_team=_map_team_ref(raw["away_team"], raw.get("league_id")),
            status=_map_status(raw.get("status")),
            round=raw.get("round"),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ProviderError(f"Malformed fixture payload from provider: {exc}") from exc


def _map_recent_match(raw: dict[str, Any], reference_team_id: str) -> RecentMatch:
    is_home = str(raw["home_team"]["id"]) == str(reference_team_id)
    home_goals = int(raw["home_goals"])
    away_goals = int(raw["away_goals"])
    goals_for = home_goals if is_home else away_goals
    goals_against = away_goals if is_home else home_goals
    opponent_raw = raw["away_team"] if is_home else raw["home_team"]

    if goals_for > goals_against:
        result = MatchResult.HOME_WIN if is_home else MatchResult.AWAY_WIN
    elif goals_for < goals_against:
        result = MatchResult.AWAY_WIN if is_home else MatchResult.HOME_WIN
    else:
        result = MatchResult.DRAW

    return RecentMatch(
        match_id=str(raw["id"]),
        date=date.fromisoformat(str(raw["date"])[:10]),
        is_home=is_home,
        opponent=_map_team_ref(opponent_raw),
        goals_for=goals_for,
        goals_against=goals_against,
        xg_for=raw.get("xg_for"),
        xg_against=raw.get("xg_against"),
        shots_for=raw.get("shots_for"),
        shots_on_target_for=raw.get("shots_on_target_for"),
        shots_against=raw.get("shots_against"),
        shots_on_target_against=raw.get("shots_on_target_against"),
        result=result,
        opponent_strength_hint=raw.get("opponent_strength_hint"),
    )


def _map_team_dataset(team_id: str, raw: dict[str, Any], league_id: Optional[str] = None) -> TeamRawDataset:
    """
    Expected placeholder shape:
    {
      "team": {"id": "T1", "name": "..."},
      "recent_matches": [ ... ],
      "standing": {"position": 3, "played": 20, "won": 12, "drawn": 4,
                    "lost": 4, "goals_for": 34, "goals_against": 18, "points": 40},
      "fixture_congestion": {"matches_last_14_days": 3, "days_since_last_match": 4,
                              "next_match_days_away": 6}
    }
    """
    try:
        team_ref = _map_team_ref(raw["team"], league_id)
        matches = [_map_recent_match(m, team_id) for m in raw.get("recent_matches", [])]
        standing_raw = raw.get("standing")
        standing = None
        if standing_raw:
            standing = StandingsEntry(
                team=team_ref,
                position=standing_raw["position"],
                played=standing_raw["played"],
                won=standing_raw["won"],
                drawn=standing_raw["drawn"],
                lost=standing_raw["lost"],
                goals_for=standing_raw["goals_for"],
                goals_against=standing_raw["goals_against"],
                points=standing_raw["points"],
            )
        congestion_raw = raw.get("fixture_congestion")
        congestion = FixtureCongestion(**congestion_raw) if congestion_raw else None

        return TeamRawDataset(
            team=team_ref,
            recent_matches=matches,
            recent_matches_home=[m for m in matches if m.is_home],
            recent_matches_away=[m for m in matches if not m.is_home],
            standing=standing,
            fixture_congestion=congestion,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ProviderError(f"Malformed team dataset payload from provider: {exc}") from exc


class RestFootballDataProvider(BaseFootballDataProvider):
    """
    Generic, vendor-agnostic REST adapter. See module docstring for how to
    connect a specific real football-data API.
    """

    name = "rest-generic"

    def __init__(self, settings: Settings):
        if not settings.football_data_api_key or not settings.football_data_api_base_url:
            raise ProviderNotConfiguredError(
                "RestFootballDataProvider requires FOOTBALL_DATA_API_KEY and "
                "FOOTBALL_DATA_API_BASE_URL to be set. No football data will be "
                "fabricated in their absence -- configure a real API key/base URL, "
                "or use ACTIVE_PROVIDER=none / sample-dev-only instead."
            )
        self._settings = settings
        self._base_url = settings.football_data_api_base_url.rstrip("/")
        self._timeout = settings.football_data_request_timeout_seconds
        self._max_retries = settings.football_data_max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.football_data_api_key}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self._base_url}{path}"
        last_error: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    response = await client.get(url, headers=self._headers(), params=params)
                    if response.status_code in (401, 403):
                        raise ProviderError(
                            f"Authentication failed against football data provider "
                            f"(HTTP {response.status_code}). Check FOOTBALL_DATA_API_KEY."
                        )
                    if response.status_code == 429:
                        raise ProviderError(
                            "Football data provider rate limit exceeded (HTTP 429)."
                        )
                    response.raise_for_status()
                    return response.json()
                except ProviderError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    continue
                except httpx.HTTPStatusError as exc:
                    raise ProviderError(
                        f"Football data provider returned HTTP {exc.response.status_code} "
                        f"for {url}."
                    ) from exc
        raise ProviderError(
            f"Football data provider request failed after {self._max_retries + 1} attempt(s): "
            f"{last_error}"
        )

    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        params: dict[str, Any] = {"date": on_date.isoformat()}
        if league_id:
            params["league_id"] = league_id
        payload = await self._get(self._settings.football_data_fixtures_path, params)
        raw_fixtures = payload.get("data", payload) if isinstance(payload, dict) else payload
        return [_map_fixture(f) for f in raw_fixtures]

    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        payload = await self._get(f"{self._settings.football_data_fixtures_path}/{match_id}")
        raw = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not raw:
            return None
        return _map_fixture(raw)

    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        params: dict[str, Any] = {"team_id": team_id}
        if league_id:
            params["league_id"] = league_id
        payload = await self._get(self._settings.football_data_team_stats_path, params)
        raw = payload.get("data", payload) if isinstance(payload, dict) else payload
        return _map_team_dataset(team_id, raw, league_id)

    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        payload = await self._get(self._settings.football_data_standings_path, {"league_id": league_id})
        raw_entries = payload.get("data", payload) if isinstance(payload, dict) else payload
        entries: list[StandingsEntry] = []
        for raw in raw_entries:
            team_ref = _map_team_ref(raw["team"], league_id)
            entries.append(
                StandingsEntry(
                    team=team_ref,
                    position=raw["position"],
                    played=raw["played"],
                    won=raw["won"],
                    drawn=raw["drawn"],
                    lost=raw["lost"],
                    goals_for=raw["goals_for"],
                    goals_against=raw["goals_against"],
                    points=raw["points"],
                )
            )
        return entries

    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        params = {"team_a": team_a_id, "team_b": team_b_id, "limit": limit}
        payload = await self._get(self._settings.football_data_h2h_path, params)
        raw_matches = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not raw_matches:
            return None
        matches = [_map_recent_match(m, team_a_id) for m in raw_matches]
        return H2HRecord(matches=matches)

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        if not self._settings.odds_api_key or not self._settings.odds_api_base_url:
            # Odds are optional by design -- absence is a valid state, not an error.
            return []
        url = f"{self._settings.odds_api_base_url.rstrip('/')}{self._settings.odds_api_path}"
        try:
            async with httpx.AsyncClient(timeout=self._settings.odds_api_request_timeout_seconds) as client:
                response = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {self._settings.odds_api_key}"},
                    params={"match_id": match_id},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError):
            # Odds are explicitly non-critical: a failure here degrades to
            # "no market data available" rather than failing the whole analysis.
            return []

        raw_markets = payload.get("data", payload) if isinstance(payload, dict) else payload
        markets: list[OddsMarket] = []
        for raw in raw_markets or []:
            selections = [
                OddsSelection(label=s["label"], price=float(s["price"])) for s in raw.get("selections", [])
            ]
            if selections:
                markets.append(
                    OddsMarket(
                        market_name=raw.get("market_name", "1X2"),
                        selections=selections,
                        bookmaker=raw.get("bookmaker"),
                    )
                )
        return markets

    async def health_check(self) -> bool:
        try:
            await self._get(self._settings.football_data_fixtures_path, {"date": date.today().isoformat()})
            return True
        except ProviderError:
            return False
