"""Historical football context for BAY TAHMİN FOOTBALL INTELLIGENCE ENGINE."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import five_dollar_bridge as five

_CACHE: Dict[str, tuple[float, Any]] = {}
TTL = 15 * 60


def _cache_get(key: str):
    item = _CACHE.get(key)
    if item and time.time() - item[0] < TTL:
        return item[1]
    return None


def _cache_put(key: str, value: Any):
    _CACHE[key] = (time.time(), value)
    return value


async def league_context(league_id: Any, target_date: str) -> Dict[str, Any]:
    """Fetch a league slice once and derive recent team form + table context.

    The endpoint is intentionally league-batched: Pro allows a broad historical
    league-fixture window, avoiding one request per team/match.
    """
    if not league_id:
        return {"fixtures": [], "standings": None}
    key = f"league:{league_id}:{target_date}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    target = datetime.fromisoformat(target_date).replace(tzinfo=ZoneInfo("Europe/Istanbul"))
    end = (target + timedelta(days=1)).astimezone(timezone.utc)
    start = (target - timedelta(days=120)).astimezone(timezone.utc)
    try:
        payload = await five._get(
            f"leagues/{int(league_id)}/fixtures",
            {
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "status": "all",
                "lang": "en",
                "per_page": 100,
            },
        )
    except Exception:
        payload = {"data": []}

    fixtures = payload.get("data") or []
    try:
        standings = await five._get(
            "standings",
            {"league": int(league_id), "type": "total", "lang": "en"},
        )
    except Exception:
        standings = None
    result = {"fixtures": fixtures, "standings": standings}
    return _cache_put(key, result)


def _finished_for_team(fixtures: List[Dict[str, Any]], team_id: Any) -> List[Dict[str, Any]]:
    out = []
    tid = str(team_id)
    for f in fixtures:
        if str(f.get("status", "")).lower() not in {"finished", "ft", "aet", "pen"}:
            continue
        teams = f.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        if str(home.get("id")) == tid or str(away.get("id")) == tid:
            out.append(f)
    out.sort(key=lambda x: x.get("kickoff_ts") or 0, reverse=True)
    return out[:10]


def team_form(fixtures: List[Dict[str, Any]], team_id: Any) -> Dict[str, Any]:
    games = _finished_for_team(fixtures, team_id)
    if not games:
        return {"sample": 0, "points_per_game": None, "goals_for_avg": None, "goals_against_avg": None, "form": ""}
    tid = str(team_id)
    points = gf = ga = 0.0
    form = []
    home_games = away_games = 0
    for f in games:
        teams = f.get("teams") or {}
        h = teams.get("home") or {}
        a = teams.get("away") or {}
        g = f.get("goals") or {}
        hg, ag = g.get("home"), g.get("away")
        if hg is None or ag is None:
            continue
        if str(h.get("id")) == tid:
            team_gf, team_ga = float(hg), float(ag)
            home_games += 1
        else:
            team_gf, team_ga = float(ag), float(hg)
            away_games += 1
        gf += team_gf; ga += team_ga
        if team_gf > team_ga: points += 3; form.append("W")
        elif team_gf == team_ga: points += 1; form.append("D")
        else: form.append("L")
    n = len(form)
    return {
        "sample": n,
        "points": points,
        "points_per_game": round(points / n, 3) if n else None,
        "goals_for_avg": round(gf / n, 3) if n else None,
        "goals_against_avg": round(ga / n, 3) if n else None,
        "goal_diff_avg": round((gf - ga) / n, 3) if n else None,
        "form": "".join(form),
        "home_games": home_games,
        "away_games": away_games,
    }


def standings_for_team(standings: Any, team_id: Any) -> Dict[str, Any]:
    rows = ((standings or {}).get("data") or {}).get("table") or []
    tid = str(team_id)
    for row in rows:
        team = row.get("team") or {}
        if str(team.get("id")) == tid:
            return {
                "position": row.get("position"),
                "played": row.get("played"),
                "win": row.get("win"),
                "draw": row.get("draw"),
                "lose": row.get("lose"),
                "points": row.get("points"),
                "goals_for": row.get("goals_for", row.get("goalsFor")),
                "goals_against": row.get("goals_against", row.get("goalsAgainst")),
            }
    return {}


async def build_match_context(row: Dict[str, Any]) -> Dict[str, Any]:
    league_id = row.get("LeagueID")
    target = str(row.get("KickoffUTC") or row.get("Date") or "")[:10]
    context = await league_context(league_id, target) if league_id and target else {"fixtures": [], "standings": None}
    home_id = row.get("HomeTeamID")
    away_id = row.get("AwayTeamID")
    return {
        "home": {
            "team_id": home_id,
            "name": row.get("Team1"),
            "recent_form": team_form(context["fixtures"], home_id),
            "standing": standings_for_team(context["standings"], home_id),
        },
        "away": {
            "team_id": away_id,
            "name": row.get("Team2"),
            "recent_form": team_form(context["fixtures"], away_id),
            "standing": standings_for_team(context["standings"], away_id),
        },
        "league": {"id": league_id, "name": row.get("League"), "country": row.get("Country")},
        "history_window_days": 120,
        "source": "5DollarFootballAPI",
    }
