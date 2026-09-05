"""BAY TAHMIN Football Intelligence Engine v1.1 strict historical boundary wrapper.

Builds the complete pre-match context from fixtures strictly before kickoff,
then delegates the statistical ensemble and tracking layers to v1.0.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import football_intelligence_agent_v5 as v5
import football_intelligence_data as data

ENGINE = v5.ENGINE
VERSION = "1.1.3"
dates, num, isiy, issur = v5.dates, v5.num, v5.isiy, v5.issur
market, window, day = v5.market, v5.window, v5.day
five = v5.five
resolve_finished_match = v5.resolve_finished_match
performance_summary = v5.performance_summary

TZ = ZoneInfo("Europe/Istanbul")


def _kickoff_ts(row: dict[str, Any]) -> float | None:
    raw = row.get("KickoffTS") or row.get("kickoff_ts")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    value = row.get("KickoffUTC") or row.get("kickoff_utc") or row.get("Date")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _finished_before(fixtures: list[dict[str, Any]], cutoff: float | None) -> list[dict[str, Any]]:
    out = []
    for fixture in fixtures:
        if str(fixture.get("status", "")).lower() not in {"finished", "ft", "aet", "pen"}:
            continue
        ts = fixture.get("kickoff_ts")
        try:
            ts = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts = None
        if cutoff is not None and (ts is None or ts >= cutoff):
            continue
        out.append(fixture)
    out.sort(key=lambda f: f.get("kickoff_ts") or 0, reverse=True)
    return out


def _league_table(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, Any]] = {}
    for fixture in fixtures:
        teams = fixture.get("teams") or {}
        goals = fixture.get("goals") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        hk, ak = str(home.get("id")), str(away.get("id"))
        table.setdefault(hk, {"team": home.get("name"), "team_id": home.get("id"), "played": 0, "points": 0, "gf": 0.0, "ga": 0.0})
        table.setdefault(ak, {"team": away.get("name"), "team_id": away.get("id"), "played": 0, "points": 0, "gf": 0.0, "ga": 0.0})
        h, a = table[hk], table[ak]
        h["played"] += 1; a["played"] += 1
        h["gf"] += float(hg); h["ga"] += float(ag)
        a["gf"] += float(ag); a["ga"] += float(hg)
        if hg > ag: h["points"] += 3
        elif ag > hg: a["points"] += 3
        else: h["points"] += 1; a["points"] += 1
    ordered = sorted(table.values(), key=lambda x: (x["points"], x["gf"] - x["ga"], x["gf"]), reverse=True)
    rows = [{"position": i + 1, "team": {"id": x["team_id"], "name": x["team"]}, "played": x["played"], "points": x["points"], "goals_for": x["gf"], "goals_against": x["ga"]} for i, x in enumerate(ordered)]
    return {"data": {"table": rows}, "source": "computed_from_finished_results_pre_match", "window_days": 365}


def _first_half_league_averages(fixtures: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    home, away = [], []
    for fixture in fixtures:
        goals = fixture.get("goals") or {}
        hh, aa = goals.get("half_home"), goals.get("half_away")
        if hh is None or aa is None:
            continue
        try:
            home.append(float(hh)); away.append(float(aa))
        except (TypeError, ValueError):
            continue
    if not home:
        return None, None
    return sum(home) / len(home), sum(away) / len(away)


def _second_half_league_averages(fixtures: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    home, away = [], []
    for fixture in fixtures:
        goals = fixture.get("goals") or {}
        fh, fa, ft_h, ft_a = goals.get("half_home"), goals.get("half_away"), goals.get("home"), goals.get("away")
        if None in (fh, fa, ft_h, ft_a):
            continue
        try:
            home.append(max(0.0, float(ft_h) - float(fh)))
            away.append(max(0.0, float(ft_a) - float(fa)))
        except (TypeError, ValueError):
            continue
    if not home:
        return None, None
    return sum(home) / len(home), sum(away) / len(away)


def _goal_timing(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    bins = ((0, 15, "0_15"), (16, 30, "16_30"), (31, 45, "31_45_plus"), (46, 60, "46_60"), (61, 75, "61_75"), (76, 120, "76_90_plus"))
    counts = {name: {"home": 0, "away": 0} for _, _, name in bins}
    observed_matches = 0
    for fixture in fixtures:
        events = fixture.get("events") or []
        got_goal = False
        for event in events:
            if str(event.get("type", "")).lower() != "goal":
                continue
            minute = event.get("minute")
            try:
                minute = int(float(minute))
            except (TypeError, ValueError):
                continue
            side = str(event.get("team", "")).lower()
            if side not in {"home", "away"}:
                teams = fixture.get("teams") or {}
                if str(event.get("team_id")) == str((teams.get("home") or {}).get("id")): side = "home"
                elif str(event.get("team_id")) == str((teams.get("away") or {}).get("id")): side = "away"
            if side not in {"home", "away"}: continue
            for lo, hi, name in bins:
                if lo <= minute <= hi:
                    counts[name][side] += int(event.get("count") or 1)
                    got_goal = True
                    break
        if got_goal: observed_matches += 1
    return {"available": observed_matches > 0, "observed_matches": observed_matches, "bins": counts, "source": "5DollarFootballAPI fixture events pre-match history"}


async def _historical_team_statistics(team_id, cutoff_ts):
    if team_id is None or cutoff_ts is None:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "unavailable", "as_of_kickoff": cutoff_ts}
    start = cutoff_ts - 365 * 86400
    try:
        fixtures = await five._get_all(
            f"teams/{int(team_id)}/fixtures",
            {"status": "finished", "start_time": int(start), "end_time": int(cutoff_ts), "include": "stats", "lang": "en", "per_page": 50},
        )
    except Exception:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "5DollarFootballAPI unavailable", "as_of_kickoff": cutoff_ts}
    finished = _finished_before(fixtures, cutoff_ts)
    return {
        "last_5": v5._aggregate_stat_games(finished[:5], team_id),
        "last_10": v5._aggregate_stat_games(finished[:10], team_id),
        "last_20": v5._aggregate_stat_games(finished[:20], team_id),
        "source": "5DollarFootballAPI team fixtures bounded to pre-kickoff window",
        "as_of_kickoff": cutoff_ts,
    }


async def _safe_base_context(row: dict[str, Any]) -> dict[str, Any]:
    cutoff = _kickoff_ts(row)
    league_id = row.get("LeagueID")
    if not league_id or cutoff is None:
        base = await v5.v4.v3._original_build_match_context(row)
        base["data_boundary"] = {"pre_match_history_cutoff": cutoff, "no_target_result_leakage": False, "strict_boundary": False}
        base["data_quality"] = {"level": "invalid_for_historical_backtest", "reason": "missing kickoff timestamp or league id"}
        return base
    start = cutoff - 365 * 86400
    try:
        fixtures = await five._get_all(f"leagues/{int(league_id)}/fixtures", {"start_time": int(start), "end_time": int(cutoff), "status": "finished", "include": "events", "lang": "en", "per_page": 50})
        fixtures = _finished_before(fixtures, cutoff)
    except Exception:
        fixtures = []
    hf = data.team_form(fixtures, row.get("HomeTeamID"))
    af = data.team_form(fixtures, row.get("AwayTeamID"))
    standings = _league_table(fixtures)
    hs = data.standings_for_team(standings, row.get("HomeTeamID"), row.get("Team1"))
    ass = data.standings_for_team(standings, row.get("AwayTeamID"), row.get("Team2"))
    h2h = data._h2h(fixtures, row.get("HomeTeamID"), row.get("AwayTeamID"))
    total = [(f.get("goals") or {}).get("home") for f in fixtures if (f.get("goals") or {}).get("home") is not None and (f.get("goals") or {}).get("away") is not None]
    away_total = [(f.get("goals") or {}).get("away") for f in fixtures if (f.get("goals") or {}).get("home") is not None and (f.get("goals") or {}).get("away") is not None]
    avh = sum(float(x) for x in total) / len(total) if total else 1.35
    ava = sum(float(x) for x in away_total) / len(away_total) if away_total else 1.10
    sh = data._strength(hf, hs, avh, True)
    sa = data._strength(af, ass, ava, False)
    fh_home, fh_away = _first_half_league_averages(fixtures)
    sh_home, sh_away = _second_half_league_averages(fixtures)
    timing = _goal_timing(fixtures)
    providers = {"api_football_configured": False, "rich_stats_configured": False, "news_configured": False}
    availability = {"xg": False, "xga": False, "shots": False, "shots_on_target": False, "big_chances": False, "possession": False, "corners": False, "cards": False, "injuries": False, "suspensions": False, "lineups": False, "news": False, "first_half_goals": fh_home is not None, "second_half_goals": sh_home is not None, "goal_timing": timing["available"]}
    return {
        "home": {"team_id": row.get("HomeTeamID"), "name": row.get("Team1"), "recent_form": hf, "standing": hs, "strength": sh},
        "away": {"team_id": row.get("AwayTeamID"), "name": row.get("Team2"), "recent_form": af, "standing": ass, "strength": sa},
        "league": {"id": league_id, "name": row.get("League"), "country": row.get("Country"), "home_goal_avg": avh, "away_goal_avg": ava, "first_half_home_goal_avg": fh_home, "first_half_away_goal_avg": fh_away, "second_half_home_goal_avg": sh_home, "second_half_away_goal_avg": sh_away},
        "history_window_days": 365,
        "h2h": h2h,
        "goal_timing": timing,
        "data_availability": availability,
        "provider_readiness": providers,
        "data_quality": {"level": "high" if hf.get("sample", 0) >= 10 and af.get("sample", 0) >= 10 else "medium" if hf.get("sample", 0) >= 5 and af.get("sample", 0) >= 5 else "low", "home_sample": hf.get("sample", 0), "away_sample": af.get("sample", 0), "historical_window": "strictly before kickoff", "events_observed_matches": timing["observed_matches"]},
    }


async def _safe_statistics(row: dict[str, Any]):
    cutoff = _kickoff_ts(row)
    hs, aws = await asyncio.gather(_historical_team_statistics(row.get("HomeTeamID"), cutoff), _historical_team_statistics(row.get("AwayTeamID"), cutoff))
    fixture_stats = await v5._fixture_statistics(v5._fixture_id(row), allow=v5._is_live(row))
    return hs, aws, fixture_stats


async def _safe_context(row: dict[str, Any]) -> dict[str, Any]:
    base, stats = await asyncio.gather(_safe_base_context(row), _safe_statistics(row))
    hs, aws, fixture_stats = stats
    base["home"]["statistics"] = hs; base["away"]["statistics"] = aws; base["fixture_statistics"] = fixture_stats
    availability = base.get("data_availability") or {}
    for side_stats in (hs, aws):
        for window_name in ("last_5", "last_10", "last_20"):
            for key, present in (side_stats.get(window_name, {}).get("availability", {}) or {}).items():
                availability[key] = bool(availability.get(key)) or bool(present)
    extracted = v5._extract_statistics(fixture_stats)
    for key in ("shots", "shots_on_target", "dangerous_attacks", "attacks", "possession", "corners", "cards"):
        availability[key] = bool(availability.get(key)) or key in extracted["pairs"]
    availability["xg"] = bool(availability.get("xg")) or bool(extracted.get("xg"))
    base["data_availability"] = availability
    cutoff = _kickoff_ts(row); live = v5._is_live(row)
    base["historical_statistics"] = {"home": hs, "away": aws, "windows": [5, 10, 20], "as_of_kickoff": cutoff}
    base["data_boundary"] = {"pre_match_history_cutoff": cutoff, "target_fixture_statistics_used": live, "no_target_result_leakage": not live, "strict_boundary": bool(row.get("LeagueID") and cutoff is not None)}
    return base


def _inject_independent_ht_prior(context: dict[str, Any]) -> dict[str, Any]:
    league = context.get("league") or {}
    hx = league.get("first_half_home_goal_avg")
    hy = league.get("first_half_away_goal_avg")
    if hx is None or hy is None:
        return context
    result = dict(context)
    for side in ("home", "away"):
        block = dict(result.get(side) or {})
        form = dict(block.get("recent_form") or {})
        fh = form.get("first_half")
        if isinstance(fh, dict) and all(fh.get(k) is not None for k in ("goals_for_avg", "goals_against_avg")):
            continue
        if side == "home":
            observed = {"goals_for_avg": float(hx), "goals_against_avg": float(hy), "source": "league_pre_match_first_half_prior"}
        else:
            observed = {"goals_for_avg": float(hy), "goals_against_avg": float(hx), "source": "league_pre_match_first_half_prior"}
        form["first_half"] = observed
        block["recent_form"] = form
        result[side] = block
    return result


def _inject_independent_second_half_prior(context: dict[str, Any]) -> dict[str, Any]:
    league = context.get("league") or {}
    hx, hy = league.get("second_half_home_goal_avg"), league.get("second_half_away_goal_avg")
    if hx is None or hy is None:
        return context
    result = dict(context)
    for side in ("home", "away"):
        block = dict(result.get(side) or {})
        form = dict(block.get("recent_form") or {})
        existing = form.get("second_half")
        if isinstance(existing, dict) and all(existing.get(k) is not None for k in ("goals_for_avg", "goals_against_avg")):
            continue
        if side == "home": observed = {"goals_for_avg": float(hx), "goals_against_avg": float(hy), "source": "league_pre_match_second_half_prior"}
        else: observed = {"goals_for_avg": float(hy), "goals_against_avg": float(hx), "source": "league_pre_match_second_half_prior"}
        form["second_half"] = observed
        block["recent_form"] = form
        result[side] = block
    return result


def model(context: dict[str, Any]) -> dict[str, Any]:
    safe_context = _inject_independent_ht_prior(context)
    safe_context = _inject_independent_second_half_prior(safe_context)
    result = v5.model(safe_context)
    fh = result.get("first_half_model") or {}
    if fh.get("source") == "observed_first_half_5_10_20_weighted":
        home_fh = (context.get("home", {}).get("recent_form", {}) or {}).get("first_half", {}) or {}
        away_fh = (context.get("away", {}).get("recent_form", {}) or {}).get("first_half", {}) or {}
        if not all(home_fh.get(k) is not None for k in ("goals_for_avg", "goals_against_avg")) or not all(away_fh.get(k) is not None for k in ("goals_for_avg", "goals_against_avg")):
            fh["source"] = "league_pre_match_first_half_prior"
            fh["independent"] = True
            result["first_half_model"] = fh
    result["second_half_model"] = {
        "independent": bool(context.get("league", {}).get("second_half_home_goal_avg") is not None),
        "source": "league_pre_match_second_half_prior" if context.get("league", {}).get("second_half_home_goal_avg") is not None else "derived_from_full_match_model_when_provider_prior_unavailable",
        "expected_goals": {"home": context.get("league", {}).get("second_half_home_goal_avg"), "away": context.get("league", {}).get("second_half_away_goal_avg")},
        "goal_timing": context.get("goal_timing", {"available": False}),
    }
    return result


async def cand(row: dict[str, Any], *, track: bool = True):
    context = await _safe_context(row)
    result = {"match": row, "context": context, "model": model(context), "markets": row.get("_markets") or []}
    if track:
        try:
            from prediction_tracking import track_predictions
            track_predictions(row, result["model"])
        except Exception:
            pass
    return result


v5.v4.v3.cand = cand
v5.v4.v3.v2.cand = cand
v5.v4.v3.model = model
v5.v4.v3.v2.model = model


async def analyze_match(main, mid):
    return await v5.v4.v3.analyze_match(main, mid)


async def match_answer(main, mid, msg, history=None):
    return await v5.v4.match_answer(main, mid, msg, history or [])


async def answer(main, message, history=None):
    return await v5.v4._impl.v3.answer(main, message, history or [])


__all__ = ["ENGINE", "VERSION", "dates", "num", "isiy", "issur", "market", "window", "day", "five", "cand", "analyze_match", "answer", "match_answer", "model", "resolve_finished_match", "performance_summary"]
