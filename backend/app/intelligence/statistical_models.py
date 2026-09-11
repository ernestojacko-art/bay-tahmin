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
    AsianHandicapLine, DoubleChance, DrawNoBet, ExpectedGoals,
    HalfTimeFullTimeProbabilities, ModelContribution, OneXTwoProbabilities,
    OverUnderLine, ScoreProbability,
)
from app.schemas.team import TeamStrengthProfile

HOME_ADVANTAGE_MULTIPLIER = 1.12
LEAGUE_AVERAGE_GOALS_PER_MATCH = 1.35
FIRST_HALF_GOAL_SHARE = 0.44


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def compute_expected_goals(home: TeamStrengthProfile, away: TeamStrengthProfile) -> ExpectedGoals:
    home_attack = home.home_strength if home.home_strength is not None else home.attack
    away_attack = away.away_strength if away.away_strength is not None else away.attack
    home_xg = LEAGUE_AVERAGE_GOALS_PER_MATCH * (home_attack * 2) * (1 - (away.defense - 0.5)) * HOME_ADVANTAGE_MULTIPLIER
    away_xg = LEAGUE_AVERAGE_GOALS_PER_MATCH * (away_attack * 2) * (1 - (home.defense - 0.5))
    home_xg = max(0.15, round(home_xg, 3))
    away_xg = max(0.15, round(away_xg, 3))
    return ExpectedGoals(home_xg=home_xg, away_xg=away_xg, total_xg=round(home_xg + away_xg, 3))


def dixon_coles_tau(home_goals: int, away_goals: int, lam_home: float, lam_away: float, rho: float) -> float:
    if home_goals == 0 and away_goals == 0:
        return 1 - (lam_home * lam_away * rho)
    if home_goals == 0 and away_goals == 1:
        return 1 + (lam_home * rho)
    if home_goals == 1 and away_goals == 0:
        return 1 + (lam_away * rho)
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def build_score_matrix(lam_home: float, lam_away: float, max_goals: int, rho: float) -> list[ScoreProbability]:
    raw: list[tuple[int, int, float]] = []
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = _poisson_pmf(h, lam_home) * _poisson_pmf(a, lam_away)
            p *= dixon_coles_tau(h, a, lam_home, lam_away, rho)
            raw.append((h, a, max(p, 0.0)))
    total = sum(p for _, _, p in raw) or 1.0
    normalized = [round(p / total, 6) for _, _, p in raw]
    drift = round(1.0 - sum(normalized), 6)
    if normalized:
        normalized[-1] = round(normalized[-1] + drift, 6)
    return [ScoreProbability(home_goals=h, away_goals=a, probability=probability) for (h, a, _), probability in zip(raw, normalized)]


def one_x_two_from_matrix(matrix: list[ScoreProbability]) -> OneXTwoProbabilities:
    home = sum(s.probability for s in matrix if s.home_goals > s.away_goals)
    draw = sum(s.probability for s in matrix if s.home_goals == s.away_goals)
    away = sum(s.probability for s in matrix if s.home_goals < s.away_goals)
    total = home + draw + away or 1.0
    return OneXTwoProbabilities(home_win=round(home / total, 4), draw=round(draw / total, 4), away_win=round(away / total, 4))


def btts_probability(matrix: list[ScoreProbability]) -> float:
    return round(sum(s.probability for s in matrix if s.home_goals > 0 and s.away_goals > 0), 4)


def over_under_lines(matrix: list[ScoreProbability], lines: tuple[float, ...] = (1.5, 2.5, 3.5)) -> list[OverUnderLine]:
    results = []
    for line in lines:
        over = sum(s.probability for s in matrix if (s.home_goals + s.away_goals) > line)
        under = 1 - over
        results.append(OverUnderLine(line=line, over=round(over, 4), under=round(under, 4)))
    return results


def double_chance_from_1x2(ox: OneXTwoProbabilities) -> DoubleChance:
    return DoubleChance(home_or_draw=round(ox.home_win + ox.draw, 4), draw_or_away=round(ox.draw + ox.away_win, 4), home_or_away=round(ox.home_win + ox.away_win, 4))


def draw_no_bet_from_1x2(ox: OneXTwoProbabilities) -> DrawNoBet:
    total = ox.home_win + ox.away_win
    if total <= 0:
        return DrawNoBet(home=0.5, away=0.5)
    return DrawNoBet(home=round(ox.home_win / total, 4), away=round(ox.away_win / total, 4))


def asian_handicap_lines(matrix: list[ScoreProbability], lines: tuple[float, ...] = (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)) -> list[AsianHandicapLine]:
    results: list[AsianHandicapLine] = []
    for line in lines:
        home_cover = 0.0; away_cover = 0.0; push = 0.0
        for s in matrix:
            diff = (s.home_goals + line) - s.away_goals
            if diff > 1e-9: home_cover += s.probability
            elif diff < -1e-9: away_cover += s.probability
            else: push += s.probability
        results.append(AsianHandicapLine(line=line, home_cover=round(home_cover, 4), away_cover=round(away_cover, 4), push=round(push, 4)))
    return results


def half_time_full_time(lam_home: float, lam_away: float, max_goals: int, rho: float) -> tuple[OneXTwoProbabilities, HalfTimeFullTimeProbabilities]:
    ht_lam_home = lam_home * FIRST_HALF_GOAL_SHARE
    ht_lam_away = lam_away * FIRST_HALF_GOAL_SHARE
    ht_matrix = build_score_matrix(ht_lam_home, ht_lam_away, max_goals=6, rho=rho)
    ht_1x2 = one_x_two_from_matrix(ht_matrix)
    ft_matrix = build_score_matrix(lam_home, lam_away, max_goals=max_goals, rho=rho)
    ft_1x2 = one_x_two_from_matrix(ft_matrix)
    labels = ["1", "X", "2"]
    ht_probs = [ht_1x2.home_win, ht_1x2.draw, ht_1x2.away_win]
    ft_probs = [ft_1x2.home_win, ft_1x2.draw, ft_1x2.away_win]
    matrix: dict[str, float] = {}
    for i, ht_label in enumerate(labels):
        for j, ft_label in enumerate(labels):
            matrix[f"{ht_label}/{ft_label}"] = round(ht_probs[i] * ft_probs[j], 5)
    total = sum(matrix.values()) or 1.0
    matrix = {k: round(v / total, 5) for k, v in matrix.items()}
    return ht_1x2, HalfTimeFullTimeProbabilities(matrix=matrix)


def elo_style_probabilities(home: TeamStrengthProfile, away: TeamStrengthProfile) -> OneXTwoProbabilities:
    home_rating = 1500 + (home.opponent_adjusted - 0.5) * 800
    away_rating = 1500 + (away.opponent_adjusted - 0.5) * 800
    home_rating += 60
    diff = home_rating - away_rating
    home_win_raw = 1 / (1 + 10 ** (-diff / 400))
    closeness = 1 - min(1.0, abs(diff) / 400)
    draw_prob = 0.22 + 0.10 * closeness
    remaining = 1 - draw_prob
    home_win = remaining * home_win_raw
    away_win = remaining * (1 - home_win_raw)
    total = home_win + draw_prob + away_win
    return OneXTwoProbabilities(home_win=round(home_win / total, 4), draw=round(draw_prob / total, 4), away_win=round(away_win / total, 4))


def form_based_probabilities(home: TeamStrengthProfile, away: TeamStrengthProfile) -> OneXTwoProbabilities:
    home_form = home.form + 0.08
    away_form = away.form
    total_form = home_form + away_form
    if total_form == 0:
        home_share, away_share = 0.5, 0.5
    else:
        home_share = home_form / total_form; away_share = away_form / total_form
    draw_prob = 0.24
    remaining = 1 - draw_prob
    home_win = remaining * home_share; away_win = remaining * away_share
    total = home_win + draw_prob + away_win
    return OneXTwoProbabilities(home_win=round(home_win / total, 4), draw=round(draw_prob / total, 4), away_win=round(away_win / total, 4))


@dataclass
class EnsembleResult:
    ensemble_1x2: OneXTwoProbabilities
    contributions: list[ModelContribution] = field(default_factory=list)
    model_agreement: float = 1.0


def _euclidean_distance(a: OneXTwoProbabilities, b: OneXTwoProbabilities) -> float:
    return math.sqrt((a.home_win - b.home_win) ** 2 + (a.draw - b.draw) ** 2 + (a.away_win - b.away_win) ** 2)


def build_ensemble(settings: Settings, poisson_1x2: OneXTwoProbabilities, elo_1x2: OneXTwoProbabilities, form_1x2: OneXTwoProbabilities) -> EnsembleResult:
    weights = {"poisson_dixon_coles": settings.model_weight_poisson_dixon_coles, "elo_strength": settings.model_weight_elo_strength, "form_based": settings.model_weight_form_based}
    total_w = sum(weights.values()) or 1.0
    weights = {k: v / total_w for k, v in weights.items()}
    models = {"poisson_dixon_coles": poisson_1x2, "elo_strength": elo_1x2, "form_based": form_1x2}
    home = sum(models[k].home_win * w for k, w in weights.items())
    draw = sum(models[k].draw * w for k, w in weights.items())
    away = sum(models[k].away_win * w for k, w in weights.items())
    total = home + draw + away or 1.0
    ensemble = OneXTwoProbabilities(home_win=round(home / total, 4), draw=round(draw / total, 4), away_win=round(away / total, 4))
    contributions = [ModelContribution(model_name=name, weight=round(w, 3), one_x_two=models[name]) for name, w in weights.items()]
    pairs = [(models["poisson_dixon_coles"], models["elo_strength"]), (models["poisson_dixon_coles"], models["form_based"]), (models["elo_strength"], models["form_based"])]
    max_dist = math.sqrt(2)
    avg_dist = sum(_euclidean_distance(a, b) for a, b in pairs) / len(pairs)
    agreement = max(0.0, 1 - (avg_dist / max_dist) * 2.5)
    agreement = min(1.0, agreement)
    return EnsembleResult(ensemble_1x2=ensemble, contributions=contributions, model_agreement=round(agreement, 4))
