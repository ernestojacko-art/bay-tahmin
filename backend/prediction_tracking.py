"""Prediction tracking for the BAY TAHMIN Intelligence Engine.
Writes are best-effort: prediction generation must never fail because tracking is unavailable.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")


def _headers() -> dict[str, str]:
    return {"apikey": SUPABASE_KEY or "", "Authorization": f"Bearer {SUPABASE_KEY or ''}", "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}


def _key(match_id: Any, prediction_type: str, prediction_value: str, model_version: str) -> str:
    return f"{match_id}:{prediction_type}:{prediction_value}:{model_version}"


def _enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _prediction_row(external_id, kickoff, match, home, away, kind, value, confidence, version, source, model):
    return {"external_match_id": int(external_id), "kickoff_at": kickoff, "competition_name": match.get("League"), "home_team": home, "away_team": away, "prediction_type": kind, "prediction_value": value, "confidence": float(confidence), "model_version": version, "source": source, "predicted_at": datetime.now(timezone.utc).isoformat(), "result_status": "pending", "prediction_key": _key(external_id, kind, value, version), "raw_prediction": model}


def track_predictions(match: dict[str, Any], model: dict[str, Any], *, source: str = "BAY TAHMIN FOOTBALL INTELLIGENCE ENGINE") -> int:
    """Persist pre-match categorical predictions, including the complete HT/FT matrix."""
    if not _enabled(): return 0
    external_id = match.get("MatchID") or match.get("matchID") or match.get("id")
    kickoff = match.get("KickoffUTC") or match.get("kickoff") or match.get("Date")
    teams = str(match.get("Teams") or "")
    if not external_id or not kickoff or " - " not in teams: return 0
    home, away = [x.strip() for x in teams.split(" - ", 1)]
    version = str(model.get("engine_version") or model.get("version") or "1.1.2")
    probs = model.get("probabilities") or {}
    predictions: list[dict[str, Any]] = []
    markets = (("match_result", {k: probs.get(k) for k in ("1", "X", "2")}), ("goals", {"over_2_5": probs.get("over_2_5"), "under_2_5": probs.get("under_2_5")}), ("btts", {"yes": probs.get("btts_yes"), "no": probs.get("btts_no")}))
    for kind, values in markets:
        for value, confidence in values.items():
            if confidence is not None: predictions.append(_prediction_row(external_id, kickoff, match, home, away, kind, str(value), confidence, version, source, model))
    for value, confidence in ((model.get("iyms") or {}).get("probabilities") or {}).items():
        if confidence is not None: predictions.append(_prediction_row(external_id, kickoff, match, home, away, "htft", str(value), confidence, version, source, model))
    if not predictions: return 0
    try:
        r = httpx.post(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), json=predictions, timeout=10)
        r.raise_for_status()
        return len(predictions)
    except Exception:
        return 0


def resolve_fixture(external_match_id: int, home_score: int | None, away_score: int | None, half_home: int | None = None, half_away: int | None = None) -> int:
    """Resolve pending 1X2, O/U, BTTS and HT/FT rows after the real result is known."""
    if not _enabled() or home_score is None or away_score is None: return 0
    actual_ft = "1" if home_score > away_score else "X" if home_score == away_score else "2"
    actual = {"match_result": actual_ft, "goals": "over_2_5" if home_score + away_score > 2.5 else "under_2_5", "btts": "yes" if home_score > 0 and away_score > 0 else "no"}
    if half_home is not None and half_away is not None:
        actual["htft"] = f"{'1' if half_home > half_away else 'X' if half_home == half_away else '2'}/{actual_ft}"
    resolved = 0
    for kind, value in actual.items():
        params = {"external_match_id": f"eq.{int(external_match_id)}", "prediction_type": f"eq.{kind}", "result_status": "eq.pending", "select": "id,prediction_value"}
        try:
            r = httpx.get(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params=params, timeout=10); r.raise_for_status()
            for row in r.json():
                status = "correct" if str(row.get("prediction_value")) == value else "incorrect"
                httpx.patch(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params={"id": f"eq.{row['id']}"}, json={"result_status": status, "actual_result": value, "resolved_at": datetime.now(timezone.utc).isoformat()}, timeout=10).raise_for_status()
                resolved += 1
        except Exception:
            continue
    return resolved


def performance_summary(limit: int = 5000) -> dict[str, Any]:
    """Return categorical accuracy and multiclass Brier scores by market type."""
    if not _enabled(): return {"available": False, "reason": "Supabase tracking is not configured"}
    try:
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/prediction_tracking", headers=_headers(), params={"result_status": "in.(correct,incorrect)", "select": "external_match_id,prediction_type,prediction_value,model_version,result_status,confidence", "limit": int(limit)}, timeout=15); r.raise_for_status(); rows = r.json()
    except Exception:
        return {"available": False, "reason": "tracking query failed"}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("external_match_id")), str(row.get("prediction_type") or "unknown"), str(row.get("model_version") or "unknown"))].append(row)
    by_type: dict[str, dict[str, Any]] = {}
    for (_match_id, kind, _version), group in groups.items():
        probs: dict[str, float] = {}; actual = None
        for row in group:
            try: probs[str(row.get("prediction_value"))] = max(0.0, min(1.0, float(row.get("confidence") or 0) / 100.0))
            except (TypeError, ValueError): continue
            if row.get("result_status") == "correct": actual = str(row.get("prediction_value"))
        if not probs or actual is None: continue
        bucket = by_type.setdefault(kind, {"total": 0, "correct": 0, "incorrect": 0, "brier_sum": 0.0})
        bucket["total"] += 1
        predicted = max(probs, key=probs.get)
        if predicted == actual: bucket["correct"] += 1
        else: bucket["incorrect"] += 1
        bucket["brier_sum"] += sum((p - (1.0 if value == actual else 0.0)) ** 2 for value, p in probs.items())
    for bucket in by_type.values():
        n = bucket["total"] or 1
        bucket["accuracy"] = round(bucket["correct"] / n * 100, 2)
        bucket["brier_score"] = round(bucket["brier_sum"] / n, 4)
        del bucket["brier_sum"]
    total = sum(v["total"] for v in by_type.values()); correct = sum(v["correct"] for v in by_type.values())
    return {"available": True, "sample": total, "accuracy": round(correct / total * 100, 2) if total else None, "by_type": by_type, "scoring": {"accuracy": "highest_probability_class", "brier": "multiclass_sum_squared_error"}}
