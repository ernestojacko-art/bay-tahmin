"""Prediction tracking for the BAY TAHMIN Intelligence Engine.
Writes are best-effort: prediction generation must never fail because tracking is unavailable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")


def _headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY or "",
        "Authorization": f"Bearer {SUPABASE_KEY or ''}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def _key(match_id: Any, prediction_type: str, prediction_value: str, model_version: str) -> str:
    return f"{match_id}:{prediction_type}:{prediction_value}:{model_version}"


def _enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def track_predictions(match: dict[str, Any], model: dict[str, Any], *, source: str = "BAY TAHMIN FOOTBALL INTELLIGENCE ENGINE") -> int:
    """Persist core pre-match predictions. Duplicate prediction_key values are ignored."""
    if not _enabled():
        return 0
    external_id = match.get("MatchID") or match.get("matchID") or match.get("id")
    kickoff = match.get("KickoffUTC") or match.get("kickoff") or match.get("Date")
    teams = str(match.get("Teams") or "")
    if not external_id or not kickoff or " - " not in teams:
        return 0
    home, away = [x.strip() for x in teams.split(" - ", 1)]
    version = str(model.get("engine_version") or model.get("version") or "0.8.0")
    probs = model.get("probabilities") or {}
    predictions = []
    for kind, values in (("match_result", {k: probs.get(k) for k in ("1", "X", "2")}),
                         ("goals", {"over_2_5": probs.get("over_2_5"), "under_2_5": probs.get("under_2_5")}),
                         ("btts", {"yes": probs.get("btts_yes"), "no": probs.get("btts_no")})):
        for value, confidence in values.items():
            if confidence is None:
                continue
            predictions.append({
                "external_match_id": int(external_id),
                "kickoff_at": kickoff,
                "competition_name": match.get("League"),
                "home_team": home,
                "away_team": away,
                "prediction_type": kind,
                "prediction_value": str(value),
                "confidence": float(confidence),
                "model_version": version,
                "source": source,
                "predicted_at": datetime.now(timezone.utc).isoformat(),
                "result_status": "pending",
                "prediction_key": _key(external_id, kind, str(value), version),
                "raw_prediction": model,
            })
    if not predictions:
        return 0
    try:
        r = httpx.post(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), json=predictions, timeout=10)
        r.raise_for_status()
        return len(predictions)
    except Exception:
        return 0


def resolve_fixture(external_match_id: int, home_score: int | None, away_score: int | None, half_home: int | None = None, half_away: int | None = None) -> int:
    """Resolve pending rows after a real fixture result is known."""
    if not _enabled() or home_score is None or away_score is None:
        return 0
    actual_ft = "1" if home_score > away_score else "X" if home_score == away_score else "2"
    actual_over = "over_2_5" if home_score + away_score > 2.5 else "under_2_5"
    actual_btts = "yes" if home_score > 0 and away_score > 0 else "no"
    actual = {"match_result": actual_ft, "goals": actual_over, "btts": actual_btts}
    resolved = 0
    for kind, value in actual.items():
        params = {"external_match_id": f"eq.{int(external_match_id)}", "prediction_type": f"eq.{kind}", "result_status": "eq.pending", "select": "id,prediction_value"}
        try:
            r = httpx.get(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params=params, timeout=10)
            r.raise_for_status()
            rows = r.json()
            for row in rows:
                status = "correct" if str(row.get("prediction_value")) == value else "incorrect"
                httpx.patch(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params={"id": f"eq.{row['id']}"}, json={"result_status": status, "actual_result": value, "resolved_at": datetime.now(timezone.utc).isoformat()}, timeout=10).raise_for_status()
                resolved += 1
        except Exception:
            continue
    return resolved


def performance_summary(limit: int = 5000) -> dict[str, Any]:
    if not _enabled():
        return {"available": False, "reason": "Supabase tracking is not configured"}
    try:
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params={"result_status": "in.(correct,incorrect)", "select": "prediction_type,result_status,confidence", "limit": int(limit)}, timeout=15)
        r.raise_for_status()
        rows = r.json()
    except Exception:
        return {"available": False, "reason": "tracking query failed"}
    by_type: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = str(row.get("prediction_type") or "unknown")
        bucket = by_type.setdefault(kind, {"total": 0, "correct": 0, "incorrect": 0, "brier_sum": 0.0})
        bucket["total"] += 1
        if row.get("result_status") == "correct": bucket["correct"] += 1
        else: bucket["incorrect"] += 1
        p = float(row.get("confidence") or 0) / 100.0
        y = 1.0 if row.get("result_status") == "correct" else 0.0
        bucket["brier_sum"] += (p - y) ** 2
    for bucket in by_type.values():
        n = bucket["total"] or 1
        bucket["accuracy"] = round(bucket["correct"] / n * 100, 2)
        bucket["brier_score"] = round(bucket["brier_sum"] / n, 4)
        del bucket["brier_sum"]
    total = sum(v["total"] for v in by_type.values())
    correct = sum(v["correct"] for v in by_type.values())
    return {"available": True, "sample": total, "accuracy": round(correct / total * 100, 2) if total else None, "by_type": by_type}
