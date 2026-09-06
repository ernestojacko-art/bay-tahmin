"""
Scenario Engine (spec section 7 / 8).

Turns raw probabilities into named, human-readable match scenarios:
favorite, balanced, and upset -- each carrying its own expected-goals
projection and tempo read, rather than collapsing everything into a
single predicted score.
"""
from __future__ import annotations

from app.schemas.prediction import ExpectedGoals, MatchScenario, OneXTwoProbabilities


def _tempo_label(total_xg: float) -> str:
    if total_xg < 2.1:
        return "low-scoring"
    if total_xg > 3.0:
        return "high-scoring"
    return "balanced"


def build_scenarios(
    one_x_two: OneXTwoProbabilities,
    expected_goals: ExpectedGoals,
    home_team_name: str,
    away_team_name: str,
) -> list[MatchScenario]:
    scenarios: list[MatchScenario] = []
    tempo = _tempo_label(expected_goals.total_xg)

    outcomes = [
        ("home_win", one_x_two.home_win, home_team_name, away_team_name, True),
        ("draw", one_x_two.draw, home_team_name, away_team_name, None),
        ("away_win", one_x_two.away_win, away_team_name, home_team_name, False),
    ]
    outcomes_sorted = sorted(outcomes, key=lambda o: o[1], reverse=True)

    favorite_key, favorite_prob, fav_team, other_team, fav_is_home = outcomes_sorted[0]
    if favorite_key == "draw":
        favorite_desc = (
            f"Statistically the most likely single outcome is a draw between "
            f"{home_team_name} and {away_team_name}."
        )
        favorite_label = "Draw favorite"
    else:
        favorite_desc = f"{fav_team} is the model's favorite to win this fixture."
        favorite_label = f"{fav_team} favored"
    scenarios.append(
        MatchScenario(
            scenario_type="favorite",
            label=favorite_label,
            description=favorite_desc,
            probability=favorite_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    # Balanced scenario: whichever of the two non-favorite outcomes is closest
    # in probability to the favorite (captures "could easily go either way").
    remaining = outcomes_sorted[1:]
    remaining_sorted_by_closeness = sorted(remaining, key=lambda o: abs(o[1] - favorite_prob))
    balanced_key, balanced_prob, b_team, b_other, _ = remaining_sorted_by_closeness[0]
    if balanced_key == "draw":
        balanced_desc = "A draw is a live, competitive alternative outcome."
        balanced_label = "Draw is live"
    else:
        balanced_desc = f"{b_team} winning is a realistic, competitive alternative."
        balanced_label = f"{b_team} competitive"
    scenarios.append(
        MatchScenario(
            scenario_type="balanced",
            label=balanced_label,
            description=balanced_desc,
            probability=balanced_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    # Upset scenario: the least-likely outcome, framed explicitly as a surprise.
    upset_key, upset_prob, u_team, u_other, _ = outcomes_sorted[-1]
    if upset_key == "draw":
        upset_desc = "The lower-probability surprise path here is a draw against the run of form."
        upset_label = "Surprise draw"
    else:
        upset_desc = f"The surprise path is {u_team} taking the win against the odds."
        upset_label = f"{u_team} upset"
    scenarios.append(
        MatchScenario(
            scenario_type="upset",
            label=upset_label,
            description=upset_desc,
            probability=upset_prob,
            expected_goals=expected_goals,
            tempo=tempo,
        )
    )

    return scenarios
