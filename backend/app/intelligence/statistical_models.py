"""
Statistical Modeling Engine (spec section 7).

Implements multiple, independent statistical models and combines them via
a weighted ensemble -- per the Golden Rule, no single model is allowed to
be the final word:

  1. Poisson / Dixon-Coles goal model (primary quantitative model)
  2. Elo-style rating-differential model (captures overall strength gap)
  3. Recent-form-ratio model (captures short-term momentum)

All three independently produce 1X2 probabilities; `ensemble_1x2` blends
them using configurable weights and reports model agreement so the
Confidence Engine can penalize predictions where the models disagree.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.config import Settings
from app.schemas.prediction import (
    ExpectedGoals,
    HalfTimeFullTimeProbabilities,
    ModelContribution,
    OneXTwoProbabilities,
    OverUnderLine,
    ScoreProbability,
)
from app.schemas.team import TeamStrengthProfile

HOME_ADVANTAGE_MULTIPLIER = 1.12
LEAGUE_AVERAGE_GOALS_PER_MATCH = 1.35
FIRST_HALF_GOAL_SHARE = 0.44  # widely-observed tendency for more 2nd-half goals


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def compute_expected_goals(
    home: TeamStrengthProfile, away: TeamStrengthProfile
) -> ExpectedGoals:
    """
    Expected goals derived from attack/defense ratings (already centered
    around ~0.5 = league average by the Team Strength Engine), scaled back
    into goal units around the league average.
    """
    home_attack = home.home_strength if home.home_strength is not None else home.attack
    away_attack = away.away_strength if away.away_strength is not None else away.attack

    home_xg = (
        LEAGUE_AVERAGE_GOALS_PER_MATCH
        * (home_attack * 2)
        * (1 - (away.defense - 0.5))
        * HOME_ADVANTAGE_MULTIPLIER
    )
    away_xg = (
        LEAGUE_AVERAGE_GOALS_PER_MATCH
        * (away_attack * 2)
        * (1 - (home.defense - 0.5))
    )
    home_xg = max(0.15, round(home_xg, 3))
    away_xg = max(0.15, round(away_xg, 3))
    return ExpectedGoals(home_xg=home_xg, away_xg=away_xg, total_xg=round(home_xg + away_xg, 3))


def dixon_coles_tau(home_goals: int, away_goals: int, lam_home: float, lam_away: float, rho: float) -> float:
    """Low-score correlation adjustment from Dixon & Coles (1997)."""
    if home_goals == 0 and away_goals == 0:
        return 1 - (lam_home * lam_away * rho)
    if home_goals == 0 and away_goals == 1:
        return 1 + (lam_home * rho)
    if home_goals == 1 and away_goals == 0:
        return 1 + (lam_away * rho)
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def build_score_matrix(
    lam_home: float, lam_away: float, max_goals: int, rho: float
) -> list[ScoreProbability]:
    raw: list[tuple[int, int, float]] = []
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = _poisson_pmf(h, lam_home) * _poisson_pmf(a, lam_away)
            p *= dixon_coles_tau(h, a, lam_home, lam_away, rho)
            raw.append((h, a, max(p, 0.0)))
    total = sum(p for _, _, p in raw) or 1.0
    normalized = [round(p / total, 6) for _, _, p in raw]
    # Keep the externally exposed matrix exactly normalized despite six-decimal
    # serialization. The correction is only rounding drift, not model output.
    drift = round(1.0 - sum(normalized), 6)
    if normalized:
        normalized[-1] = round(normalized[-1] + drift, 6)
    return [
        ScoreProbability(home_goals=h, away_goals=a, probability=probability)
        for (h, a, _), probability in zip(raw, normalized)
    ]


def one_x_two_from_matrix(matrix: list[ScoreProbability]) -> OneXTwoProbabilities:
    home = sum(s.probability for s in matrix if s.home_goals > s.away_goals)
    draw = sum(s.probability for s in matrix if s.home_goals == s.away_goals)
    away = sum(s.probability for s in matrix if s.home_goals < s.away_goals)
    total = home + draw + away or 1.0
    return OneXTwoProbabilities(
        home_win=round(home / total, 4), draw=round(draw / total, 4), away_win=round(away / total, 4)
    )


def btts_probability(matrix: list[ScoreProbability]) -> float:
    return round(sum(s.probability for s in matrix if s.home_goals > 0 and s.away_goals > 0), 4)


def over_under_lines(matrix: list[ScoreProbability], lines: tuple[float, ...] = (1.5, 2.5, 3.5)) -> list[OverUnderLine]:
    results = []
    for line in lines:
        over = sum(s.probability for s in matrix if (s.home_goals + s.away_goals) > line)
        under = 1 - over
        results.append(OverUnderLine(line=line, over=round(over, 4), under=round(under, 4)))
    return results


def half_time_full_time(
    lam_home: float, lam_away: float, max_goals: int, rho: float
) -> tuple[OneXTwoProbabilities, HalfTimeFullTimeProbabilities]:
    """
    Approximate HT and HT/FT probabilities by scaling full-time expected
    goals down to a first-half share (a standard, documented
    simplification -- true minute-by-minute modeling would need
    in-match event data this system does not fabricate).
    """
    ht_lam_home = lam_home * FIRST_HALF_GOAL_SHARE
    ht_lam_away = lam_away * FIRST_HALF_GOAL_SHARE
    ht_matrix = build_score_matrix(ht_lam_home, ht_lam_away, max_goals=6, rho=rho)
    ht_1x2 = one_x_two_from_matrix(ht_matrix)

    ft_matrix = build_score_matrix(lam_home, lam_away, max_goals=max_goals, rho=rho)
    ft_1x2 = one_x_two_from_matrix(ft_matrix)

    # Treat 2nd-half outcome as conditionally independent of 1st half given
    # the same underlying team strengths (documented simplification).
    labels = ["1", "X", "2"]
    ht_probs = [ht_1x2.home_win, ht_1x2.draw, ht_1x2.away_win]
    ft_probs = [ft_1x2.home_win, ft_1x2.draw, ft_1x2.away_win]
    matrix: dict[str, float] = {}
    for i, ht_label in enumerate(labels):
        for j, ft_label in enumerate(labels):
            matrix[f"{ht_label}/{ft_label}"] = round(ht_probs[i] * ft_probs[j], 5)
    # Renormalize for rounding drift
    total = sum(matrix.values()) or 1.0
    matrix = {k: round(v / total, 5) for k, v in matrix.items()}
    return ht_1x2, HalfTimeFullTimeProbabilities(matrix=matrix)


def elo_style_probabilities(home: TeamStrengthProfile, away: TeamStrengthProfile) -> OneXTwoProbabilities:
    """
    Converts the Team Strength Engine's 0..1 opponent-adjusted rating into
    an Elo-like rating differential and applies a logistic win function,
    with an explicit draw allowance.
    """
    home_rating = 1500 + (home.opponent_adjusted - 0.5) * 800
    away_rating = 1500 + (away.opponent_adjusted - 0.5) * 800
    home_rating += 60  # home advantage in Elo points

    diff = home_rating - away_rating
    home_win_raw = 1 / (1 + 10 ** (-diff / 400))

    # Empirically, draws are more likely when teams are close in strength.
    closeness = 1 - min(1.0, abs(diff) / 400)
    draw_prob = 0.22 + 0.10 * closeness
    remaining = 1 - draw_prob
    home_win = remaining * home_win_raw
    away_win = remaining * (1 - home_win_raw)

    total = home_win + draw_prob + away_win
    return OneXTwoProbabilities(
        home_win=round(home_win / total, 4),
        draw=round(draw_prob / total, 4),
        away_win=round(away_win / total, 4),
    )


def form_based_probabilities(home: TeamStrengthProfile, away: TeamStrengthProfile) -> OneXTwoProbabilities:
    """Simple short-term momentum model based on recent form scores."""
    home_form = home.form + 0.08  # home boost
    away_form = away.form
    total_form = home_form + away_form
    if total_form == 0:
        home_share, away_share = 0.5, 0.5
    else:
        home_share = home_form / total_form
        away_share = away_form / total_form

    draw_prob = 0.24
    remaining = 1 - draw_prob
    home_win = remaining * home_share
    away_win = remaining * away_share
    total = home_win + draw_prob + away_win
    return OneXTwoProbabilities(
        home_win=round(home_win / total, 4),
        draw=round(draw_prob / total, 4),
        away_win=round(away_win / total, 4),
    )


@dataclass
class EnsembleResult:
    ensemble_1x2: OneXTwoProbabilities
    contributions: list[ModelContribution] = field(default_factory=list)
    model_agreement: float = 1.0


def _euclidean_distance(a: OneXTwoProbabilities, b: OneXTwoProbabilities) -> float:
    return math.sqrt(
        (a.home_win - b.home_win) ** 2 + (a.draw - b.draw) ** 2 + (a.away_win - b.away_win) ** 2
    )


def build_ensemble(
    settings: Settings,
    poisson_1x2: OneXTwoProbabilities,
    elo_1x2: OneXTwoProbabilities,
    form_1x2: OneXTwoProbabilities,
) -> EnsembleResult:
    weights = {
        "poisson_dixon_coles": settings.model_weight_poisson_dixon_coles,
        "elo_strength": settings.model_weight_elo_strength,
        "form_based": settings.model_weight_form_based,
    }
    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}

    models = {
        "poisson_dixon_coles": poisson_1x2,
        "elo_strength": elo_1x2,
        "form_based": form_1x2,
    }

    home = sum(models[k].home_win * w for k, w in weights.items())
    draw = sum(models[k].draw * w for k, w in weights.items())
    away = sum(models[k].away_win * w for k, w in weights.items())
    total = home + draw + away or 1.0
    ensemble = OneXTwoProbabilities(
        home_win=round(home / total, 4), draw=round(draw / total, 4), away_win=round(away / total, 4)
    )

    contributions = [
        ModelContribution(model_name=name, weight=round(w, 3), one_x_two=models[name])
        for name, w in weights.items()
    ]

    # Model agreement: 1 minus the average pairwise Euclidean distance,
    # normalized so 1.0 = perfect agreement, 0.0 = maximal disagreement.
    pairs = [
        (models["poisson_dixon_coles"], models["elo_strength"]),
        (models["poisson_dixon_coles"], models["form_based"]),
        (models["elo_strength"], models["form_based"]),
    ]
    max_dist = math.sqrt(2)  # theoretical max distance between two simplex points
    avg_dist = sum(_euclidean_distance(a, b) for a, b in pairs) / len(pairs)
    agreement = max(0.0, 1 - (avg_dist / max_dist) * 2.5)  # scaled to be discriminative
    agreement = min(1.0, agreement)

    return EnsembleResult(ensemble_1x2=ensemble, contributions=contributions, model_agreement=round(agreement, 4))
