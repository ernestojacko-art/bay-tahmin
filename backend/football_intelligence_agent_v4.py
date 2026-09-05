"""BAY TAHMIN Football Intelligence Engine v0.9 orchestration layer.

Keeps the real statistical engine intact while adding:
- explicit model-vs-market divergence intelligence
- prediction persistence and post-match outcome resolution
- performance summary exposure
- strict no-fabrication handling for unavailable squad/news data
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

BASE_PATH = Path(__file__).resolve().parent / "football_intelligence_agent_v3.py"
spec = importlib.util.spec_from_file_location("_bay_tahmin_engine_v3", BASE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Unable to load engine v0.8: {BASE_PATH}")
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)

try:
    from prediction_tracking import track_predictions, resolve_fixture, performance_summary
except Exception:
    def track_predictions(*args, **kwargs): return 0
    def resolve_fixture(*args, **kwargs): return 0
    def performance_summary(*args, **kwargs): return {"available": False, "reason": "tracking module unavailable"}

ENGINE = v3.ENGINE
VERSION = "0.9.0"
dates, num, isiy, issur, market, window, day = v3.dates, v3.num, v3.isiy, v3.issur, v3.market, v3.window, v3.day
five = v3.five

_original_model = v3.model
_original_cand = v3.cand
_original_analyze_match = v3.analyze_match
_original_match_answer = v3.match_answer


def _market_divergence(model: dict[str, Any]) -> dict[str, Any]:
    mi = model.get("model_consensus", {}).get("market_intelligence") or {}
    mp = mi.get("probabilities") or {}
    pp = model.get("probabilities") or {}
    if not mp or not all(k in mp and k in pp for k in ("1", "X", "2")):
        return {"available": False, "reason": "active 1X2 market unavailable"}
    gaps = {k: round(float(pp[k]) - float(mp[k]), 2) for k in ("1", "X", "2")}
    favorite = max(("1", "X", "2"), key=lambda k: float(mp[k]))
    model_pick = max(("1", "X", "2"), key=lambda k: float(pp[k]))
    favorite_gap = gaps[favorite]
    return {
        "available": True,
        "market_favorite": favorite,
        "model_pick": model_pick,
        "probability_gap_points": gaps,
        "favorite_gap_points": favorite_gap,
        "divergence_points": max(abs(v) for v in gaps.values()),
        "divergence": "strong" if max(abs(v) for v in gaps.values()) >= 12 else "moderate" if max(abs(v) for v in gaps.values()) >= 7 else "low",
        "role": "market is a cross-check only; model remains decision maker",
    }


def _model(c: dict[str, Any]) -> dict[str, Any]:
    result = _original_model(c)
    result.setdefault("model_consensus", {}).setdefault("models", {})
    result["model_consensus"]["models"].setdefault("squad_impact", {"available": False, "reason": "No verified squad/injury/suspension provider is configured"})
    result["model_consensus"]["models"].setdefault("news_intelligence", {"available": False, "reason": "No verified news provider is configured"})
    result["market_divergence"] = _market_divergence(result)
    result["model_consensus"]["market_decision_weight"] = 0
    result["model_consensus"]["market_role"] = "cross_check_only"
    return result


async def cand(r):
    result = await _original_cand(r)
    model_result = _model(result.get("context") or {})
    result["model"] = model_result
    try:
        track_predictions(result.get("match") or {}, model_result)
    except Exception:
        pass
    return result


v3.cand = cand
v3.model = _model


async def analyze_match(main, mid):
    result = await _original_analyze_match(main, mid)
    model_result = result.get("model") or {}
    if model_result:
        model_result = _model(result.get("context") or {})
        result["model"] = model_result
        try:
            track_predictions((await main.get_match_detail(mid)) or {}, model_result)
        except Exception:
            pass
    return result


async def match_answer(main, mid, msg, history=None):
    return await _original_match_answer(main, mid, msg, history or [])


async def resolve_finished_match(match: dict[str, Any]) -> int:
    goals = match.get("goals") or {}
    home = goals.get("home")
    away = goals.get("away")
    mid = match.get("MatchID") or match.get("matchID") or match.get("id")
    if mid is None or home is None or away is None:
        return 0
    half_home = goals.get("half_home")
    half_away = goals.get("half_away")
    return resolve_fixture(int(mid), int(home), int(away), half_home, half_away)


# Public model export is required by the production facade.
model = _model

__all__ = [
    "ENGINE", "VERSION", "dates", "num", "isiy", "issur", "market", "window", "day",
    "five", "cand", "analyze_match", "match_answer", "model", "resolve_finished_match",
    "performance_summary",
]
