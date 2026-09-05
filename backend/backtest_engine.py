"""Production historical backtest runner for BAY TAHMIN Intelligence Engine."""
from __future__ import annotations
from typing import Any
import football_intelligence_agent_v6 as engine

def _actual_result(fixture: dict[str, Any]) -> str | None:
    goals = fixture.get("goals") or {}
    home, away = goals.get("home"), goals.get("away")
    if home is None or away is None: return None
    return "1" if int(home) > int(away) else "X" if int(home) == int(away) else "2"

def score_prediction(probabilities: dict[str, float], actual: str) -> dict[str, float]:
    keys = ("1", "X", "2")
    p = {k: max(0.0, float(probabilities.get(k, 0.0))) / 100.0 for k in keys}
    total = sum(p.values()) or 1.0
    p = {k: v / total for k, v in p.items()}
    predicted = max(p, key=p.get)
    brier = sum((p[k] - (1.0 if k == actual else 0.0)) ** 2 for k in keys)
    return {"correct": 1.0 if predicted == actual else 0.0, "brier": round(brier, 6), "actual_probability": round(p.get(actual, 0.0), 6)}

async def fetch_historical_fixtures(league_id: int, *, season: str | None = None, start_time: int | None = None, end_time: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"status": "finished", "include": "stats", "lang": "en", "per_page": 50}
    if season:
        params["season"] = season
    else:
        if start_time is not None: params["start_time"] = int(start_time)
        if end_time is not None: params["end_time"] = int(end_time)
    fixtures = await engine.five._get_all(f"leagues/{int(league_id)}/fixtures", params)
    fixtures = [f for f in fixtures if _actual_result(f) is not None]
    fixtures.sort(key=lambda f: f.get("kickoff_ts") or 0)
    return fixtures[:max(1, min(int(limit), 250))]

async def backtest_fixtures(fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for fixture in fixtures:
        actual = _actual_result(fixture)
        if actual is None:
            skipped += 1
            continue
        try:
            row = engine.five._fixture_row(fixture)
            candidate = await engine.cand(row)
            probabilities = candidate.get("model", {}).get("probabilities") or {}
            if not all(k in probabilities for k in ("1", "X", "2")):
                skipped += 1
                continue
            score = score_prediction(probabilities, actual)
            rows.append({"match_id": fixture.get("id"), "kickoff_ts": fixture.get("kickoff_ts"), "teams": row.get("Teams"), "actual_result": actual, "predicted_result": max(probabilities, key=probabilities.get), "probabilities": probabilities, **score})
        except Exception as exc:
            skipped += 1
            rows.append({"match_id": fixture.get("id"), "error": str(exc)[:300]})
    scored = [r for r in rows if "correct" in r]
    if not scored:
        return {"available": True, "engine": engine.ENGINE, "engine_version": engine.VERSION, "sample": 0, "skipped": skipped, "message": "No completed fixtures could be scored.", "rows": rows}
    n = len(scored)
    return {"available": True, "engine": engine.ENGINE, "engine_version": engine.VERSION, "sample": n, "skipped": skipped, "accuracy_pct": round(sum(r["correct"] for r in scored) / n * 100, 2), "brier_score": round(sum(r["brier"] for r in scored) / n, 6), "rows": scored, "warning": "Backtest is a measurement, not a guarantee. Accuracy is valid only for the returned historical sample and production model version."}

async def run_historical_backtest(league_id: int, *, season: str | None = None, start_time: int | None = None, end_time: int | None = None, limit: int = 100) -> dict[str, Any]:
    fixtures = await fetch_historical_fixtures(league_id, season=season, start_time=start_time, end_time=end_time, limit=limit)
    result = await backtest_fixtures(fixtures)
    result["source"] = "5DollarFootballAPI /leagues/{id}/fixtures"
    result["requested"] = {"league_id": league_id, "season": season, "start_time": start_time, "end_time": end_time, "limit": limit}
    return result
