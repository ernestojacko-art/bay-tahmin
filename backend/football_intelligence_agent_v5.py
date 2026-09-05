"""BAY TAHMIN Football Intelligence Engine v1.0 orchestration boundary.

Prevents target-match leakage during live analysis and historical backtests:
- historical team statistics are cut off strictly before kickoff
- target fixture full-time/in-play statistics are never used for pre-match predictions
- live statistics remain available for live analysis

The statistical ensemble and v0.9 tracking/market layers remain unchanged.
"""
from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_PATH = Path(__file__).resolve().parent / "football_intelligence_agent_v4.py"
spec = importlib.util.spec_from_file_location("_bay_tahmin_engine_v4", BASE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Unable to load engine v0.9: {BASE_PATH}")
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)

ENGINE = v4.ENGINE
VERSION = "1.0.0"
dates, num, isiy, issur, market, window, day = v4.dates, v4.num, v4.isiy, v4.issur, v4.market, v4.window, v4.day
five = v4.five
model = v4.model
resolve_finished_match = v4.resolve_finished_match
performance_summary = v4.performance_summary


def _fixture_id(row: dict[str, Any]):
    return row.get("MatchID") or row.get("matchID") or row.get("id")


def _kickoff_ts(row: dict[str, Any]) -> float | None:
    raw = row.get("KickoffTS") or row.get("kickoff_ts")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    value = row.get("KickoffUTC") or row.get("kickoff_utc")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _is_live(row: dict[str, Any]) -> bool:
    status = str(row.get("Status") or row.get("status") or "").lower()
    return status in {"live", "in_play", "inplay", "1h", "2h", "ht", "et"}


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stat_pair(stats, aliases):
    wanted = {str(x).lower().replace(" ", "_").replace("-", "_") for x in aliases}
    for d in _walk(stats):
        norm = {str(k).lower().replace(" ", "_").replace("-", "_"): v for k, v in d.items()}
        home_keys = {f"{x}_home" for x in wanted} | {f"home_{x}" for x in wanted} | {f"home{x}" for x in wanted}
        away_keys = {f"{x}_away" for x in wanted} | {f"away_{x}" for x in wanted} | {f"away{x}" for x in wanted}
        h = next((_number(norm[k]) for k in home_keys if k in norm and _number(norm[k]) is not None), None)
        a = next((_number(norm[k]) for k in away_keys if k in norm and _number(norm[k]) is not None), None)
        if h is not None and a is not None:
            return h, a
    return None


def _xg(stats):
    h = a = None
    for d in _walk(stats):
        norm = {str(k).lower().replace(" ", "_").replace("-", "_"): v for k, v in d.items()}
        for k in ("xg_home", "home_xg", "expected_goals_home", "expected_xg_home"):
            if k in norm and _number(norm[k]) is not None:
                h = _number(norm[k])
        for k in ("xg_away", "away_xg", "expected_goals_away", "expected_xg_away"):
            if k in norm and _number(norm[k]) is not None:
                a = _number(norm[k])
    return (h, a) if h is not None and a is not None else None


def _extract_statistics(stats):
    mapping = {"shots": ("shots", "total_shots"), "shots_on_target": ("shots_on_target", "shots_on_goal", "on_target"), "dangerous_attacks": ("dangerous_attacks", "dangerous_attack"), "attacks": ("attacks",), "possession": ("possession", "ball_possession"), "corners": ("corners", "corner_kicks"), "cards": ("cards", "yellow_cards", "total_cards")}
    pairs = {}
    for name, aliases in mapping.items():
        pair = _stat_pair(stats, aliases)
        if pair is not None:
            pairs[name] = {"home": pair[0], "away": pair[1]}
    xg = _xg(stats)
    return {"available": bool(pairs or xg), "pairs": pairs, "xg": {"home": xg[0], "away": xg[1]} if xg else None, "source": "5DollarFootballAPI fixture statistics"}


def _side_value(team_id, fixture):
    teams = fixture.get("teams") or {}
    if str((teams.get("home") or {}).get("id")) == str(team_id): return "home"
    if str((teams.get("away") or {}).get("id")) == str(team_id): return "away"
    return None


def _aggregate_stat_games(games, team_id):
    metrics = {"shots": [], "shots_on_target": [], "dangerous_attacks": [], "attacks": [], "possession": [], "corners": [], "cards": [], "xg": []}
    first_half = {k: [] for k in metrics if k != "xg"}
    observed_matches = 0
    for fixture in games:
        stats = fixture.get("statistics") or {}
        extracted = _extract_statistics(stats)
        side = _side_value(team_id, fixture)
        if not side or not extracted["available"]: continue
        got_any = False
        for name, pair in extracted["pairs"].items():
            value = pair.get(side)
            if value is not None: metrics[name].append(float(value)); got_any = True
        if extracted.get("xg"):
            metrics["xg"].append(float(extracted["xg"][side])); got_any = True
        fh = stats.get("first_half") if isinstance(stats, dict) else None
        if isinstance(fh, dict):
            fh_extracted = _extract_statistics(fh)
            for name, pair in fh_extracted["pairs"].items():
                value = pair.get(side)
                if value is not None and name in first_half: first_half[name].append(float(value))
        if got_any: observed_matches += 1
    def avg(values): return round(sum(values) / len(values), 3) if values else None
    return {"observed_matches": observed_matches, "averages": {k: avg(v) for k, v in metrics.items()}, "first_half_averages": {k: avg(v) for k, v in first_half.items()}, "availability": {k: bool(v) for k, v in metrics.items()}, "source": "5DollarFootballAPI /teams/{id}/fixtures?include=stats"}


async def _team_statistics(team_id, cutoff_ts):
    if team_id is None: return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "unavailable"}
    try:
        fixtures = await five._get_all(f"teams/{int(team_id)}/fixtures", {"status": "finished", "include": "stats", "lang": "en", "per_page": 50})
    except Exception:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "5DollarFootballAPI unavailable"}
    finished = []
    for fixture in fixtures:
        ts = _number(fixture.get("kickoff_ts"))
        if cutoff_ts is not None and ts is not None and ts >= cutoff_ts: continue
        if str(fixture.get("status", "")).lower() in {"finished", "ft", "aet", "pen"}: finished.append(fixture)
    finished.sort(key=lambda f: f.get("kickoff_ts") or 0, reverse=True)
    return {"last_5": _aggregate_stat_games(finished[:5], team_id), "last_10": _aggregate_stat_games(finished[:10], team_id), "last_20": _aggregate_stat_games(finished[:20], team_id), "source": "5DollarFootballAPI", "as_of_kickoff": cutoff_ts}


async def _fixture_statistics(match_id, allow=False):
    if not match_id or not allow: return {}
    try:
        payload = await five._get(f"fixtures/{int(match_id)}", {"lang": "en", "include": "events,stats"})
        return (payload.get("data") or {}).get("statistics") or {}
    except Exception: return {}


async def _safe_context(row):
    base = await v4.v3._original_build_match_context(row)
    cutoff = _kickoff_ts(row)
    fixture_stats, (hs, aws) = await asyncio.gather(_fixture_statistics(_fixture_id(row), allow=_is_live(row)), asyncio.gather(_team_statistics(row.get("HomeTeamID"), cutoff), _team_statistics(row.get("AwayTeamID"), cutoff)))
    base["home"]["statistics"] = hs
    base["away"]["statistics"] = aws
    base["fixture_statistics"] = fixture_stats
    availability = base.get("data_availability") or {}
    for side_stats in (hs, aws):
        for window_name in ("last_5", "last_10", "last_20"):
            observed = side_stats.get(window_name, {}).get("availability", {})
            for key, present in observed.items(): availability[key] = bool(availability.get(key)) or bool(present)
    extracted = _extract_statistics(fixture_stats)
    for key in ("shots", "shots_on_target", "dangerous_attacks", "attacks", "possession", "corners", "cards"): availability[key] = bool(availability.get(key)) or key in extracted["pairs"]
    availability["xg"] = bool(availability.get("xg")) or bool(extracted.get("xg"))
    base["data_availability"] = availability
    base["historical_statistics"] = {"home": hs, "away": aws, "windows": [5, 10, 20], "as_of_kickoff": cutoff}
    base["data_boundary"] = {"pre_match_history_cutoff": cutoff, "target_fixture_statistics_used": bool(_is_live(row)), "no_target_result_leakage": not bool(_is_live(row))}
    return base


async def cand(r):
    context = await _safe_context(r)
    result = {"match": r, "context": context, "model": model(context), "markets": r.get("_markets") or []}
    try:
        from prediction_tracking import track_predictions
        track_predictions(r, result["model"])
    except Exception: pass
    return result


v4.v3.cand = cand
v4.v3.model = model
v4.v3.v2.cand = cand
v4.v3.v2.model = model


async def analyze_match(main, mid):
    return await v4.v3.analyze_match(main, mid)


async def match_answer(main, mid, msg, history=None):
    return await v4.match_answer(main, mid, msg, history or [])


async def answer(main, message, history=None):
    return await v4._impl.v3.answer(main, message, history or [])


__all__ = ["ENGINE", "VERSION", "dates", "num", "isiy", "issur", "market", "window", "day", "five", "cand", "analyze_match", "answer", "match_answer", "model", "resolve_finished_match", "performance_summary"]
