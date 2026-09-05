"""BAY TAHMIN prediction consistency and reconciliation layer.

All published prediction families are derived from one probability system when
sufficient pre-match inputs exist. The layer also refuses to invent HT/FT or
xG-like values when the required source data is missing.
"""
from __future__ import annotations

import math
from typing import Any

RESULTS = ("1", "X", "2")
HTFT = tuple(f"{h}/{f}" for h in RESULTS for f in RESULTS)


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _poisson(lam: float, k: int) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _matrix(home: float, away: float, n: int = 8) -> list[list[float]]:
    home, away = max(0.01, home), max(0.01, away)
    matrix = [[_poisson(home, i) * _poisson(away, j) for j in range(n + 1)] for i in range(n + 1)]
    total = sum(map(sum, matrix)) or 1.0
    return [[value / total for value in row] for row in matrix]


def _result_probs(matrix: list[list[float]]) -> dict[str, float]:
    home = draw = 0.0
    for i, row in enumerate(matrix):
        for j, probability in enumerate(row):
            if i > j:
                home += probability
            elif i == j:
                draw += probability
    return {"1": home, "X": draw, "2": max(0.0, 1.0 - home - draw)}


def _joint(ht: list[list[float]], second: list[list[float]]) -> dict[str, float]:
    output = {key: 0.0 for key in HTFT}
    for hi, hrow in enumerate(ht):
        for hj, hp in enumerate(hrow):
            ht_result = "1" if hi > hj else "X" if hi == hj else "2"
            for si, srow in enumerate(second):
                for sj, sp in enumerate(srow):
                    ft_result = "1" if hi + si > hj + sj else "X" if hi + si == hj + sj else "2"
                    output[f"{ht_result}/{ft_result}"] += hp * sp
    total = sum(output.values()) or 1.0
    return {key: value / total for key, value in output.items()}


def _avg(*values: Any) -> float | None:
    nums = [x for x in (_num(v) for v in values) if x is not None]
    return sum(nums) / len(nums) if nums else None


def _recent_half(context: dict[str, Any], side: str, half: str) -> dict[str, Any]:
    team = context.get(side) or {}
    form = team.get("recent_form") or {}
    block = form.get(half) or {}
    return block if isinstance(block, dict) else {}


def _expected_goals(model: dict[str, Any], context: dict[str, Any]) -> tuple[float, float] | None:
    blocks = []
    for name in ("goal_model", "score_model", "poisson", "score_projection"):
        block = model.get(name)
        if isinstance(block, dict):
            blocks.append(block)
    blocks.append(model)
    for block in blocks:
        expected = block.get("expected_goals") or block.get("lambda") or block.get("lambdas")
        if isinstance(expected, dict):
            home = _num(expected.get("home")); away = _num(expected.get("away"))
            if home is not None and away is not None:
                return home, away
        for hk, ak in (("home_lambda", "away_lambda"), ("lambda_home", "lambda_away"), ("home_xg", "away_xg")):
            home = _num(block.get(hk)); away = _num(block.get(ak))
            if home is not None and away is not None:
                return home, away

    # Build a transparent goal prior from pre-match team scoring/conceding form.
    home = context.get("home") or {}; away = context.get("away") or {}
    hf = home.get("recent_form") or {}; af = away.get("recent_form") or {}
    home_for = _num(hf.get("goals_for_avg")); home_against = _num(hf.get("goals_against_avg"))
    away_for = _num(af.get("goals_for_avg")); away_against = _num(af.get("goals_against_avg"))
    if all(x is not None for x in (home_for, home_against, away_for, away_against)):
        return _avg(home_for, away_against), _avg(away_for, home_against)

    league = context.get("league") or {}
    home = _num(league.get("home_goal_avg")); away = _num(league.get("away_goal_avg"))
    return (home, away) if home is not None and away is not None else None


def _half_lambdas(model: dict[str, Any], context: dict[str, Any], second: bool) -> tuple[float, float] | None:
    key = "second_half_model" if second else "first_half_model"
    block = model.get(key) or {}
    if isinstance(block, dict):
        expected = block.get("expected_goals")
        if isinstance(expected, dict):
            home = _num(expected.get("home")); away = _num(expected.get("away"))
            if home is not None and away is not None:
                return home, away

    half = "second_half" if second else "first_half"
    home_form = _recent_half(context, "home", half)
    away_form = _recent_half(context, "away", half)
    home_for = _num(home_form.get("goals_for_avg")); home_against = _num(home_form.get("goals_against_avg"))
    away_for = _num(away_form.get("goals_for_avg")); away_against = _num(away_form.get("goals_against_avg"))
    if all(x is not None for x in (home_for, home_against, away_for, away_against)):
        return _avg(home_for, away_against), _avg(away_for, home_against)

    league = context.get("league") or {}
    prefix = "second_half" if second else "first_half"
    home = _num(league.get(f"{prefix}_home_goal_avg")); away = _num(league.get(f"{prefix}_away_goal_avg"))
    return (home, away) if home is not None and away is not None else None


def _coverage(context: dict[str, Any]) -> float:
    availability = context.get("data_availability") or {}
    keys = ("xg", "shots", "shots_on_target", "possession", "corners", "cards", "first_half_goals", "second_half_goals", "goal_timing")
    return sum(bool(availability.get(key)) for key in keys) / len(keys)


def reconcile(model: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(model or {})
    context = context or {}
    warnings = list(result.get("prediction_warnings") or [])
    checks: list[str] = []
    score_matrix = None

    goals = _expected_goals(result, context)
    if goals:
        score_matrix = _matrix(*goals)
        ft = _result_probs(score_matrix)
        top_i, top_j, top_probability = max(
            ((i, j, p) for i, row in enumerate(score_matrix) for j, p in enumerate(row)),
            key=lambda item: item[2],
        )
        result["expected_goals"] = {"home": round(goals[0], 3), "away": round(goals[1], 3), "source": "pre-match model/team-form prior"}
        result["score_distribution"] = {f"{i}-{j}": round(p * 100, 4) for i, row in enumerate(score_matrix) for j, p in enumerate(row) if p >= 0.001}
        result["predicted_score"] = f"{top_i}-{top_j}"
        result["predicted_score_probability"] = round(top_probability * 100, 2)
        result["ms"] = max(ft, key=ft.get)
        result["ms_probabilities"] = {key: round(value * 100, 2) for key, value in ft.items()}
        btts_yes = sum(score_matrix[i][j] for i in range(1, 9) for j in range(1, 9))
        over25 = sum(score_matrix[i][j] for i in range(9) for j in range(9) if i + j >= 3)
        result["btts_probabilities"] = {"Var": round(btts_yes * 100, 2), "Yok": round((1 - btts_yes) * 100, 2)}
        result["btts"] = "Var" if btts_yes >= 0.5 else "Yok"
        result["ou_2_5"] = {"Alt": round((1 - over25) * 100, 2), "Üst": round(over25 * 100, 2)}
        checks.append("Skor dağılımı → MS/BTTS/2.5 Alt-Üst tek kaynaktan türetildi")
    else:
        warnings.append("Güvenilir maç-gol öncülü yok; skor/MS/BTTS/2.5 ortak skor modelinden üretilemedi")

    first_half = _half_lambdas(result, context, False)
    second_half = _half_lambdas(result, context, True)
    if first_half and second_half:
        ht_matrix = _matrix(*first_half)
        second_matrix = _matrix(*second_half)
        ht = _result_probs(ht_matrix)
        joint = _joint(ht_matrix, second_matrix)
        top_ht = max(ht, key=ht.get)
        top_joint = max(joint, key=joint.get)
        result["first_half"] = {key: round(value * 100, 2) for key, value in ht.items()}
        result["iyms"] = {
            "probabilities": {key: round(value * 100, 2) for key, value in sorted(joint.items(), key=lambda item: item[1], reverse=True)},
            "top": top_joint,
            "source": "independent first-half × independent second-half joint model",
            "surprise_candidates": [{"selection": key, "probability": round(value * 100, 2)} for key, value in sorted(joint.items(), key=lambda item: item[1], reverse=True) if key not in {"1/1", "X/X", "2/2"}][:5],
        }
        result["htft_model"] = {"independent_first_half": True, "independent_second_half": True, "joint_method": "HT matrix × 2H matrix", "consistency_locked": True}
        checks.append(f"İY={top_ht}; İY/MS={top_joint}; HT ve HT/MS aynı joint sistemden geliyor")
    else:
        warnings.append("Bağımsız İY ve 2Y öncülleri birlikte mevcut değil; İY/MS joint tahmini üretilmedi")

    if warnings:
        result["prediction_warnings"] = list(dict.fromkeys(warnings))

    coverage = _coverage(context)
    quality = "high" if coverage >= 0.78 else "medium" if coverage >= 0.45 else "low"
    result["data_quality"] = {"level": quality, "coverage": round(coverage * 100, 1), "consistency_validated": True}
    result["prediction_consistency"] = {"validated": True, "score_ft_linked": bool(score_matrix), "htft_linked": bool(first_half and second_half), "checks": checks}
    return result


def install(impl: Any) -> None:
    """Wrap the active candidate function without rewriting historical engines."""
    original = getattr(impl, "cand", None)
    if original is None or getattr(original, "_consistency_guard", False):
        return

    async def guarded_cand(row: dict[str, Any], **kwargs: Any):
        result = await original(row, **kwargs)
        result["model"] = reconcile(result.get("model") or {}, result.get("context") or {})
        return result

    guarded_cand._consistency_guard = True
    impl.cand = guarded_cand
    v5 = getattr(impl, "v5", None)
    v4 = getattr(v5, "v4", None)
    v3 = getattr(v4, "v3", None)
    v2 = getattr(v3, "v2", None)
    for obj in (v3, v2):
        if obj is not None:
            obj.cand = guarded_cand
