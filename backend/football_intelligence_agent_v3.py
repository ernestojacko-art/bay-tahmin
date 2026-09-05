"""BAY TAHMIN Football Intelligence Engine v0.7 statistics + historical stats layer.
Wraps v0.5 without replacing routing or HT/FT logic.
"""
from __future__ import annotations
import importlib.util
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent / "football_intelligence_agent_v2.py"
spec = importlib.util.spec_from_file_location("_bay_tahmin_engine_v2", BASE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Unable to load engine v0.5: {BASE_PATH}")
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)

ENGINE = v2.ENGINE
VERSION = "0.7.1"
dates, num, isiy, issur, market, window, day = v2.dates, v2.num, v2.isiy, v2.issur, v2.market, v2.window, v2.day
_original_model = v2.model
_original_build_match_context = v2.build_match_context
five = v2.five


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
    mapping = {
        "shots": ("shots", "total_shots"),
        "shots_on_target": ("shots_on_target", "shots_on_goal", "on_target"),
        "dangerous_attacks": ("dangerous_attacks", "dangerous_attack"),
        "attacks": ("attacks",),
        "possession": ("possession", "ball_possession"),
        "corners": ("corners", "corner_kicks"),
        "cards": ("cards", "yellow_cards", "total_cards"),
    }
    pairs = {}
    for name, aliases in mapping.items():
        pair = _stat_pair(stats, aliases)
        if pair is not None:
            pairs[name] = {"home": pair[0], "away": pair[1]}
    xg = _xg(stats)
    return {
        "available": bool(pairs or xg),
        "pairs": pairs,
        "xg": {"home": xg[0], "away": xg[1]} if xg else None,
        "source": "5DollarFootballAPI fixture statistics",
    }


def _side_value(team_id, fixture):
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    if str(home.get("id")) == str(team_id):
        return "home"
    if str(away.get("id")) == str(team_id):
        return "away"
    return None


def _aggregate_stat_games(games, team_id):
    """Aggregate only provider-observed statistics; never substitute score-derived proxies."""
    metrics = {
        "shots": [], "shots_on_target": [], "dangerous_attacks": [], "attacks": [],
        "possession": [], "corners": [], "cards": [], "xg": [],
    }
    first_half = {k: [] for k in metrics if k != "xg"}
    observed_matches = 0
    for fixture in games:
        stats = fixture.get("statistics") or {}
        extracted = _extract_statistics(stats)
        if not extracted["available"]:
            continue
        side = _side_value(team_id, fixture)
        if not side:
            continue
        got_any = False
        for name, pair in extracted["pairs"].items():
            value = pair.get(side)
            if value is not None:
                metrics[name].append(float(value)); got_any = True
        if extracted.get("xg"):
            metrics["xg"].append(float(extracted["xg"][side])); got_any = True
        fh = stats.get("first_half") if isinstance(stats, dict) else None
        if isinstance(fh, dict):
            fh_extracted = _extract_statistics(fh)
            for name, pair in fh_extracted["pairs"].items():
                value = pair.get(side)
                if value is not None and name in first_half:
                    first_half[name].append(float(value))
        if got_any:
            observed_matches += 1

    def avg(values):
        return round(sum(values) / len(values), 3) if values else None

    return {
        "observed_matches": observed_matches,
        "averages": {k: avg(v) for k, v in metrics.items()},
        "first_half_averages": {k: avg(v) for k, v in first_half.items()},
        "availability": {k: bool(v) for k, v in metrics.items()},
        "source": "5DollarFootballAPI /teams/{id}/fixtures?include=stats",
    }


async def _team_statistics(team_id):
    if team_id is None:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "unavailable"}
    try:
        fixtures = await five._get_all(
            f"teams/{int(team_id)}/fixtures",
            {"status": "finished", "include": "stats", "lang": "en", "per_page": 50},
        )
    except Exception:
        return {"last_5": {}, "last_10": {}, "last_20": {}, "source": "5DollarFootballAPI unavailable"}
    finished = [f for f in fixtures if str(f.get("status", "")).lower() in {"finished", "ft", "aet", "pen"}]
    finished.sort(key=lambda f: f.get("kickoff_ts") or 0, reverse=True)
    return {
        "last_5": _aggregate_stat_games(finished[:5], team_id),
        "last_10": _aggregate_stat_games(finished[:10], team_id),
        "last_20": _aggregate_stat_games(finished[:20], team_id),
        "source": "5DollarFootballAPI",
    }


async def _fixture_statistics(match_id):
    if not match_id:
        return {}
    try:
        payload = await five._get(f"fixtures/{int(match_id)}", {"lang": "en", "include": "events,stats"})
        fixture = payload.get("data") or {}
        return fixture.get("statistics") or {}
    except Exception:
        return {}


async def _rich_context(row):
    c = await _original_build_match_context(row)
    import asyncio
    fixture_stats, (hs, aws) = await asyncio.gather(
        _fixture_statistics(row.get("MatchID") or row.get("matchID") or row.get("id")),
        asyncio.gather(_team_statistics(row.get("HomeTeamID")), _team_statistics(row.get("AwayTeamID"))),
    )
    c["home"]["statistics"] = hs
    c["away"]["statistics"] = aws
    c["fixture_statistics"] = fixture_stats
    availability = c.get("data_availability") or {}
    for side_stats in (hs, aws):
        for window_name in ("last_5", "last_10", "last_20"):
            observed = side_stats.get(window_name, {}).get("availability", {})
            for key, present in observed.items():
                if key == "xg":
                    availability["xg"] = availability.get("xg", False) or present
                elif key in availability:
                    availability[key] = availability[key] or present
    extracted = _extract_statistics(fixture_stats)
    for key in ("shots", "shots_on_target", "dangerous_attacks", "attacks", "possession", "corners", "cards"):
        availability[key] = availability.get(key, False) or key in extracted["pairs"]
    availability["xg"] = availability.get("xg", False) or bool(extracted.get("xg"))
    c["data_availability"] = availability
    c["historical_statistics"] = {"home": hs, "away": aws, "windows": [5, 10, 20]}
    return c


def _statistics_adjustment(base, stats):
    if not stats.get("available"):
        return base, None
    p = dict(base)
    pair = stats["pairs"].get("shots_on_target") or stats["pairs"].get("shots")
    if not pair:
        return p, None
    h, a = pair["home"], pair["away"]
    total = max(1.0, h + a)
    sp = {"1": .5 + .30 * (h / total - .5), "X": .24, "2": .5 + .30 * (a / total - .5)}
    z = sum(sp.values()); sp = {k: v / z for k, v in sp.items()}
    for k in ("1", "X", "2"):
        p[k] = .90 * float(p[k]) + .10 * sp[k] * 100
    z = sum(p[k] for k in ("1", "X", "2"))
    for k in ("1", "X", "2"):
        p[k] = round(p[k] / z * 100, 2)
    return p, {"model_weight": 10, "signal": "shots_on_target_or_shots", "note": "capped statistical cross-signal"}


def model(c):
    result = _original_model(c)
    stats = _extract_statistics(c.get("fixture_statistics") or {})
    result["statistics"] = stats
    probs, adjustment = _statistics_adjustment(result.get("probabilities", {}), stats)
    result["probabilities"] = probs
    if adjustment:
        result["model_consensus"]["statistics_cross_signal"] = adjustment
    xg = stats.get("xg")
    if xg:
        old = result.get("expected_goals", {})
        result["expected_goals"] = {
            "home": round(.65 * float(old.get("home", 0)) + .35 * xg["home"], 3),
            "away": round(.65 * float(old.get("away", 0)) + .35 * xg["away"], 3),
            "kind": "provider_xg_blended",
        }
    result["historical_statistics"] = c.get("historical_statistics", {})
    result["quality"] = "real fixture and historical statistics used when supplied; unavailable fields are not invented"
    return result


async def cand(r):
    c = await _rich_context(r)
    stats = r.get("_stats") or c.get("fixture_statistics") or {}
    c["fixture_statistics"] = stats
    extracted = _extract_statistics(stats)
    flags = c.get("data_availability") or {}
    aliases = {"shots": "shots", "shots_on_target": "shots_on_target", "dangerous_attacks": "dangerous_attacks", "attacks": "attacks", "possession": "possession", "corners": "corners", "cards": "cards"}
    for source, target in aliases.items():
        flags[target] = bool(flags.get(target)) or source in extracted["pairs"]
    flags["xg"] = bool(flags.get("xg")) or bool(extracted.get("xg"))
    c["data_availability"] = flags
    return {"match": r, "context": c, "model": model(c), "markets": r.get("_markets") or []}

v2.build_match_context = _rich_context
v2.model = model
v2.cand = cand
v2.ENGINE = ENGINE
v2.VERSION = VERSION

answer = v2.answer
analyze_match = v2.analyze_match
match_answer = v2.match_answer
choose = v2.choose
pack = v2.pack
