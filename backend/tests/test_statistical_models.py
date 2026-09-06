from __future__ import annotations

from app.intelligence import statistical_models as stats
from app.schemas.common import DataQuality
from app.schemas.team import TeamStrengthProfile


def _profile(name: str, overall: float, attack: float, defense: float, form: float) -> TeamStrengthProfile:
    return TeamStrengthProfile(
        team_id=name,
        team_name=name,
        overall=overall,
        attack=attack,
        defense=defense,
        form=form,
        home_strength=overall,
        away_strength=overall,
        opponent_adjusted=overall,
        matches_considered=15,
        data_quality=DataQuality.HIGH,
        weights_used={},
        notes=[],
    )


def test_expected_goals_are_positive():
    home = _profile("Home", 0.65, 0.7, 0.6, 0.6)
    away = _profile("Away", 0.45, 0.5, 0.5, 0.4)
    eg = stats.compute_expected_goals(home, away)
    assert eg.home_xg > 0
    assert eg.away_xg > 0
    assert eg.total_xg == round(eg.home_xg + eg.away_xg, 3)


def test_score_matrix_probabilities_sum_to_one():
    matrix = stats.build_score_matrix(1.4, 1.1, max_goals=8, rho=-0.13)
    total = sum(s.probability for s in matrix)
    assert abs(total - 1.0) < 1e-6


def test_one_x_two_sums_to_one():
    matrix = stats.build_score_matrix(1.6, 1.0, max_goals=8, rho=-0.13)
    ox = stats.one_x_two_from_matrix(matrix)
    assert abs((ox.home_win + ox.draw + ox.away_win) - 1.0) < 1e-4


def test_stronger_home_team_is_favored():
    strong = _profile("Strong", 0.8, 0.85, 0.75, 0.75)
    weak = _profile("Weak", 0.3, 0.25, 0.35, 0.3)
    eg = stats.compute_expected_goals(strong, weak)
    matrix = stats.build_score_matrix(eg.home_xg, eg.away_xg, max_goals=8, rho=-0.13)
    ox = stats.one_x_two_from_matrix(matrix)
    assert ox.home_win > ox.away_win


def test_ensemble_weights_normalize_and_sum_to_one():
    from app.core.config import get_settings

    settings = get_settings()
    home = _profile("Home", 0.65, 0.7, 0.6, 0.6)
    away = _profile("Away", 0.45, 0.5, 0.5, 0.4)
    eg = stats.compute_expected_goals(home, away)
    matrix = stats.build_score_matrix(eg.home_xg, eg.away_xg, max_goals=8, rho=-0.13)
    poisson_1x2 = stats.one_x_two_from_matrix(matrix)
    elo_1x2 = stats.elo_style_probabilities(home, away)
    form_1x2 = stats.form_based_probabilities(home, away)

    ensemble = stats.build_ensemble(settings, poisson_1x2, elo_1x2, form_1x2)
    total_weight = sum(c.weight for c in ensemble.contributions)
    assert abs(total_weight - 1.0) < 1e-6
    assert 0.0 <= ensemble.model_agreement <= 1.0
    total_prob = ensemble.ensemble_1x2.home_win + ensemble.ensemble_1x2.draw + ensemble.ensemble_1x2.away_win
    assert abs(total_prob - 1.0) < 1e-3


def test_half_time_full_time_matrix_sums_to_one():
    ht_1x2, htft = stats.half_time_full_time(1.5, 1.1, max_goals=8, rho=-0.13)
    assert abs(sum(htft.matrix.values()) - 1.0) < 1e-3
    assert len(htft.matrix) == 9
