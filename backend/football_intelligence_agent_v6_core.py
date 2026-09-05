"""BAY TAHMIN Football Intelligence Engine v1.2.

Strict pre-match intelligence wrapper. Historical context is bounded to the
kickoff timestamp, target-match result data is excluded from pre-match models,
and HT/FT is calculated with independent first-half and second-half priors.
Unavailable providers are reported explicitly; nothing is fabricated.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import football_intelligence_agent_v5 as v5
import football_intelligence_data as data

ENGINE = v5.ENGINE
VERSION = "1.2.0"
dates, num, isiy, issur = v5.dates, v5.num, v5.isiy, v5.issur
market, window, day = v5.market, v5.window, v5.day
five = v5.five
resolve_finished_match = v5.resolve_finished_match
performance_summary = v5.performance_summary
TZ = ZoneInfo("Europe/Istanbul")
_FINISHED = {"finished", "ft", "aet", "pen"}


def _kickoff_ts(row: dict[str, Any]) -> float | None:
    raw = row.get("KickoffTS") or row.get("kickoff_ts")
    if raw is not None:
        try: return float(raw)
        except (TypeError, ValueError): pass
    value = row.get("KickoffUTC") or row.get("kickoff_utc") or row.get("Date")
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError): return None


def _finished_before(fixtures: list[dict[str, Any]], cutoff: float | None) -> list[dict[str, Any]]:
    out = []
    for f in fixtures:
        if str(f.get("status", "")).lower() not in _FINISHED: continue
        try: ts = float(f.get("kickoff_ts")) if f.get("kickoff_ts") is not None else None
        except (TypeError, ValueError): ts = None
        if cutoff is not None and (ts is None or ts >= cutoff): continue
        out.append(f)
    out.sort(key=lambda f: f.get("kickoff_ts") or 0, reverse=True)
    return out


def _league_table(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, Any]] = {}
    for f in fixtures:
        t, g = f.get("teams") or {}, f.get("goals") or {}
        h, a = t.get("home") or {}, t.get("away") or {}
        hg, ag = g.get("home"), g.get("away")
        if hg is None or ag is None: continue
        hk, ak = str(h.get("id")), str(a.get("id"))
        table.setdefault(hk, {"team": h.get("name"), "team_id": h.get("id"), "played": 0, "points": 0, "gf": 0.0, "ga": 0.0})
        table.setdefault(ak, {"team": a.get("name"), "team_id": a.get("id"), "played": 0, "points": 0, "gf": 0.0, "ga": 0.0})
        x, y = table[hk], table[ak]; x["played"] += 1; y["played"] += 1
        x["gf"] += float(hg); x["ga"] += float(ag); y["gf"] += float(ag); y["ga"] += float(hg)
        if hg > ag: x["points"] += 3
        elif ag > hg: y["points"] += 3
        else: x["points"] += 1; y["points"] += 1
    ordered = sorted(table.values(), key=lambda x: (x["points"], x["gf"] - x["ga"], x["gf"]), reverse=True)
    rows = [{"position": i + 1, "team": {"id": x["team_id"], "name": x["team"]}, "played": x["played"], "points": x["points"], "goals_for": x["gf"], "goals_against": x["ga"]} for i, x in enumerate(ordered)]
    return {"data": {"table": rows}, "source": "computed_from_finished_results_pre_match", "window_days": 365}


def _half_averages(fixtures: list[dict[str, Any]], second=False) -> tuple[float | None, float | None]:
    home, away = [], []
    for f in fixtures:
        g = f.get("goals") or {}; fh, fa, ft_h, ft_a = g.get("half_home"), g.get("half_away"), g.get("home"), g.get("away")
        if second:
            if None in (fh, fa, ft_h, ft_a): continue
            vals = (max(0.0, float(ft_h) - float(fh)), max(0.0, float(ft_a) - float(fa)))
        else:
            if None in (fh, fa): continue
            vals = (float(fh), float(fa))
        home.append(vals[0]); away.append(vals[1])
    return (sum(home) / len(home), sum(away) / len(away)) if home else (None, None)


def _goal_timing(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    bins = ((0, 15, "0_15"), (16, 30, "16_30"), (31, 45, "31_45_plus"), (46, 60, "46_60"), (61, 75, "61_75"), (76, 120, "76_90_plus"))
    counts = {n: {"home": 0, "away": 0} for _, _, n in bins}; observed = 0; goals = 0
    for f in fixtures:
        got = False
        for e in f.get("events") or []:
            if str(e.get("type", "")).lower() != "goal": continue
            try: minute = int(float(e.get("minute")))
            except (TypeError, ValueError): continue
            side = str(e.get("team", "")).lower()
            if side not in {"home", "away"}:
                t = f.get("teams") or {}
                if str(e.get("team_id")) == str((t.get("home") or {}).get("id")): side = "home"
                elif str(e.get("team_id")) == str((t.get("away") or {}).get("id")): side = "away"
            if side not in {"home", "away"}: continue
            count = int(e.get("count") or 1); goals += count; got = True
            for lo, hi, n in bins:
                if lo <= minute <= hi: counts[n][side] += count; break
        if got: observed += 1
    return {"available": observed > 0, "observed_matches": observed, "goal_count": goals, "bins": counts, "source": "5DollarFootballAPI fixture events pre-match history"}


async def _historical_team_statistics(team_id, cutoff_ts):
    if team_id is None or cutoff_ts is None:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "unavailable", "as_of_kickoff": cutoff_ts}
    start = cutoff_ts - 365 * 86400
    try:
        fixtures = await five._get_all(f"teams/{int(team_id)}/fixtures", {"status": "finished", "start_time": int(start), "end_time": int(cutoff_ts), "include": "stats", "lang": "en", "per_page": 50})
    except Exception:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "5DollarFootballAPI unavailable", "as_of_kickoff": cutoff_ts}
    finished = _finished_before(fixtures, cutoff_ts)
    return {"last_5": v5._aggregate_stat_games(finished[:5], team_id), "last_10": v5._aggregate_stat_games(finished[:10], team_id), "last_20": v5._aggregate_stat_games(finished[:20], team_id), "source": "5DollarFootballAPI team fixtures bounded to pre-kickoff window", "as_of_kickoff": cutoff_ts}


async def _safe_base_context(row):
    cutoff, league_id = _kickoff_ts(row), row.get("LeagueID")
    if not league_id or cutoff is None:
        base = await v5.v4.v3._original_build_match_context(row)
        base["data_boundary"] = {"pre_match_history_cutoff": cutoff, "no_target_result_leakage": False, "strict_boundary": False}
        base["data_quality"] = {"level": "invalid_for_historical_backtest", "reason": "missing kickoff timestamp or league id"}
        return base
    start = cutoff - 365 * 86400
    try: fixtures = _finished_before(await five._get_all(f"leagues/{int(league_id)}/fixtures", {"start_time": int(start), "end_time": int(cutoff), "status": "finished", "include": "events", "lang": "en", "per_page": 50}), cutoff)
    except Exception: fixtures = []
    hf, af = data.team_form(fixtures, row.get("HomeTeamID")), data.team_form(fixtures, row.get("AwayTeamID"))
    standings = _league_table(fixtures)
    hs, ass = data.standings_for_team(standings, row.get("HomeTeamID"), row.get("Team1")), data.standings_for_team(standings, row.get("AwayTeamID"), row.get("Team2"))
    h2h = data._h2h(fixtures, row.get("HomeTeamID"), row.get("AwayTeamID"))
    vals = [(f.get("goals") or {}).get("home") for f in fixtures if (f.get("goals") or {}).get("home") is not None and (f.get("goals") or {}).get("away") is not None]
    vals_a = [(f.get("goals") or {}).get("away") for f in fixtures if (f.get("goals") or {}).get("home") is not None and (f.get("goals") or {}).get("away") is not None]
    avh, ava = (sum(map(float, vals)) / len(vals) if vals else 1.35), (sum(map(float, vals_a)) / len(vals_a) if vals_a else 1.10)
    fh, fa = _half_averages(fixtures, False); sh, sa = _half_averages(fixtures, True); timing = _goal_timing(fixtures)
    strength_h, strength_a = data._strength(hf, hs, avh, True), data._strength(af, ass, ava, False)
    availability = {"xg": False, "xga": False, "shots": False, "shots_on_target": False, "big_chances": False, "possession": False, "corners": False, "cards": False, "injuries": False, "suspensions": False, "lineups": False, "news": False, "first_half_goals": fh is not None, "second_half_goals": sh is not None, "goal_timing": timing["available"]}
    return {"home": {"team_id": row.get("HomeTeamID"), "name": row.get("Team1"), "recent_form": hf, "standing": hs, "strength": strength_h}, "away": {"team_id": row.get("AwayTeamID"), "name": row.get("Team2"), "recent_form": af, "standing": ass, "strength": strength_a}, "league": {"id": league_id, "name": row.get("League"), "country": row.get("Country"), "home_goal_avg": avh, "away_goal_avg": ava, "first_half_home_goal_avg": fh, "first_half_away_goal_avg": fa, "second_half_home_goal_avg": sh, "second_half_away_goal_avg": sa}, "history_window_days": 365, "h2h": h2h, "goal_timing": timing, "data_availability": availability, "provider_readiness": {"api_football_configured": False, "rich_stats_configured": False, "news_configured": False}, "data_quality": {"level": "high" if hf.get("sample", 0) >= 10 and af.get("sample", 0) >= 10 else "medium" if hf.get("sample", 0) >= 5 and af.get("sample", 0) >= 5 else "low", "home_sample": hf.get("sample", 0), "away_sample": af.get("sample", 0), "historical_window": "strictly before kickoff", "events_observed_matches": timing["observed_matches"]}}


async def _safe_context(row):
    base, (hs, aws) = await asyncio.gather(_safe_base_context(row), asyncio.gather(_historical_team_statistics(row.get("HomeTeamID"), _kickoff_ts(row)), _historical_team_statistics(row.get("AwayTeamID"), _kickoff_ts(row))))
    fixture_stats = await v5._fixture_statistics(v5._fixture_id(row), allow=v5._is_live(row))
    base["home"]["statistics"], base["away"]["statistics"], base["fixture_statistics"] = hs, aws, fixture_stats
    availability = base.get("data_availability") or {}
    for side in (hs, aws):
        for w in ("last_5", "last_10", "last_20"):
            for k, present in (side.get(w, {}).get("availability", {}) or {}).items(): availability[k] = bool(availability.get(k)) or bool(present)
    extracted = v5._extract_statistics(fixture_stats)
    for k in ("shots", "shots_on_target", "dangerous_attacks", "attacks", "possession", "corners", "cards"): availability[k] = bool(availability.get(k)) or k in extracted["pairs"]
    availability["xg"] = bool(availability.get("xg")) or bool(extracted.get("xg")); base["data_availability"] = availability
    cutoff, live = _kickoff_ts(row), v5._is_live(row)
    base["historical_statistics"] = {"home": hs, "away": aws, "windows": [5, 10, 20], "as_of_kickoff": cutoff}
    base["data_boundary"] = {"pre_match_history_cutoff": cutoff, "target_fixture_statistics_used": live, "no_target_result_leakage": not live, "strict_boundary": bool(row.get("LeagueID") and cutoff is not None)}
    return base


def _inject_priors(c):
    result = dict(c)
    league = c.get("league") or {}
    for side, key_h, key_a, source in (("home", "first_half_home_goal_avg", "first_half_away_goal_avg", "league_pre_match_first_half_prior"), ("away", "first_half_away_goal_avg", "first_half_home_goal_avg", "league_pre_match_first_half_prior"), ("home", "second_half_home_goal_avg", "second_half_away_goal_avg", "league_pre_match_second_half_prior"), ("away", "second_half_away_goal_avg", "second_half_home_goal_avg", "league_pre_match_second_half_prior")):
        block, form = dict(result.get(side) or {}), dict((result.get(side) or {}).get("recent_form") or {})
        target = "first_half" if "first_half" in source else "second_half"
        existing = form.get(target)
        if isinstance(existing, dict) and all(existing.get(k) is not None for k in ("goals_for_avg", "goals_against_avg")): continue
        x, y = league.get(key_h), league.get(key_a)
        if x is None or y is None: continue
        form[target] = {"goals_for_avg": float(x), "goals_against_avg": float(y), "source": source}; block["recent_form"] = form; result[side] = block
    return result


def _poisson(lam, k): return math.exp(-lam) * lam ** k / math.factorial(k)


def _matrix(x, y, n=8):
    m = [[_poisson(max(.01, x), i) * _poisson(max(.01, y), j) for j in range(n + 1)] for i in range(n + 1)]
    z = sum(sum(r) for r in m) or 1.0
    return [[v / z for v in r] for r in m]


def _result_probs(m):
    one = sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i > j); draw = sum(m[i][i] for i in range(len(m)))
    return {"1": one, "X": draw, "2": max(0.0, 1 - one - draw)}


def _joint_htft(ht, second):
    out = {f"{h}/{f}": 0.0 for h in ("1", "X", "2") for f in ("1", "X", "2")}
    for hi, hr in enumerate(ht):
        for hj, hp in enumerate(hr):
            ht_r = "1" if hi > hj else "X" if hi == hj else "2"
            for si, sr in enumerate(second):
                for sj, sp in enumerate(sr):
                    ft_r = "1" if hi + si > hj + sj else "X" if hi + si == hj + sj else "2"
                    out[f"{ht_r}/{ft_r}"] += hp * sp
    z = sum(out.values()) or 1.0
    return {k: v / z for k, v in out.items()}


def _timing_signal(c):
    timing = c.get("goal_timing") or {}
    bins = timing.get("bins") or {}
    total = sum(v.get("home", 0) + v.get("away", 0) for v in bins.values())
    if not total: return {"available": False, "reason": "goal timing events unavailable"}
    second = sum(v.get("home", 0) + v.get("away", 0) for k, v in bins.items() if k in {"46_60", "61_75", "76_90_plus"})
    first = total - second
    return {"available": True, "second_half_goal_share": round(second / total * 100, 2), "first_half_goal_share": round(first / total * 100, 2), "sample_matches": timing.get("observed_matches", 0), "source": timing.get("source")}


def model(context):
    safe = _inject_priors(context)
    result = v5.model(safe)
    league = context.get("league") or {}
    shx, shy = league.get("second_half_home_goal_avg"), league.get("second_half_away_goal_avg")
    fh = result.get("first_half_model") or {}
    fhx, fhy = fh.get("expected_goals", {}).get("home"), fh.get("expected_goals", {}).get("away")
    independent_second = shx is not None and shy is not None
    if independent_second:
        fhx = float(fhx if fhx is not None else (league.get("first_half_home_goal_avg") or 0.55)); fhy = float(fhy if fhy is not None else (league.get("first_half_away_goal_avg") or 0.45))
        sx, sy = max(.05, float(shx)), max(.05, float(shy))
        ht = _matrix(fhx, fhy, 8); second = _matrix(sx, sy, 8); joint = _joint_htft(ht, second)
        result["first_half"] = {k: round(v * 100, 2) for k, v in _result_probs(ht).items()}
        result["iyms"] = {"probabilities": {k: round(v * 100, 2) for k, v in sorted(joint.items(), key=lambda z: z[1], reverse=True)}, "surprise_candidates": [x for x in result.get("iyms", {}).get("surprise_candidates", []) if x.get("selection") not in {"1/1", "X/X", "2/2"}]}
        result["second_half_model"] = {"independent": True, "source": "league_pre_match_second_half_prior", "expected_goals": {"home": round(sx, 3), "away": round(sy, 3)}, "goal_timing": _timing_signal(context)}
        result["htft_model"] = {"independent_first_half": True, "independent_second_half": True, "joint_method": "HT matrix × independent second-half matrix", "all_9_outcomes": True}
    else:
        result["second_half_model"] = {"independent": False, "source": "unavailable_independent_second_half_prior", "expected_goals": {"home": shx, "away": shy}, "goal_timing": _timing_signal(context)}
        result["htft_model"] = {"independent_first_half": True, "independent_second_half": False, "all_9_outcomes": True, "warning": "independent second-half prior unavailable"}
    models = result.setdefault("model_consensus", {}).setdefault("models", {})
    models["second_half_independent"] = {"available": independent_second, "home_expected_goals": shx, "away_expected_goals": shy}
    models["squad_impact"] = {"available": False, "reason": "No verified squad/injury/suspension provider configured"}
    models["news_intelligence"] = {"available": False, "reason": "No verified news provider configured"}
    models["xg"] = models.get("xg") or {"available": False, "reason": "5DollarFootballAPI statistics currently do not provide verified xG/xGA fields"}
    result["market_role"] = "cross_check_only"
    result["quality"] = "strict pre-kickoff history; independent HT/2H priors when available; market is cross-check only; unavailable fields are never fabricated"
    return result


async def cand(row: dict[str, Any], *, track: bool = True):
    context = await _safe_context(row); result = {"match": row, "context": context, "model": model(context), "markets": row.get("_markets") or []}
    if track:
        try:
            from prediction_tracking import track_predictions
            track_predictions(row, result["model"])
        except Exception: pass
    return result

v5.v4.v3.cand = cand
v5.v4.v3.v2.cand = cand
v5.v4.v3.model = model
v5.v4.v3.v2.model = model

async def analyze_match(main, mid): return await v5.v4.v3.analyze_match(main, mid)
async def match_answer(main, mid, msg, history=None): return await v5.v4.match_answer(main, mid, msg, history or [])
async def answer(main, message, history=None): return await v5.v4._impl.v3.answer(main, message, history or [])

__all__ = ["ENGINE", "VERSION", "dates", "num", "isiy", "issur", "market", "window", "day", "five", "cand", "analyze_match", "answer", "match_answer", "model", "resolve_finished_match", "performance_summary"]
