"""Historical backtest runner for BAY TAHMIN.
Uses only completed fixtures returned by the configured provider. It never uses future odds/results to build a prediction for the same match.
"""
from __future__ import annotations

from typing import Any

import football_intelligence_agent_v3 as engine


def _actual_result(fixture: dict[str, Any]) -> str | None:
    goals = fixture.get("goals") or {}
    h, a = goals.get("home"), goals.get("away")
    if h is None or a is None:
        return None
    return "1" if h > a else "X" if h == a else "2"


def _actual_goals(fixture: dict[str, Any]) -> str | None:
    goals = fixture.get("goals") or {}
    h, a = goals.get("home"), goals.get("away")
    if h is None or a is None:
        return None
    return "over_2_5" if int(h) + int(a) > 2 else "under_2_5"


def _actual_btts(fixture: dict[str, Any]) -> str | None:
    goals = fixture.get("goals") or {}
    h, a = goals.get("home"), goals.get("away")
    if h is None or a is None:
        return None
    return "yes" if int(h) > 0 and int(a) > 0 else "no"


def score_prediction(probabilities: dict[str, float], actual: str) -> dict[str, float]:
    """Return accuracy and multiclass Brier contribution for one prediction."""
    actual_probability = float(probabilities.get(actual, 0.0)) / 100.0
    brier = sum((float(probabilities.get(k, 0.0)) / 100.0 - (1.0 if k == actual else 0.0)) ** 2 for k in ("1", "X", "2"))
    return {"correct": 1.0 if max(probabilities, key=probabilities.get) == actual else 0.0, "brier": round(brier, 6), "actual_probability": round(actual_probability, 6)}


async def backtest_fixtures(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    """Backtest already-finished fixtures supplied by a historical provider query."""
    rows = []
    for fixture in fixtures:
        actual = _actual_result(fixture)
        if actual is None:
            continue
        try:
            candidate = await engine.cand(engine.v2._fixture_row(fixture) if hasattr(engine.v2, "_fixture_row") else fixture)
        except Exception:
            continue
        probs = candidate["model"].get("probabilities") or {}
        result = score_prediction(probs, actual)
        rows.append({"match_id": fixture.get("id"), "actual_result": actual, "predicted_result": max(probs, key=probs.get), **result})

    if not rows:
        return {"available": True, "sample": 0, "message": "No completed fixtures could be scored."}
    n = len(rows)
    return {
        "available": True,
        "sample": n,
        "accuracy_pct": round(sum(r["correct"] for r in rows) / n * 100, 2),
        "brier_score": round(sum(r["brier"] for r in rows) / n, 6),
        "rows": rows,
        "warning": "Backtest quality depends on provider history and must not be interpreted as a guarantee of future accuracy.",
    }
